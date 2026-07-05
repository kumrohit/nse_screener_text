"""NSE bhavcopy ingestion — data layer v2 (ROADMAP Item 3).

Runs side-by-side with the existing yfinance-backed store
(`data_ingest.py`); nothing in the evaluator/webapp/cli reads from here
yet. Cutover only happens after ~2 weeks of side-by-side evidence (the
`verify` cross-source consistency check) — see TECHNICAL_DESIGN.md §4a.

Two NSE sources:

1. `sec_bhavdata_full_DDMMYYYY.csv` — the daily "full bhavcopy with
   delivery" file. One file has OHLCV *and* delivery % together for
   every series; we filter to SERIES == "EQ". Confirmed URL and schema
   by fetching a live file (2026-07-03): SYMBOL, SERIES, DATE1,
   PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE,
   CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES,
   DELIV_QTY, DELIV_PER.
2. The corporate-actions API (`/api/corporates-corporateActions`) —
   requires a cookie warm-up GET first (NSE's anti-bot layer); the
   warm-up itself may return 403 yet still set the cookies the
   subsequent API call needs, so its status is not checked.

Prices from both files are **unadjusted** (raw as-traded) — adjustment
for splits/bonuses only (never dividends, documented divergence from
yfinance's `auto_adjust` convention) happens via `adjustment_factors`
below, applied on read.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sys
import time

import pandas as pd
import requests

from . import config

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}
MAX_RETRIES = 3


def _bhav_url(d: dt.date) -> str:
    return (f"https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv")


def fetch_day(d: dt.date) -> pd.DataFrame | None:
    """One day's EQ-series OHLCV + delivery %, unadjusted. `None` if NSE
    has no file for that date (weekend/holiday — not an error)."""
    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(_bhav_url(d), headers=_HEADERS, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"bhavcopy fetch failed for {d}: {exc}") from exc
            wait = 5 * attempt
            print(f"[bhavcopy] {d} fetch failed ({exc}); retry in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)

    raw = pd.read_csv(io.StringIO(resp.text))
    raw.columns = [c.strip() for c in raw.columns]
    # NSE's CSV has a stray leading space after every comma
    raw["SYMBOL"] = raw["SYMBOL"].astype(str).str.strip()
    raw["SERIES"] = raw["SERIES"].astype(str).str.strip()
    raw["DATE1"] = raw["DATE1"].astype(str).str.strip()
    eq = raw[raw["SERIES"] == "EQ"].copy()
    if eq.empty:
        return None
    out = pd.DataFrame({
        "symbol": eq["SYMBOL"],
        "date": pd.to_datetime(eq["DATE1"], format="%d-%b-%Y"),
        "open": pd.to_numeric(eq["OPEN_PRICE"], errors="coerce"),
        "high": pd.to_numeric(eq["HIGH_PRICE"], errors="coerce"),
        "low": pd.to_numeric(eq["LOW_PRICE"], errors="coerce"),
        "close": pd.to_numeric(eq["CLOSE_PRICE"], errors="coerce"),
        "volume": pd.to_numeric(eq["TTL_TRD_QNTY"], errors="coerce"),
        "delivery_pct": pd.to_numeric(eq["DELIV_PER"], errors="coerce"),
    })
    bad = (out[["open", "high", "low", "close"]] <= 0).any(axis=1) \
        | (out["high"] < out["low"])
    return out.loc[~bad].reset_index(drop=True)


def update_bhavcopy_store() -> pd.DataFrame:
    """Fetch any missing business days since the store's last date (or
    the last 10 calendar days on first run) and append. A 404 (weekend
    or holiday) is skipped, not treated as a failure — NSE's calendar is
    the source of truth, not a hardcoded holiday list."""
    if config.BHAVCOPY_STORE.exists():
        store = pd.read_parquet(config.BHAVCOPY_STORE)
        start = store["date"].max().date() + dt.timedelta(days=1)
    else:
        store = pd.DataFrame(columns=[
            "symbol", "date", "open", "high", "low", "close", "volume",
            "delivery_pct"])
        start = dt.date.today() - dt.timedelta(days=10)

    today = dt.date.today()
    frames = []
    d = start
    while d < today:
        day_df = fetch_day(d)
        if day_df is not None:
            frames.append(day_df)
        d += dt.timedelta(days=1)
        time.sleep(1)  # be polite to NSE

    if frames:
        new = pd.concat(frames, ignore_index=True)
        store = (pd.concat([store, new], ignore_index=True)
                .drop_duplicates(subset=["symbol", "date"], keep="last")
                .sort_values(["symbol", "date"]).reset_index(drop=True))
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        store.to_parquet(config.BHAVCOPY_STORE, index=False)
    return store


# ---------------------------------------------------------------- corp actions
CORP_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions"

# Regexes built from real NSE subject-line text (fetched 2026-07-05,
# Jan 2024-Jul 2026 window), not guessed formats:
#   "Bonus 3:1"                                              (X new : Y held)
#   "Face Value Split (Sub-Division) - From Rs10/- Per Share
#    To Re 1/- Per Share"
_BONUS_RE = re.compile(r"\bbonus\s+(\d+)\s*:\s*(\d+)", re.I)
_FACE_VALUE_RE = re.compile(
    r"from\s+r[se]\.?\s*(\d+(?:\.\d+)?)\s*/?-?\s*per\s+share\s+to\s+"
    r"r[se]\.?\s*(\d+(?:\.\d+)?)\s*/?-?\s*per\s+share", re.I)


def parse_adjustment_factor(subject: str) -> float | None:
    """Multiplicative price-adjustment factor implied by a corporate
    action's free-text `subject` line: pre-action prices are multiplied
    by this factor so the series is continuous across the action.
    Returns `None` for anything that isn't a confidently-recognised
    split or bonus — dividends, AGM notices, etc. must never produce a
    factor (fail loud / don't guess, per the project's core principle),
    and so must a split/bonus phrasing NSE describes in a way these two
    patterns don't cover; such rows should be logged and reviewed, not
    silently skipped."""
    s = subject.strip()
    m = _FACE_VALUE_RE.search(s)
    if m:
        old, new = float(m.group(1)), float(m.group(2))
        return round(new / old, 6) if old > 0 else None
    m = _BONUS_RE.search(s)
    if m:
        new_shares, held = int(m.group(1)), int(m.group(2))
        total = new_shares + held
        return round(held / total, 6) if total > 0 else None
    return None


def fetch_corporate_actions(from_date: dt.date, to_date: dt.date
                            ) -> pd.DataFrame:
    """Raw corporate-actions rows (symbol, exDate, subject, ...) for the
    date range. Splits/bonuses only are relevant here; dividends and
    other entries pass through with `factor = NaN` (see
    `parse_adjustment_factor`) rather than being filtered out, so the
    full record is auditable."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS,
                    timeout=20)
    except Exception:  # noqa: BLE001
        pass  # best-effort cookie warm-up; NSE may 403 this yet still
              # set the cookies the API call below needs
    resp = session.get(
        CORP_ACTIONS_URL,
        headers={**_HEADERS, "Accept": "application/json"},
        params={"index": "equities",
                "from_date": from_date.strftime("%d-%m-%Y"),
                "to_date": to_date.strftime("%d-%m-%Y")},
        timeout=30)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    if df.empty:
        return df
    df["ex_date"] = pd.to_datetime(df["exDate"], format="%d-%b-%Y",
                                   errors="coerce")
    df["factor"] = df["subject"].map(parse_adjustment_factor)
    return df[["symbol", "ex_date", "subject", "series", "factor"]]


def build_adjustment_factors(actions: pd.DataFrame) -> pd.DataFrame:
    """Per-(symbol, ex_date) cumulative adjustment factor: every bar
    strictly before `ex_date` gets multiplied by `factor` (and by every
    earlier action's factor too, since adjustments compound going back
    in time — the same convention yfinance's `auto_adjust` uses).
    Rows with `factor` NaN (unparsed or non-split/bonus) are excluded
    from the adjustment but kept in the raw `actions` table for review."""
    acts = actions.dropna(subset=["factor", "ex_date"]).copy()
    if acts.empty:
        return pd.DataFrame(columns=["symbol", "ex_date", "factor",
                                     "cum_factor"])
    acts = acts.sort_values(["symbol", "ex_date"])
    acts["cum_factor"] = (
        acts.groupby("symbol")["factor"]
        .transform(lambda f: f[::-1].cumprod()[::-1]))
    return acts[["symbol", "ex_date", "factor", "cum_factor"]].reset_index(
        drop=True)


def apply_adjustments(prices: pd.DataFrame, adj: pd.DataFrame
                      ) -> pd.DataFrame:
    """Multiply OHLC (not volume, not delivery_pct) by the cumulative
    factor in effect for each bar's date — every bar strictly before an
    action's ex_date is scaled by that action's (and all later actions')
    factor.

    Each action's `date < ex_date` mask is a superset of every *later*
    action's mask (an earlier ex_date is a narrower cutoff), so actions
    must be applied latest-ex_date-first: the broad (small, single-
    action) factor gets written first, then progressively narrower
    masks correctly overwrite their subset with the larger cumulative
    factor. Applying oldest-first would let a later action's broad
    write clobber an earlier action's correct (larger) value."""
    if adj.empty:
        return prices.copy()
    out = prices.copy()
    out["_adj"] = 1.0
    for sym, g in adj.groupby("symbol"):
        mask = out["symbol"] == sym
        if not mask.any():
            continue
        for _, row in g.sort_values("ex_date", ascending=False).iterrows():
            pre = mask & (out["date"] < row["ex_date"])
            out.loc[pre, "_adj"] = row["cum_factor"]
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * out["_adj"]
    return out.drop(columns="_adj")
