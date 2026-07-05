"""Cross-sectional pre-pass — universe-wide ranks computed once per
(as_of, window), consumed by the `rs_percentile` and `sector_rank`
conditions.

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

_CACHE: dict[tuple, pd.DataFrame] = {}


def build_cross_section(panels: dict[str, pd.DataFrame],
                        universe: pd.DataFrame,
                        as_of: str | None = "latest",
                        window: int = 63) -> pd.DataFrame:
    """Per-symbol table: sector, `window`-bar return, RS percentile among
    symbols with sufficient history, equal-weight sector return, and the
    sector's momentum rank (1 = best).

    Thin-history symbols (fewer than `window` bars up to `as_of`, or a
    NaN close) get `ret_pct = NaN` and are excluded from ranking rather
    than defaulted to the 0th/100th percentile.
    """
    key = (id(panels), as_of, int(window))
    if key in _CACHE:
        return _CACHE[key]

    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else pd.Series(dtype=str))

    rows = []
    for sym, panel in panels.items():
        i = _row_at(panel, as_of)
        ret = float("nan")
        if i is not None and i - window >= 0:
            base, cur = panel["close"].iloc[i - window], panel["close"].iloc[i]
            if pd.notna(base) and pd.notna(cur) and base != 0:
                ret = 100 * (cur / base - 1)
        rows.append({
            "symbol": sym,
            "sector": sector_by_symbol.get(sym),
            "ret_pct": ret,
        })
    df = pd.DataFrame(rows)

    # RS percentile: NaN rets stay NaN (pandas rank keeps NaN by default) —
    # never coerced to 0th/100th, so thin-history names are simply excluded.
    df["rs_percentile"] = (df["ret_pct"].rank(pct=True, na_option="keep")
                           * 100).round(2)

    sector_ret = df.groupby("sector")["ret_pct"].mean()
    df["sector_ret_pct"] = df["sector"].map(sector_ret).round(2)
    n_sectors = sector_ret.notna().sum()
    sector_rank = sector_ret.rank(ascending=False, method="min",
                                  na_option="keep")
    df["sector_rank"] = df["sector"].map(sector_rank)
    df["n_sectors"] = n_sectors
    df["ret_pct"] = df["ret_pct"].round(2)

    df = df.set_index("symbol")
    _CACHE[key] = df
    return df
