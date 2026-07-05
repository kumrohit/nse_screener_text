"""Post-backfill verification — automates the §10 sanity checklist.

    python -m screener.cli verify

Runs structural, coverage, and indicator-sanity checks against the live
store and prints a PASS/WARN/FAIL report. Exit code 1 on any FAIL, so it
can gate a cron pipeline (backfill && verify && ...).

Checks are pure functions over dataframes so they're testable on synthetic
data without touching disk.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def verify_store(prices: pd.DataFrame, universe: pd.DataFrame,
                 benchmark: pd.Series | None,
                 panels: dict[str, pd.DataFrame] | None = None
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
