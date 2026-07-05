"""Indicator engine.

Pure pandas/numpy (no pandas-ta dependency — fewer version headaches, and
every formula is auditable). Computes a full indicator panel per symbol;
the evaluator consumes the last N rows of each panel.

All functions operate on a single-symbol OHLCV DataFrame indexed by date,
then `build_panels` maps over the universe via groupby.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# ---------------------------------------------------------------- core maths
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False,
                                   min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False,
                                      min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat(
        [df["high"] - df["low"],
         (df["high"] - pc).abs(),
         (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False,
                              min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0),
                        index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0),
                         index=df.index)
    tr_s = true_range(df).ewm(alpha=1 / n, adjust=False,
                              min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, adjust=False,
                            min_periods=n).mean() / tr_s
    mdi = 100 * minus_dm.ewm(alpha=1 / n, adjust=False,
                             min_periods=n).mean() / tr_s
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({
        "adx": dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(),
        "plus_di": pdi, "minus_di": mdi,
    })


# ---------------------------------------------------------------- panel
def compute_panel(df: pd.DataFrame) -> pd.DataFrame:
    """df: single-symbol OHLCV sorted by date, indexed by date."""
    out = df[["open", "high", "low", "close", "volume"]].copy()
    c = out["close"]

    for n in config.EMA_PERIODS:
        out[f"ema_{n}"] = ema(c, n)
        out[f"ema_{n}_slope"] = out[f"ema_{n}"].diff(5)  # 5-bar slope
    for n in config.SMA_PERIODS:
        out[f"sma_{n}"] = sma(c, n)

    out["rsi"] = rsi(c, config.RSI_PERIOD)
    out["atr"] = atr(out, config.ATR_PERIOD)
    out["atr_pct"] = 100 * out["atr"] / c
    out = out.join(adx(out, config.ADX_PERIOD))

    macd_line = ema(c, 12) - ema(c, 26)
    out["macd"] = macd_line
    out["macd_signal"] = ema(macd_line, 9)
    out["macd_hist"] = macd_line - out["macd_signal"]

    mid = sma(c, config.BB_PERIOD)
    sd = c.rolling(config.BB_PERIOD, min_periods=config.BB_PERIOD).std()
    out["bb_upper"] = mid + config.BB_STD * sd
    out["bb_lower"] = mid - config.BB_STD * sd
    out["bb_width_pct"] = 100 * (out["bb_upper"] - out["bb_lower"]) / mid

    out["vol_avg_20"] = sma(out["volume"], config.VOL_AVG_PERIOD)
    out["vol_ratio"] = out["volume"] / out["vol_avg_20"]
    out["turnover_cr"] = c * out["volume"] / 1e7

    out["high_52w"] = out["high"].rolling(252, min_periods=60).max()
    out["low_52w"] = out["low"].rolling(252, min_periods=60).min()
    out["pct_from_52w_high"] = 100 * (c / out["high_52w"] - 1)
    out["pct_from_52w_low"] = 100 * (c / out["low_52w"] - 1)

    for n in (5, 21, 63):  # 1w, 1m, 3m
        out[f"roc_{n}"] = 100 * c.pct_change(n)

    return out


def build_panels(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """{symbol: indicator panel}. Long-format prices in, dict out."""
    panels = {}
    for sym, grp in prices.groupby("symbol"):
        g = grp.set_index("date").sort_index()
        if len(g) < 60:          # not enough history to say anything
            continue
        panels[sym] = compute_panel(g)
    return panels


# ---------------------------------------------------------------- weekly
WEEKLY_EMA_PERIODS = [10, 20, 40]


def compute_weekly_panel(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample a single-symbol daily OHLCV panel to weekly (W-FRI) and
    compute the weekly indicator subset. The in-progress week is included
    as a partial bar — acceptable for screening 'as of now', but note it
    in results interpretation."""
    w = pd.DataFrame({
        "open": daily["open"].resample("W-FRI").first(),
        "high": daily["high"].resample("W-FRI").max(),
        "low": daily["low"].resample("W-FRI").min(),
        "close": daily["close"].resample("W-FRI").last(),
        "volume": daily["volume"].resample("W-FRI").sum(),
    }).dropna(subset=["close"])
    c = w["close"]
    for n in WEEKLY_EMA_PERIODS:
        w[f"ema_{n}"] = ema(c, n)
        w[f"ema_{n}_slope"] = w[f"ema_{n}"].diff(3)
    w["rsi"] = rsi(c, 14)
    w["roc_4"] = 100 * c.pct_change(4)    # ~1 month
    w["roc_13"] = 100 * c.pct_change(13)  # ~1 quarter
    return w
