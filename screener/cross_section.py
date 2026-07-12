"""Cross-sectional pre-pass — universe-wide ranks computed once per
(as_of, window), consumed by the `rs_percentile`, `sector_rank`, and
`atr_pct_percentile` conditions.

Pure function over panels + universe metadata: no disk state, no
look-ahead (each symbol's return is taken from its row at or before
`as_of`, never from "today"). Cached in-process only, keyed by
(id(panels), as_of, window) — cheap enough per screen (see perf note in
TECHNICAL_DESIGN.md) that a disk cache isn't worth the invalidation
complexity in v1.
"""
from __future__ import annotations

import pandas as pd

from .evaluator import _row_at

# Bounded FIFO cache: replaying many as-of dates in a long-running webapp
# must not grow memory forever. 32 entries ≈ 32 replayed dates, far more
# than a session uses; eviction is oldest-first.
#
# Keyed by (id(panels), frozenset(panels), as_of, window) — id() alone
# isn't safe: a short-lived `panels` dict (e.g. one built fresh per test
# or per CLI invocation) can be garbage-collected and its memory address
# reused by an unrelated dict built moments later, which would then
# silently hit this cache and get another universe's stale ranks back.
# This was a real, non-hypothetical bug: ROADMAP Item 14's backtest test
# suite builds and discards dozens of small synthetic `panels` dicts per
# run, and started intermittently corrupting unrelated preset tests
# elsewhere in the same pytest session via exactly this id-reuse path.
# frozenset(panels) is just the symbol set — cheap (panels.keys() is
# already iterated to build the table below) and, combined with id(),
# makes an accidental collision require both the address AND the exact
# symbol set to match, which doesn't happen in practice.
_CACHE: dict[tuple, pd.DataFrame] = {}
_CACHE_MAX = 32


def build_cross_section(panels: dict[str, pd.DataFrame],
                        universe: pd.DataFrame,
                        as_of: str | None = "latest",
                        window: int = 63) -> pd.DataFrame:
    """Per-symbol table: sector, `window`-bar return, RS percentile among
    symbols with sufficient history, equal-weight sector return, the
    sector's momentum rank (1 = best), 12-1 skip-month momentum percentile
    (ROADMAP Item 9 — see LITERATURE.md §1), and ATR% (volatility)
    percentile (LITERATURE.md §6) — ranked ascending, so 0 = lowest
    volatility in the universe.

    Thin-history symbols (fewer than `window` bars up to `as_of`, or a
    NaN close) get `ret_pct = NaN` and are excluded from ranking rather
    than defaulted to the 0th/100th percentile. Same NaN-exclusion policy
    applies to `mom_12_1` and `atr_pct` independently of `window`.
    """
    key = (id(panels), frozenset(panels), as_of, int(window))
    if key in _CACHE:
        return _CACHE[key]

    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else pd.Series(dtype=str))

    rows = []
    for sym, panel in panels.items():
        i = _row_at(panel, as_of)
        ret = mom_12_1 = atr_pct = float("nan")
        if i is not None and i - window >= 0:
            base, cur = panel["close"].iloc[i - window], panel["close"].iloc[i]
            if pd.notna(base) and pd.notna(cur) and base != 0:
                ret = 100 * (cur / base - 1)
        if i is not None:
            if "mom_12_1" in panel.columns:
                mom_12_1 = panel["mom_12_1"].iloc[i]
            if "atr_pct" in panel.columns:
                atr_pct = panel["atr_pct"].iloc[i]
        rows.append({
            "symbol": sym,
            "sector": sector_by_symbol.get(sym),
            "ret_pct": ret,
            "mom_12_1": mom_12_1,
            "atr_pct": atr_pct,
        })
    df = pd.DataFrame(rows)

    # RS percentile: NaN rets stay NaN (pandas rank keeps NaN by default) —
    # never coerced to 0th/100th, so thin-history names are simply excluded.
    df["rs_percentile"] = (df["ret_pct"].rank(pct=True, na_option="keep")
                           * 100).round(2)
    df["mom_12_1_percentile"] = (
        df["mom_12_1"].rank(pct=True, na_option="keep") * 100).round(2)
    # Ascending: 0 = least volatile. Low-vol preset reads this directly;
    # high-vol ("ma_timing_highvol") reads the same column with `>=`.
    df["atr_percentile"] = (
        df["atr_pct"].rank(pct=True, na_option="keep") * 100).round(2)

    sector_ret = df.groupby("sector")["ret_pct"].mean()
    df["sector_ret_pct"] = df["sector"].map(sector_ret).round(2)
    n_sectors = sector_ret.notna().sum()
    sector_rank = sector_ret.rank(ascending=False, method="min",
                                  na_option="keep")
    df["sector_rank"] = df["sector"].map(sector_rank)
    df["n_sectors"] = n_sectors
    df["ret_pct"] = df["ret_pct"].round(2)

    df = df.set_index("symbol")
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))  # FIFO evict oldest
    _CACHE[key] = df
    return df


# ---------------------------------------------------------------- breadth
# Bounded FIFO cache, same reasoning as _CACHE above — keyed the same way
# (id(panels), frozenset(panels), as_of) since breadth has no `window`.
_BREADTH_CACHE: dict[tuple, dict] = {}
_BREADTH_CACHE_MAX = 32


def compute_breadth(panels: dict[str, pd.DataFrame], as_of: str | None = "latest"
                    ) -> dict:
    """Market breadth regime fields (ROADMAP breadth item, Session 2 prep)
    — a single scalar snapshot for `as_of`, computed from the universe
    itself with no external data feed: `pct_above_200dma` (% of symbols
    with close > SMA200 — the standard "stocks above the 200-day moving
    average" breadth gauge) and `pct_at_20d_high` (% of symbols making a
    new 20-trading-day high today — the standard new-high/new-low breadth
    convention: today's high at or above the highest high of the prior
    20 bars, NOT including today, so a flat market can't trivially
    satisfy it every day). A symbol needs 200 bars for SMA200 and 20
    PRIOR bars for the new-high check to contribute at all — thin-history
    symbols are excluded from both the numerator and denominator (same
    NaN-exclusion policy `build_cross_section` already uses for
    `ret_pct`), never defaulted to "not above"/"not at a high"."""
    key = (id(panels), frozenset(panels), as_of)
    if key in _BREADTH_CACHE:
        return _BREADTH_CACHE[key]

    above_200 = at_20high = total = 0
    for panel in panels.values():
        i = _row_at(panel, as_of)
        if i is None or i < 20 or "sma_200" not in panel.columns:
            continue
        close, sma200 = panel["close"].iloc[i], panel["sma_200"].iloc[i]
        cur_high = panel["high"].iloc[i]
        prior_high = panel["high"].iloc[i - 20: i].max()  # prior 20 bars,
                                                           # excludes today
        if (pd.isna(close) or pd.isna(sma200) or pd.isna(cur_high)
                or pd.isna(prior_high)):
            continue
        total += 1
        if close > sma200:
            above_200 += 1
        if cur_high >= prior_high:
            at_20high += 1

    result = {
        "pct_above_200dma": round(100 * above_200 / total, 2) if total else None,
        "pct_at_20d_high": round(100 * at_20high / total, 2) if total else None,
        "n_symbols": total,
    }
    if len(_BREADTH_CACHE) >= _BREADTH_CACHE_MAX:
        _BREADTH_CACHE.pop(next(iter(_BREADTH_CACHE)))
    _BREADTH_CACHE[key] = result
    return result
