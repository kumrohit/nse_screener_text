"""Post-backfill verification — automates the §10 sanity checklist.

    python -m screener.cli verify

Runs structural, coverage, and indicator-sanity checks against the live
store and prints a PASS/WARN/FAIL report. Exit code 1 on any FAIL, so it
can gate a cron pipeline (backfill && verify && ...).

Checks are pure functions over dataframes so they're testable on synthetic
data without touching disk.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def cross_source_divergence(yf_prices: pd.DataFrame,
                            bhav_prices: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date) close-price divergence between the two data
    sources, for dates present in both — the evidence Item 3's cutover
    decision hinges on. Both closes should be on a comparable basis
    (e.g. both raw/unadjusted, or bhavcopy already run through
    `bhavcopy.apply_adjustments`) before calling this."""
    y = yf_prices[["symbol", "date", "close"]].rename(
        columns={"close": "yf_close"})
    b = bhav_prices[["symbol", "date", "close"]].rename(
        columns={"close": "bhav_close"})
    m = y.merge(b, on=["symbol", "date"], how="inner")
    m["pct_diff"] = 100 * (m["bhav_close"] / m["yf_close"] - 1).abs()
    return m.sort_values("pct_diff", ascending=False).reset_index(drop=True)


def check_cross_source(yf_prices: pd.DataFrame, bhav_prices: pd.DataFrame,
                       threshold_pct: float = 0.5
                       ) -> tuple[str, str, str]:
    """One verify-report row summarising cross-source agreement so far.
    WARN (not FAIL) on divergence — this is evidence-gathering during
    the side-by-side period, not a live-store integrity gate; a
    systematic >0.5% gap is a signal to investigate, not to refuse a
    screen (bhavcopy isn't consumed by any screen yet)."""
    if bhav_prices is None or bhav_prices.empty:
        return ("cross-source (bhavcopy)", WARN,
                "no bhavcopy data yet — run `python -m screener.cli "
                "bhavcopy-update` to start the side-by-side clock")
    div = cross_source_divergence(yf_prices, bhav_prices)
    if div.empty:
        return ("cross-source (bhavcopy)", WARN,
                "no overlapping (symbol,date) rows yet between the two "
                "stores")
    bad = div[div["pct_diff"] > threshold_pct]
    if bad.empty:
        detail = (f"{len(div)} overlapping bars, median diff "
                  f"{div['pct_diff'].median():.4f}%, max "
                  f"{div['pct_diff'].max():.4f}% — all within "
                  f"{threshold_pct}% tolerance")
    else:
        detail = (f"{len(div)} overlapping bars, median diff "
                  f"{div['pct_diff'].median():.4f}%, {len(bad)} bar(s) "
                  f"over {threshold_pct}% (e.g. "
                  f"{', '.join(bad['symbol'].unique()[:5])}) — "
                  "investigate before counting this toward cutover evidence")
    return ("cross-source (bhavcopy)", PASS if bad.empty else WARN, detail)


def check_screen_log(log_lines: list[str] | None,
                     rotated_lines: list[str] | None = None
                     ) -> tuple[str, str, str]:
    """Parseable-JSONL integrity check for `data/screen_log.jsonl` and,
    once size-capped rotation has kicked in (ROADMAP Item 6), its
    `screen_log.rotated.jsonl` archive too. Log writes never raise (a
    failed write can't break a screen — see webapp.py), so this is the
    only place a corrupt log would surface."""
    all_lines = list(log_lines or []) + list(rotated_lines or [])
    if not all_lines:
        return ("screen log", WARN,
                "no screens logged yet — run one from the web UI")
    required = {"ts", "as_of", "spec", "stats", "matched"}
    bad = []
    for i, line in enumerate(all_lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad.append(i)
            continue
        if not required.issubset(obj):
            bad.append(i)
    if bad:
        return ("screen log", FAIL,
                f"{len(bad)}/{len(all_lines)} lines unparseable or missing "
                f"required keys (first bad line: {bad[0] + 1})")
    if rotated_lines:
        detail = (f"{len(log_lines or [])} active + {len(rotated_lines)} "
                  f"rotated = {len(all_lines)} entries, all parseable")
    else:
        detail = f"{len(all_lines)} entries, all parseable"
    return ("screen log", PASS, detail)


def check_backup(backup_info: dict) -> tuple[str, str, str]:
    """One verify-report row for the latest evidence backup (ROADMAP
    Item 18 v1.0 hardening) — confirms a recent snapshot exists and its
    JSONL files parse. Doesn't check the snapshot's CONTENTS match the
    live stores (create_backup() making a byte-for-byte copy already
    guarantees that by construction) or how old it is beyond an
    informational note — a strict staleness FAIL wasn't asked for and
    would invent a threshold this check has no basis to pick."""
    if not backup_info["exists"]:
        return ("evidence backup", WARN,
                "no backup yet — run `python -m screener.cli backup`")
    if backup_info["bad_files"]:
        return ("evidence backup", FAIL,
                f"{backup_info['path']}: unparseable file(s): "
                f"{', '.join(backup_info['bad_files'])}")
    return ("evidence backup", PASS,
            f"latest snapshot: {backup_info['path']}")


def verify_store(prices: pd.DataFrame, universe: pd.DataFrame,
                 benchmark: pd.Series | None,
                 panels: dict[str, pd.DataFrame] | None = None,
                 bhav_prices: pd.DataFrame | None = None,
                 screen_log_lines: list[str] | None = None,
                 screen_log_rotated_lines: list[str] | None = None,
                 backup_info: dict | None = None
                 ) -> list[tuple[str, str, str]]:
    """[(check_name, status, detail), ...]"""
    r: list[tuple[str, str, str]] = []
    n_expected = len(universe)
    got = prices["symbol"].nunique()

    # -- coverage -----------------------------------------------------
    missing = sorted(set(universe["symbol"]) - set(prices["symbol"]))
    cov = 100 * got / max(n_expected, 1)
    status = PASS if cov >= 97 else WARN if cov >= 90 else FAIL
    detail = f"{got}/{n_expected} symbols ({cov:.1f}%)"
    if missing:
        detail += f"; missing e.g. {', '.join(missing[:8])}" + \
                  (" …" if len(missing) > 8 else "") + \
                  " — likely NSE↔Yahoo ticker mismatches, see " \
                  "TECHNICAL_DESIGN.md §4 (alias map)"
    r.append(("symbol coverage", status, detail))

    # -- freshness ----------------------------------------------------
    latest = prices["date"].max()
    age = (pd.Timestamp.today().normalize() - latest).days
    r.append(("store freshness",
              PASS if age <= config.MAX_STALENESS_DAYS else FAIL,
              f"latest bar {latest.date()} ({age}d old, limit "
              f"{config.MAX_STALENESS_DAYS}d)"))

    # -- history depth ------------------------------------------------
    span_years = (latest - prices["date"].min()).days / 365.25
    r.append(("history depth",
              PASS if span_years >= config.HISTORY_YEARS - 0.5 else WARN,
              f"{span_years:.1f}y (target {config.HISTORY_YEARS}y)"))

    bars = prices.groupby("symbol").size()
    thin = bars[bars < 250].index.tolist()
    r.append(("per-symbol depth",
              PASS if len(thin) <= 0.05 * got else WARN,
              f"{len(thin)} symbols with <250 bars (recent listings "
              f"expected)" + (f": e.g. {', '.join(thin[:6])}" if thin else "")))

    # -- bar integrity ------------------------------------------------
    bad_ohlc = int(((prices[["open", "high", "low", "close"]] <= 0)
                    .any(axis=1) | (prices["high"] < prices["low"])).sum())
    r.append(("bar integrity", PASS if bad_ohlc == 0 else FAIL,
              f"{bad_ohlc} impossible bars (should be 0 after _clean)"))

    dupes = int(prices.duplicated(["symbol", "date"]).sum())
    r.append(("duplicate bars", PASS if dupes == 0 else FAIL,
              f"{dupes} duplicate (symbol,date) rows"))

    zero_vol = prices.groupby("symbol")["volume"].apply(
        lambda s: (s.tail(20) == 0).mean())
    dead = zero_vol[zero_vol > 0.5].index.tolist()
    r.append(("volume liveness", PASS if not dead else WARN,
              f"{len(dead)} symbols with >50% zero-volume in last 20 bars"
              + (f": {', '.join(dead[:6])}" if dead else "")))

    # -- corporate-action smell test ---------------------------------
    # unadjusted splits show up as huge one-day gaps; adjusted data should
    # have very few >40% single-day moves outside genuine crashes
    rets = prices.sort_values(["symbol", "date"]).groupby("symbol")[
        "close"].pct_change().abs()
    jumps = int((rets > 0.40).sum())
    r.append(("adjustment smell test",
              PASS if jumps <= max(2, got // 100) else WARN,
              f"{jumps} single-day |moves| >40% across store — a high "
              "count suggests unadjusted corporate actions"))

    # -- benchmark ----------------------------------------------------
    if benchmark is None or len(benchmark) == 0:
        r.append(("benchmark (Nifty)", FAIL,
                  "missing — rel_strength screens will refuse to run; "
                  "re-run update"))
    else:
        b_age = (pd.Timestamp.today().normalize()
                 - benchmark.index.max()).days
        r.append(("benchmark (Nifty)",
                  PASS if b_age <= config.MAX_STALENESS_DAYS else WARN,
                  f"{len(benchmark)} bars, latest "
                  f"{benchmark.index.max().date()}"))

    # -- indicator spot checks ---------------------------------------
    if panels:
        syms = sorted(panels, key=lambda s: -len(panels[s]))[:25]
        rsi_bad = ema_bad = 0
        for s in syms:
            p = panels[s]
            rsi_tail = p["rsi"].dropna().tail(250)
            if len(rsi_tail) and not rsi_tail.between(0, 100).all():
                rsi_bad += 1
            e = p[["close", "ema_50"]].dropna().tail(250)
            # EMA must stay within the price envelope loosely: mean gap <15%
            if len(e) and (np.abs(e["close"] / e["ema_50"] - 1)
                           .mean() > 0.15):
                ema_bad += 1
        r.append(("RSI bounds (25-symbol sample)",
                  PASS if rsi_bad == 0 else FAIL,
                  f"{rsi_bad} symbols with RSI outside [0,100]"))
        r.append(("EMA50 plausibility (25-symbol sample)",
                  PASS if ema_bad == 0 else WARN,
                  f"{ema_bad} symbols with mean |close/EMA50−1| >15% — "
                  "check for data gaps"))

    # -- data layer v2 side-by-side evidence (Item 3, pre-cutover) ----
    r.append(check_cross_source(prices, bhav_prices))
    r.append(check_screen_log(screen_log_lines, screen_log_rotated_lines))
    if backup_info is not None:
        r.append(check_backup(backup_info))
    return r


def print_report(results: list[tuple[str, str, str]]) -> int:
    width = max(len(n) for n, _, _ in results)
    icon = {PASS: "✓", WARN: "!", FAIL: "✗"}
    for name, status, detail in results:
        print(f" {icon[status]} {status:<4} {name:<{width}}  {detail}")
    fails = sum(s == FAIL for _, s, _ in results)
    warns = sum(s == WARN for _, s, _ in results)
    print(f"\n{len(results)} checks: "
          f"{len(results) - fails - warns} pass, {warns} warn, "
          f"{fails} fail")
    if fails:
        print("Resolve FAILs before trusting any screen output.")
    elif warns:
        print("WARNs are usually benign (recent listings, ticker "
              "mismatches) but worth a look — details above.")
    else:
        print("Store looks healthy. Suggested next step: run the flagship "
              "query and eyeball 2-3 matches against a charting platform.")
    return 1 if fails else 0


def list_jumps(prices: pd.DataFrame, threshold: float = 0.40
               ) -> pd.DataFrame:
    """The exact bars behind the adjustment smell test: one row per
    single-day |move| > threshold, with context for classification."""
    p = prices.sort_values(["symbol", "date"]).copy()
    p["ret"] = p.groupby("symbol")["close"].pct_change()
    j = p.loc[p["ret"].abs() > threshold,
              ["symbol", "date", "close", "ret"]].copy()
    if j.empty:
        return j
    prev = p.groupby("symbol")["close"].shift(1)
    j["prev_close"] = prev.loc[j.index]
    j["move_pct"] = (100 * j.pop("ret")).round(1)
    # classification hint: a clean ratio near 1/2, 1/5, 1/10 smells like an
    # unadjusted split/bonus; anything else is more likely a real event
    ratio = j["close"] / j["prev_close"]
    j["ratio"] = ratio.round(3)
    j["hint"] = np.where(
        (np.abs(ratio - 0.5) < 0.03) | (np.abs(ratio - 0.2) < 0.02)
        | (np.abs(ratio - 0.1) < 0.01) | (np.abs(ratio - 0.25) < 0.02),
        "split-like ratio — likely UNADJUSTED action",
        "no clean ratio — likely real event (verify news)")
    return j[["symbol", "date", "prev_close", "close", "move_pct",
              "ratio", "hint"]].reset_index(drop=True)
