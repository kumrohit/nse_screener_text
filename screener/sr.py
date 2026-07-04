"""Horizontal support/resistance from swing pivots.

Methodology (documented in TECHNICAL_DESIGN.md §8):

1. Pivot detection — fractal method. Bar j is a pivot high if its high is
   the maximum of the window [j-k, j+k] (default k=5). Mirror for lows.
   The last k bars of any window can never confirm a pivot (the right side
   of the fractal doesn't exist yet) — this is standard and prevents
   look-ahead: a pivot only "exists" once k bars have printed after it.

2. Level clustering — collected pivot prices over the lookback window
   (default 250 bars) are sorted and greedily clustered: a price joins the
   current cluster if it is within `tol_pct` of the cluster's last member.
   Each cluster becomes one level (mean of members) with a touch count.

3. Significance filter — only levels with >= min_touches (default 2)
   survive. A single pivot is noise; two or more pivots at the same price
   is structure.

All functions take an explicit row position `i` and only use data up to
and including row i, so historical as-of screening stays honest.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PIVOT_K = 5
SR_LOOKBACK = 250
SR_MIN_TOUCHES = 2
SR_CLUSTER_TOL_PCT = 1.0


def find_pivots(df: pd.DataFrame, k: int = PIVOT_K
                ) -> tuple[pd.Series, pd.Series]:
    """Boolean masks (pivot_high, pivot_low). Last k bars are always False."""
    w = 2 * k + 1
    ph = df["high"] == df["high"].rolling(w, center=True, min_periods=w).max()
    pl = df["low"] == df["low"].rolling(w, center=True, min_periods=w).min()
    return ph.fillna(False), pl.fillna(False)


def sr_levels(panel: pd.DataFrame, i: int, k: int = PIVOT_K,
              lookback: int = SR_LOOKBACK,
              min_touches: int = SR_MIN_TOUCHES,
              tol_pct: float = SR_CLUSTER_TOL_PCT
              ) -> list[tuple[float, int]]:
    """[(level_price, touch_count), ...] sorted ascending, using data
    up to row i only."""
    lo = max(0, i - lookback)
    win = panel.iloc[lo: i + 1]
    if len(win) < 2 * k + 1:
        return []
    ph, pl = find_pivots(win, k)
    prices = sorted(
        list(win.loc[ph, "high"]) + list(win.loc[pl, "low"]))
    if not prices:
        return []

    levels: list[tuple[float, int]] = []
    cluster = [prices[0]]
    for p in prices[1:]:
        if 100 * (p - cluster[-1]) / cluster[-1] <= tol_pct:
            cluster.append(p)
        else:
            levels.append((float(np.mean(cluster)), len(cluster)))
            cluster = [p]
    levels.append((float(np.mean(cluster)), len(cluster)))
    return [(lvl, n) for lvl, n in levels if n >= min_touches]


def nearest_levels(panel: pd.DataFrame, i: int, **kw
                   ) -> tuple[float | None, float | None]:
    """(nearest support at/below close, nearest resistance above close)."""
    close = panel["close"].iloc[i]
    levels = sr_levels(panel, i, **kw)
    supports = [l for l, _ in levels if l <= close]
    resistances = [l for l, _ in levels if l > close]
    return (max(supports) if supports else None,
            min(resistances) if resistances else None)
