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
# must not grow memory forever, and a bounded cache also limits the blast
# radius of the id(panels) key if a future data-reload feature ever rebuilds
# the panels dict (a GC-reused id could otherwise serve stale ranks
# indefinitely). 32 entries ≈ 32 replayed dates, far more than a session
# uses; eviction is oldest-first.
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
    key = (id(panels), as_of, int(window))
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
