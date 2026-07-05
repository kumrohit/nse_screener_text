"""Demo universe — eight synthetic stocks with distinct, engineered
behaviours so the web UI is fully explorable before any live backfill.
Activated automatically when data/prices.parquet is absent; the UI shows a
prominent DEMO banner.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators

_RNG = np.random.default_rng(7)


def _panel(closes: np.ndarray, vol_last_ratio: float = 1.0) -> pd.DataFrame:
    n = len(closes)
    end = pd.Timestamp.today().normalize()
    if end.dayofweek >= 5:                 # weekend → last business day
        end -= pd.offsets.BDay(1)
    dates = pd.bdate_range(end=end, periods=n)
    close = pd.Series(closes, index=dates)
    noise = 1 + _RNG.normal(0, 0.002, n)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * (1.005 * np.abs(noise)),
        "low": close * 0.995,
        "close": close,
        "volume": pd.Series(1_000_000.0, index=dates),
    })
    df.iloc[-1, df.columns.get_loc("volume")] *= vol_last_ratio
    return indicators.compute_panel(df)


def _trend(n, drift, start=100.0):
    return start * np.cumprod(1 + np.full(n, drift))


def build_demo() -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.Series]:
    """(panels, universe_df, benchmark_series)"""
    panels: dict[str, pd.DataFrame] = {}

    up = _trend(600, 0.0015)
    pullback = up.copy()
    dip = np.array([0, -0.010, -0.022, -0.032, -0.022, -0.012])
    pullback[-6:] = pullback[-7] * (1 + dip)
    p = _panel(pullback, vol_last_ratio=1.8)
    p.iloc[-3, p.columns.get_loc("low")] = p["ema_50"].iloc[-3] * 1.001
    panels["PULLBK"] = p                                  # support at EMA50

    panels["STEADY"] = _panel(_trend(600, 0.0018))        # clean uptrend

    brk = _trend(600, 0.0015)
    brk[-8:] = brk[-9] * np.cumprod(np.full(8, 0.975))
    panels["BRKDWN"] = _panel(brk, vol_last_ratio=2.6)    # breakdown

    seg = np.concatenate([np.linspace(100, 110, 25),
                          np.linspace(110, 100, 25)])
    panels["RANGER"] = _panel(np.concatenate(
        [np.tile(seg, 10), np.linspace(100, 101.5, 10)])) # at support

    panels["BRKOUT"] = _panel(np.concatenate(
        [np.tile(seg, 10), np.linspace(100, 118, 8)]),
        vol_last_ratio=2.2)                               # resistance breakout

    sell = np.concatenate([_trend(520, 0.0006),
                           _trend(520, 0.0006)[-1]
                           * np.cumprod(1 - np.full(80, 0.006))])
    panels["OVRSLD"] = _panel(sell)                       # RSI oversold

    gx = np.concatenate([_trend(450, -0.001),
                         _trend(450, -0.001)[-1]
                         * np.cumprod(1 + np.full(150, 0.004))])
    panels["GLDNCX"] = _panel(gx)                         # recent golden cross

    panels["DRIFTR"] = _panel(
        100 + np.cumsum(_RNG.normal(0, 0.25, 600)))       # noise

    uni = pd.DataFrame({
        "symbol": list(panels),
        "name": ["Pullback Industries", "Steady Compounders",
                 "Breakdown Metals", "Rangebound Retail",
                 "Breakout Chemicals", "Oversold Textiles",
                 "Golden Cross Finance", "Drifter Media"],
        "industry": ["Capital Goods", "IT", "Metals", "Retail",
                     "Chemicals", "Textiles", "Financials", "Media"],
    })
    idx = panels["STEADY"].index
    bench = pd.Series(100 * np.cumprod(1 + np.full(len(idx), 0.0007)),
                      index=idx, name="nifty_close")
    return panels, uni, bench
