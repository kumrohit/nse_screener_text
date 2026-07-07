"""Screen backtester — event-study engine (ROADMAP Item 14, v0.12).

Answers: for any DSL spec, what happened after this signal fired
historically, versus just holding the universe? This is an *edge
detector for filters*, NOT a portfolio simulator: no position sizing, no
compounding, no stops, no execution modelling (that is the separate
concern `screener/allocate.py` covers once a screen has already earned
some trust).

Methodology — locked, see ROADMAP.md Item 14:

  Event definition   signal(sym, t) True AND no event for that symbol in
                      the prior `cooldown` bars (default 20) — de-dupes
                      "signal stays true for a week" into one event.
  Entry convention    open[t+1] (signal computed on bar-t close; a
                      close-t entry would be look-ahead). Forward return
                      at horizon h = close[t+h] / open[t+1] - 1. Events
                      within h bars of a panel's end are excluded from
                      that horizon only, never NaN-polluted into stats.
  Baseline            same-date equal-weight mean forward return, same
                      horizon, over ALL liquidity-passing universe
                      symbols — controls for the market regime at signal
                      time. Primary comparison (not the Nifty index).
  Costs               gross AND net (net = gross - cost_pct round trip,
                      default 0.30%) always shown side by side.
  Statistics honesty  date-level block bootstrap on the event-date
                      portfolio series, never pooled-event t-stats
                      (same-date events are cross-correlated). <30 valid
                      events at a horizon suppresses stats outright.
  Sensitivity grid    one-parameter-at-a-time perturbation (±2 steps) of
                      the spec's own numeric thresholds, verdict: robust
                      across range, or edge concentrated at one value
                      (curve-fit warning).
  Survivorship        current-Nifty-500-constituents-only is a real bias
                      (dip-buying setups are flattered — the ones that
                      didn't survive aren't in this universe). Printed on
                      every result; use these numbers to rank filters
                      against each other, not as absolute expectations.

Engineering: a per-condition *vectorized* signal path (whole-panel
pandas ops) for the cheap condition types, and a strided "compute every
`stride`-th bar, forward-fill between" approximation for the expensive
ones (near_support/near_resistance/breakout_resistance/bb_squeeze — O(bars)
pivot search each call — and the cross-sectional rs_percentile/
sector_rank/atr_pct_percentile, which need a universe-wide rank rebuilt
per sample date). `verify_vectorizer_consistency` is the acceptance
test: vectorized signals must match `evaluator.evaluate_symbol()`
exactly at every date the vectorized path claims to be exact (all dates,
for specs with only cheap conditions; stride-grid dates only, for specs
touching an expensive one — the approximation is real and documented,
not silently swept under a "close enough").
"""
from __future__ import annotations

import copy as _copy
import random
import time

import numpy as np
import pandas as pd

from . import config, cross_section as cs_mod, dsl, evaluator

SURVIVORSHIP_NOTE = (
    "Survivorship caveat: this backtest runs over the CURRENT Nifty 500 "
    "constituent list projected backward. Symbols that were delisted, "
    "merged, or dropped from the index between then and now are not in "
    "this universe. This flatters strategies — especially dip-buying / "
    "mean-reversion setups — since the names that didn't survive aren't "
    "here to drag the average down. Use these numbers to RANK filters "
    "against each other on the same biased universe, not as absolute "
    "return expectations."
)

DEFAULT_HORIZONS = (5, 20, 60)
DEFAULT_COOLDOWN = 20
DEFAULT_COST_PCT = 0.30          # % round trip
# ROADMAP Item 14 named 5 as an example default; measured live against
# the real 500-symbol/5y store, stride=5 put near_support/
# breakout_resistance/bb_squeeze at ~6-7 minutes for a single condition
# — well past the <3min gate. stride=20 (~1 calendar month between
# samples) measured at ~25-70s per expensive condition type and ~30s
# for a cross-sectional one (rs_percentile/sector_rank/atr_pct_percentile,
# one window) — the default actually shipped. Callers who want tighter
# S/R resolution can still pass a smaller stride and accept the runtime.
DEFAULT_STRIDE = 20
MIN_EVENTS = 30
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
SENSITIVITY_HORIZON = 20

# The expensive set: O(lookback) per bar (pivot search) or requiring a
# universe-wide rank rebuild — computed on a `stride`-bar date grid with
# forward-fill rather than every bar. See module docstring.
EXPENSIVE_SYMBOL_TYPES = {"near_support", "near_resistance",
                          "breakout_resistance", "bb_squeeze"}
EXPENSIVE_CROSS_TYPES = {"rs_percentile", "sector_rank",
                         "atr_pct_percentile"}

# Numeric leaf keys the sensitivity grid auto-detects and perturbs one at
# a time (±2 steps of the listed size) — covers tolerance_pct, min_ratio,
# rsi/adx range bounds (the generic range.min/max keys), percentile
# cutoffs (rs_percentile/atr_pct_percentile.value, bb_squeeze.percentile),
# and the other common numeric thresholds across condition types.
SENSITIVITY_STEPS = {
    "tolerance_pct": 0.5, "min_ratio": 0.1, "value_pct": 2.0,
    "max_range_pct": 2.0, "buffer_pct": 0.5, "min_gap_pct": 0.5,
    "value": 5.0, "percentile": 5.0, "min": 2.0, "max": 2.0,
}


# ------------------------------------------------------------ asof mapping
def _asof_map(dst_index: pd.DatetimeIndex, src_index: pd.DatetimeIndex,
             src_values) -> pd.Series:
    """Map boolean `src_values` (aligned to `src_index`, sorted ascending)
    onto `dst_index` using the value from the most recent src entry at or
    before each dst date — the same "as of this date" semantics as
    `evaluator._row_at`, never look-ahead. Used for weekly->daily
    expansion and for the stride-grid forward-fill approximation."""
    vals = np.asarray(src_values, dtype=bool)
    pos = src_index.searchsorted(dst_index, side="right") - 1
    out = np.zeros(len(dst_index), dtype=bool)
    ok = pos >= 0
    out[ok] = vals[pos[ok]]
    return pd.Series(out, index=dst_index)


def _common_dates(panels: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    all_dates = sorted(set().union(*(p.index for p in panels.values())))
    return pd.DatetimeIndex(all_dates)


# ------------------------------------------------------------ cheap vectorizers
def _vec_field(panel: pd.DataFrame, f) -> pd.Series:
    if isinstance(f, (int, float)):
        return pd.Series(float(f), index=panel.index)
    return panel[f]


def _apply_op(a, op: str, b):
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    return a <= b


def _vec_compare(panel, c):
    left = _vec_field(panel, c["left"])
    right = _vec_field(panel, c["right"])
    valid = left.notna() & right.notna()
    return (_apply_op(left, c["op"], right) & valid).fillna(False)


def _vec_range(panel, c):
    v = panel[c["field"]]
    ok = v.notna()
    if "min" in c:
        ok = ok & (v >= c["min"])
    if "max" in c:
        ok = ok & (v <= c["max"])
    return ok.fillna(False)


def _vec_volume_spike(panel, c):
    v = panel["vol_ratio"]
    return ((v >= float(c["min_ratio"])) & v.notna()).fillna(False)


def _vec_change(panel, c):
    w = int(c["window"])
    s = panel[c["field"]]
    base = s.shift(w)
    valid = base.notna() & (base != 0) & s.notna()
    with np.errstate(invalid="ignore", divide="ignore"):
        chg = 100 * (s / base - 1)
    return (_apply_op(chg, c["op"], float(c["value_pct"])) & valid).fillna(False)


def _vec_trend(panel, c):
    close, e50, e200 = panel["close"], panel["ema_50"], panel["ema_200"]
    slope = panel["ema_50_slope"]
    valid = close.notna() & e50.notna() & e200.notna() & slope.notna()
    if c["direction"] == "up":
        cond = (close > e50) & (e50 > e200) & (slope > 0)
    else:
        cond = (close < e50) & (e50 < e200) & (slope < 0)
    return (cond & valid).fillna(False)


def _vec_weekly_trend(wp, c):
    close, e20, e40 = wp["close"], wp["ema_20"], wp["ema_40"]
    slope = wp["ema_20_slope"]
    valid = close.notna() & e20.notna() & e40.notna() & slope.notna()
    if c["direction"] == "up":
        cond = (close > e20) & (e20 > e40) & (slope > 0)
    else:
        cond = (close < e20) & (e20 < e40) & (slope < 0)
    return (cond & valid).fillna(False)


def _vec_cross(panel, c):
    lb = int(c.get("lookback", 3))
    fast, slow = panel[c["fast"]], panel[c["slow"]]
    diff = fast - slow
    prev = diff.shift(1)
    valid = diff.notna() & prev.notna()
    if c["direction"] == "above":
        crossed = (prev <= 0) & (diff > 0)
    else:
        crossed = (prev >= 0) & (diff < 0)
    crossed = (crossed & valid).fillna(False).astype(int)
    return crossed.rolling(lb, min_periods=1).max().astype(bool)


def _vec_proximity(panel, c):
    lb = int(c.get("lookback", 3))
    tgt, ref = panel[c["target"]], panel[c["ref"]]
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = (tgt - ref).abs() / ref * 100
    within = (dist <= c["tolerance_pct"]).fillna(False).astype(int)
    return within.rolling(lb, min_periods=1).max().astype(bool)


def _vec_support_at_ma(panel, c):
    ma, tol = c["ma"], float(c.get("tolerance_pct", 1.5))
    lb = int(c.get("lookback", 3))
    ma_s, low, close = panel[ma], panel["low"], panel["close"]
    with np.errstate(invalid="ignore", divide="ignore"):
        touched = (((low - ma_s).abs() / ma_s * 100) <= tol) | (low < ma_s)
    held = close >= ma_s * (1 - tol / 100)
    touched = touched.fillna(False).astype(int)
    held = held.fillna(False).astype(int)
    ma_isna_any = ma_s.isna().astype(int).rolling(
        lb, min_periods=1).max().astype(bool)
    touched_any = touched.rolling(lb, min_periods=1).max().astype(bool)
    held_all = held.rolling(lb, min_periods=1).min().astype(bool)
    latest_above = (close > ma_s).fillna(False)
    return touched_any & held_all & latest_above & (~ma_isna_any)


def _vec_gap(panel, c):
    lb = int(c.get("lookback", 3))
    min_pct = float(c.get("min_gap_pct", 2.0))
    direction = c["direction"]
    prev_close, o = panel["close"].shift(1), panel["open"]
    valid = prev_close.notna() & o.notna() & (prev_close != 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gap_pct = 100 * (o / prev_close - 1)
    hit = gap_pct >= min_pct if direction == "up" else gap_pct <= -min_pct
    hit = (hit & valid).fillna(False).astype(int)
    return hit.rolling(lb, min_periods=1).max().astype(bool)


def _vec_tight_range(panel, c):
    bars = int(c.get("bars", 10))
    max_range_pct = float(c["max_range_pct"])
    hi = panel["high"].rolling(bars, min_periods=bars).max()
    lo = panel["low"].rolling(bars, min_periods=bars).min()
    with np.errstate(invalid="ignore", divide="ignore"):
        span = 100 * (hi - lo) / lo
    valid = lo.notna() & (lo > 0) & hi.notna()
    return (_apply_op(span, "<=", max_range_pct) & valid).fillna(False)


def _vec_flat_base(panel, c):
    bars = int(c.get("bars", 20))
    max_range = float(c.get("max_range_pct", 12))
    max_off = float(c.get("max_from_52w_high_pct", 15))
    tr = _vec_tight_range(panel, {"bars": bars, "max_range_pct": max_range})
    off = panel["pct_from_52w_high"]
    return (tr & (off >= -max_off) & off.notna()).fillna(False)


def _vec_pattern(panel, pattern: str) -> pd.Series:
    h, l, o, c_ = panel["high"], panel["low"], panel["open"], panel["close"]
    if pattern == "inside_bar":
        return ((h < h.shift(1)) & (l > l.shift(1))).fillna(False)
    if pattern == "nr7":
        rng = h - l
        rmin_prev6 = rng.shift(1).rolling(6, min_periods=6).min()
        return (rng.notna() & rmin_prev6.notna()
               & (rng < rmin_prev6)).fillna(False)
    if pattern == "bullish_engulfing":
        po, pc = o.shift(1), c_.shift(1)
        return ((pc < po) & (c_ > o) & (o <= pc) & (c_ >= po)).fillna(False)
    if pattern == "bearish_engulfing":
        po, pc = o.shift(1), c_.shift(1)
        return ((pc > po) & (c_ < o) & (o >= pc) & (c_ <= po)).fillna(False)
    rng = h - l
    body = (c_ - o).abs()
    lower = np.minimum(o, c_) - l
    upper = h - np.maximum(o, c_)
    if pattern == "hammer":
        return ((rng > 0) & (lower >= 2 * body) & (upper <= 0.3 * rng)
                ).fillna(False)
    if pattern == "shooting_star":
        return ((rng > 0) & (upper >= 2 * body) & (lower <= 0.3 * rng)
                ).fillna(False)
    raise ValueError(f"unknown candle pattern {pattern!r}")


def _vec_candle(panel, c):
    lb = int(c.get("lookback", 1))
    single = _vec_pattern(panel, c["pattern"]).astype(int)
    return single.rolling(lb, min_periods=1).max().astype(bool)


def _vec_rel_strength(panel, c, benchmark: pd.Series | None):
    if benchmark is None:
        raise RuntimeError(
            "rel_strength condition requires benchmark data. Run "
            "`python -m screener.cli update` to fetch the Nifty index.")
    w = int(c["window"])
    b = benchmark.reindex(panel.index, method="ffill")
    b0, b1 = b.shift(w), b
    s0, s1 = panel["close"].shift(w), panel["close"]
    valid = (b0.notna() & b1.notna() & s0.notna() & s1.notna()
            & (b0 != 0) & (s0 != 0))
    rs = 100 * ((s1 / s0) - (b1 / b0))
    return (_apply_op(rs, c["op"], float(c["value_pct"])) & valid
           ).fillna(False)


def _vec_sector(panel, c, symbol, sector_by_symbol):
    if sector_by_symbol is None or symbol is None:
        raise RuntimeError(
            "sector condition requires universe metadata.")
    sec = sector_by_symbol.get(symbol)
    val = bool(sec is not None and pd.notna(sec) and sec in c["in"])
    return pd.Series(val, index=panel.index)


_CHEAP_DISPATCH = {
    "compare": _vec_compare, "range": _vec_range, "change": _vec_change,
    "trend": _vec_trend, "cross": _vec_cross, "proximity": _vec_proximity,
    "support_at_ma": _vec_support_at_ma, "volume_spike": _vec_volume_spike,
    "gap": _vec_gap, "tight_range": _vec_tight_range,
    "flat_base": _vec_flat_base, "candle": _vec_candle,
}


def _weekly_condition_series(wp: pd.DataFrame, c: dict) -> pd.Series:
    if c["type"] == "trend":
        return _vec_weekly_trend(wp, c)
    return _CHEAP_DISPATCH[c["type"]](wp, c)


# ------------------------------------------------------------ expensive (stride-grid)
def _stride_symbol_series(panel: pd.DataFrame, c: dict, ctype: str,
                          stride: int) -> pd.Series:
    n = len(panel)
    idx_samples = list(range(0, n, stride))
    if idx_samples[-1] != n - 1:
        idx_samples.append(n - 1)
    fn = {"near_support": evaluator.cond_near_support,
         "near_resistance": evaluator.cond_near_resistance,
         "breakout_resistance": evaluator.cond_breakout_resistance,
         "bb_squeeze": evaluator.cond_bb_squeeze}[ctype]
    sample_vals = np.array([bool(fn(panel, c, i)) for i in idx_samples])
    return _asof_map(panel.index, panel.index[idx_samples], sample_vals)


def _stride_cross_section_series(panel: pd.DataFrame, c: dict, ctype: str,
                                 symbol: str, dates_grid: pd.DatetimeIndex,
                                 cs_grid: dict) -> pd.Series:
    window = int(c.get("window", 63))
    fn = {"rs_percentile": evaluator.cond_rs_percentile,
         "sector_rank": evaluator.cond_sector_rank,
         "atr_pct_percentile": evaluator.cond_atr_pct_percentile}[ctype]
    sample_vals = np.zeros(len(dates_grid), dtype=bool)
    for k, d in enumerate(dates_grid):
        cs_df = cs_grid[(d, window)]
        if symbol in cs_df.index:
            sample_vals[k] = bool(fn(panel, c, 0, symbol=symbol,
                                     cross_section=cs_df))
    return _asof_map(panel.index, dates_grid, sample_vals)


# ------------------------------------------------------------ per-symbol signal
def _condition_series(panel, c, *, symbol, sector_by_symbol, benchmark,
                      weekly_cache, stride, dates_grid, cs_grid) -> pd.Series:
    ctype = c["type"]
    if c.get("timeframe") == "weekly":
        if "panel" not in weekly_cache:
            from . import indicators
            weekly_cache["panel"] = indicators.compute_weekly_panel(panel)
        wp = weekly_cache["panel"]
        wseries = _weekly_condition_series(wp, c)
        return _asof_map(panel.index, wp.index, wseries.to_numpy())
    if ctype == "rel_strength":
        return _vec_rel_strength(panel, c, benchmark)
    if ctype == "sector":
        return _vec_sector(panel, c, symbol, sector_by_symbol)
    if ctype in EXPENSIVE_SYMBOL_TYPES:
        return _stride_symbol_series(panel, c, ctype, stride)
    if ctype in EXPENSIVE_CROSS_TYPES:
        return _stride_cross_section_series(
            panel, c, ctype, symbol, dates_grid, cs_grid)
    return _CHEAP_DISPATCH[ctype](panel, c)


def _symbol_signal(panel, screen, *, symbol, sector_by_symbol, benchmark,
                  stride, dates_grid, cs_grid) -> pd.Series:
    weekly_cache: dict = {}
    series_list = [
        _condition_series(panel, c, symbol=symbol,
                          sector_by_symbol=sector_by_symbol,
                          benchmark=benchmark, weekly_cache=weekly_cache,
                          stride=stride, dates_grid=dates_grid,
                          cs_grid=cs_grid)
        for c in screen["conditions"]
    ]
    combined = series_list[0]
    op = (lambda a, b: a & b) if screen.get("logic", "AND") == "AND" \
        else (lambda a, b: a | b)
    for s in series_list[1:]:
        combined = op(combined, s)
    return combined


# ------------------------------------------------------------ returns / baseline
def _fwd_return_series(panel: pd.DataFrame, h: int) -> pd.Series:
    entry = panel["open"].shift(-1)
    exit_ = panel["close"].shift(-h)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = exit_ / entry - 1
    valid = entry.notna() & (entry > 0) & exit_.notna()
    return ret.where(valid)


def _liquidity_series(panel: pd.DataFrame, min_turnover_cr: float
                      ) -> pd.Series:
    if not min_turnover_cr:
        return pd.Series(True, index=panel.index)
    med = panel["turnover_cr"].rolling(20, min_periods=20).median()
    return (med >= min_turnover_cr).fillna(False)


def _dedup_events(idx_true: np.ndarray, cooldown: int) -> list[int]:
    events = []
    last_event = -(10 ** 9)
    for j in idx_true:
        if j - last_event > cooldown:
            events.append(int(j))
            last_event = j
    return events


def _horizon_stats(events_df: pd.DataFrame, h: int, min_events: int,
                   bootstrap_n: int, bootstrap_seed: int) -> dict:
    col_g, col_n = f"excess_gross_{h}", f"excess_net_{h}"
    sub = events_df.dropna(subset=[col_g, col_n])
    n = len(sub)
    if n < min_events:
        return {"count": n, "insufficient": True, "raw": None,
               "excess_gross": None, "excess_net": None,
               "bootstrap_ci_excess_net_mean": None, "event_dates": 0}

    def _dist(col: str) -> dict:
        s = sub[col]
        p5 = float(np.percentile(s, 5))
        worst = s[s <= p5]
        return {
            "mean": round(float(s.mean()), 4),
            "median": round(float(s.median()), 4),
            "hit_rate": round(float((s > 0).mean()), 4),
            "p5": round(p5, 4),
            "p95": round(float(np.percentile(s, 95)), 4),
            "worst5pct_mean": round(float(worst.mean()), 4)
                if len(worst) else None,
        }

    portfolio = sub.groupby("signal_date")[col_n].mean()
    rng = np.random.default_rng(bootstrap_seed)
    vals = portfolio.to_numpy()
    boots = rng.choice(vals, size=(bootstrap_n, len(vals)),
                       replace=True).mean(axis=1)
    ci = {"lo5": round(float(np.percentile(boots, 5)), 4),
         "hi95": round(float(np.percentile(boots, 95)), 4)}

    return {
        "count": n, "insufficient": False,
        "raw": {
            "event_gross_mean": round(float(sub[f"gross_{h}"].mean()), 4),
            "event_net_mean": round(float(sub[f"net_{h}"].mean()), 4),
            "baseline_mean": round(float(sub[f"baseline_{h}"].mean()), 4),
        },
        "excess_gross": _dist(col_g), "excess_net": _dist(col_n),
        "bootstrap_ci_excess_net_mean": ci,
        "event_dates": int(portfolio.shape[0]),
    }


def _events_to_records(events_df: pd.DataFrame) -> list[dict]:
    """Per-event rows for the API/UI. NaN (e.g. a horizon that ran past
    the panel's end for that event) must become JSON `null`, not a raw
    float `nan` — `json.dumps` rejects NaN outright, and it silently
    corrupted the very first live /api/backtest call this shipped with."""
    if events_df.empty:
        return []
    df = events_df.assign(signal_date=events_df["signal_date"].astype(str))
    df = df.astype(object).where(pd.notna(df), None)
    return df.to_dict("records")


# ------------------------------------------------------------ sensitivity grid
def _find_sensitivity_params(conditions: list[dict]
                             ) -> list[tuple[int, str, float]]:
    found = []
    for ci, c in enumerate(conditions):
        for key in SENSITIVITY_STEPS:
            if key in c and isinstance(c[key], (int, float)):
                found.append((ci, key, float(c[key])))
    return found


def _copy_screen_with_override(screen: dict, ci: int, key: str,
                               value: float) -> dict:
    s = _copy.deepcopy(screen)
    s["conditions"][ci][key] = value
    return s


def _sensitivity_grid(panels, universe, screen, *, benchmark,
                      min_turnover_cr, stride, cooldown, cost_pct
                      ) -> list[dict] | None:
    params = _find_sensitivity_params(screen["conditions"])
    if not params:
        return None

    base = backtest_spec(panels, universe, screen,
                         horizons=(SENSITIVITY_HORIZON,), cooldown=cooldown,
                         cost_pct=cost_pct, min_turnover_cr=min_turnover_cr,
                         stride=stride, sensitivity=False,
                         benchmark=benchmark)
    base_stats = base["horizons"][SENSITIVITY_HORIZON]
    base_mean = (base_stats["excess_net"]["mean"]
                if not base_stats["insufficient"] else None)

    grid = []
    for ci, key, base_val in params:
        cells = []
        for step in (-2, -1, 1, 2):
            new_val = base_val + step * SENSITIVITY_STEPS[key]
            variant = _copy_screen_with_override(screen, ci, key, new_val)
            try:
                r = backtest_spec(
                    panels, universe, variant,
                    horizons=(SENSITIVITY_HORIZON,), cooldown=cooldown,
                    cost_pct=cost_pct, min_turnover_cr=min_turnover_cr,
                    stride=stride, sensitivity=False, benchmark=benchmark)
                hs = r["horizons"][SENSITIVITY_HORIZON]
                cells.append({
                    "step": step, "value": round(new_val, 4),
                    "count": hs["count"],
                    "mean_excess_net": (hs["excess_net"]["mean"]
                                       if not hs["insufficient"] else None),
                })
            except (dsl.DSLValidationError, ValueError):
                cells.append({"step": step, "value": round(new_val, 4),
                             "error": True})

        means = [c["mean_excess_net"] for c in cells
                if c.get("mean_excess_net") is not None]
        robust = bool(means) and base_mean is not None and base_mean != 0 \
            and all((m > 0) == (base_mean > 0)
                    and abs(m) >= 0.25 * abs(base_mean) for m in means)
        verdict = ("robust across range" if robust else
                  "edge concentrated at one value — treat as curve-fit")
        grid.append({"condition_index": ci, "param": key,
                    "base_value": round(base_val, 4), "cells": cells,
                    "verdict": verdict})
    return grid


# ------------------------------------------------------------ entry point
def backtest_spec(panels: dict[str, pd.DataFrame],
                  universe: pd.DataFrame | None, screen: dict, *,
                  horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                  cooldown: int = DEFAULT_COOLDOWN,
                  cost_pct: float = DEFAULT_COST_PCT,
                  min_turnover_cr: float = config.MIN_MEDIAN_TURNOVER_CR,
                  stride: int = DEFAULT_STRIDE,
                  min_events: int = MIN_EVENTS,
                  bootstrap_n: int = BOOTSTRAP_N,
                  bootstrap_seed: int = BOOTSTRAP_SEED,
                  hypothesis: str | None = None,
                  benchmark: pd.Series | None = None,
                  sensitivity: bool = True) -> dict:
    """(panels, universe, spec) -> events + per-horizon stats + sensitivity
    grid + survivorship caveat. Pure function, no I/O. `screen` is
    re-validated defensively since a caller-supplied dict may not have
    gone through dsl.validate yet."""
    t0 = time.perf_counter()
    screen = dsl.validate(_copy.deepcopy(screen))
    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else pd.Series(dtype=str))
    symbols = list(panels.keys())

    windows_needed = {int(c.get("window", 63))
                      for c in screen["conditions"]
                      if c["type"] in EXPENSIVE_CROSS_TYPES}
    dates_grid, cs_grid = None, {}
    if windows_needed:
        all_dates = _common_dates(panels)
        dates_grid = all_dates[::stride]
        if len(all_dates) and dates_grid[-1] != all_dates[-1]:
            dates_grid = dates_grid.append(pd.DatetimeIndex([all_dates[-1]]))
        for d in dates_grid:
            d_str = str(pd.Timestamp(d).date())
            for w in windows_needed:
                cs_grid[(d, w)] = cs_mod.build_cross_section(
                    panels, universe, d_str, w)

    signals: dict[str, pd.Series] = {}
    for sym in symbols:
        panel = panels[sym]
        try:
            sig = _symbol_signal(panel, screen, symbol=sym,
                                sector_by_symbol=sector_by_symbol,
                                benchmark=benchmark, stride=stride,
                                dates_grid=dates_grid, cs_grid=cs_grid)
        except RuntimeError:
            continue
        liq = _liquidity_series(panel, min_turnover_cr)
        signals[sym] = (sig & liq).fillna(False)

    fwd = {h: {sym: _fwd_return_series(panels[sym], h) for sym in symbols}
          for h in horizons}
    liq_all = {sym: _liquidity_series(panels[sym], min_turnover_cr)
              for sym in symbols}
    baseline = {}
    for h in horizons:
        cols = {sym: fwd[h][sym].where(liq_all[sym]) for sym in symbols}
        baseline[h] = pd.concat(cols, axis=1, sort=True).mean(
            axis=1, skipna=True)

    events = []
    for sym, sig in signals.items():
        if not sig.any():
            continue
        panel = panels[sym]
        idx_true = np.flatnonzero(sig.to_numpy())
        for t in _dedup_events(idx_true, cooldown):
            if t + 1 >= len(panel):
                continue
            entry = panel["open"].iloc[t + 1]
            if pd.isna(entry) or entry <= 0:
                continue
            date = panel.index[t]
            row = {"symbol": sym, "signal_date": date}
            for h in horizons:
                fh = fwd[h][sym]
                gross = fh.iloc[t] if t < len(fh) else np.nan
                net = gross - cost_pct / 100 if pd.notna(gross) else np.nan
                b = baseline[h].get(date, np.nan)
                row[f"gross_{h}"] = gross
                row[f"net_{h}"] = net
                row[f"baseline_{h}"] = b
                row[f"excess_gross_{h}"] = (gross - b
                    if pd.notna(gross) and pd.notna(b) else np.nan)
                row[f"excess_net_{h}"] = (net - b
                    if pd.notna(net) and pd.notna(b) else np.nan)
            events.append(row)

    events_df = pd.DataFrame(events)
    horizon_stats = {}
    for h in horizons:
        if events_df.empty:
            horizon_stats[h] = {"count": 0, "insufficient": True,
                               "raw": None, "excess_gross": None,
                               "excess_net": None,
                               "bootstrap_ci_excess_net_mean": None,
                               "event_dates": 0}
        else:
            horizon_stats[h] = _horizon_stats(
                events_df, h, min_events, bootstrap_n, bootstrap_seed)

    timeline = {}
    if not events_df.empty:
        months = events_df["signal_date"].dt.to_period("M").astype(str)
        timeline = months.value_counts().sort_index().to_dict()

    result = {
        "spec_hash": dsl.spec_hash(screen),
        "english": dsl.describe(screen),
        "hypothesis": hypothesis,
        "survivorship_note": SURVIVORSHIP_NOTE,
        "cooldown": cooldown, "cost_pct": cost_pct,
        "min_turnover_cr": min_turnover_cr, "stride": stride,
        "n_symbols": len(symbols), "n_events_total": int(len(events_df)),
        "horizons": horizon_stats,
        "event_timeline": timeline,
        "events": _events_to_records(events_df),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    if sensitivity:
        result["sensitivity"] = _sensitivity_grid(
            panels, universe, screen, benchmark=benchmark,
            min_turnover_cr=min_turnover_cr, stride=stride,
            cooldown=cooldown, cost_pct=cost_pct)
    return result


# ------------------------------------------------------------ acceptance test
def verify_vectorizer_consistency(panels: dict[str, pd.DataFrame],
                                  universe: pd.DataFrame | None,
                                  screen: dict, *, n_samples: int = 200,
                                  seed: int = 42,
                                  stride: int = DEFAULT_STRIDE,
                                  benchmark: pd.Series | None = None
                                  ) -> dict:
    """Sample (symbol, date) pairs and assert the vectorized signal path
    matches `evaluator.evaluate_symbol()` exactly — the "CRITICAL
    acceptance test" from ROADMAP Item 14: the backtester and the live
    screener must never disagree about whether a signal fired.

    For specs containing an expensive/stride condition type, the dates
    sampled are restricted to the stride grid itself — exactness is only
    guaranteed there (that's the documented approximation), so this
    tests the guarantee actually made, not a stronger one no one
    promised."""
    screen = dsl.validate(_copy.deepcopy(screen))
    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else pd.Series(dtype=str))
    has_expensive = any(
        c["type"] in EXPENSIVE_SYMBOL_TYPES | EXPENSIVE_CROSS_TYPES
        for c in screen["conditions"])
    windows_needed = {int(c.get("window", 63))
                      for c in screen["conditions"]
                      if c["type"] in EXPENSIVE_CROSS_TYPES}
    dates_grid, cs_grid = None, {}
    if windows_needed:
        all_dates = _common_dates(panels)
        dates_grid = all_dates[::stride]
        for d in dates_grid:
            d_str = str(pd.Timestamp(d).date())
            for w in windows_needed:
                cs_grid[(d, w)] = cs_mod.build_cross_section(
                    panels, universe, d_str, w)

    rng = random.Random(seed)
    symbols = [s for s in panels if len(panels[s]) >= 260]
    mismatches, checked, attempts = [], 0, 0
    while checked < n_samples and attempts < n_samples * 50 and symbols:
        attempts += 1
        sym = rng.choice(symbols)
        panel = panels[sym]
        if has_expensive:
            if dates_grid is not None and len(dates_grid):
                date = rng.choice(list(dates_grid))
                if date not in panel.index:
                    continue
            else:
                positions = list(range(0, len(panel), stride))
                date = panel.index[rng.choice(positions)]
        else:
            i = rng.randrange(200, len(panel))
            date = panel.index[i]

        sig = _symbol_signal(panel, screen, symbol=sym,
                            sector_by_symbol=sector_by_symbol,
                            benchmark=benchmark, stride=stride,
                            dates_grid=dates_grid, cs_grid=cs_grid)
        vec_val = bool(sig.loc[date])
        as_of = str(date.date())
        _sec_ctx, cross_ctx = evaluator._cross_sectional_context(
            screen, panels, universe, as_of)
        eval_val = evaluator.evaluate_symbol(
            panel, screen, as_of, benchmark=benchmark, symbol=sym,
            sector_by_symbol=sector_by_symbol, cross_section=cross_ctx)
        checked += 1
        if vec_val != eval_val:
            mismatches.append({"symbol": sym, "date": as_of,
                              "vectorized": vec_val, "evaluator": eval_val})
    return {"checked": checked, "mismatches": mismatches}
