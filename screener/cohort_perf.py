"""Cohort performance engine — window metrics for both forward and
replay cohorts (ROADMAP Item 17, v0.15).

One engine, two callers: a forward cohort's performance defaults its
window to entry_date -> latest bar; a replay cohort gets the exact same
computation with an already-historical, already-long window available
instantly. Nothing here treats the two modes differently — `mode` only
matters for the OOS-scorecard wall in `cohorts.py`, not here.

  Window            open[entry_date] -> close[end_date], end_date
                    resolved to the trading day at-or-before it (same
                    lenient semantics `evaluator._row_at` uses
                    elsewhere) and clamped to the latest bar in the
                    shared universe calendar. `end < entry+1` (no bar
                    strictly after entry) is pending, not zero — the
                    same "no returns until a bar exists" rule Item 16's
                    forward cohorts already use.
  Equity curves     Cohort, baseline (same liquidity-passing universe,
                    `backtest.daily_baseline_returns` — a sibling of
                    Item 16's `compute_baseline`, not a fork of it),
                    and Nifty, all indexed to 100 at entry_date. The
                    cohort's own curve uses open[entry_date] as the 100
                    point (matching the entry convention exactly);
                    baseline/Nifty are indexed to 100 at entry_date's
                    close, since neither has a single "entry price" the
                    way a traded position does — a documented, minor
                    day-0 asymmetry, not an error.
  Stale symbols     Same carry-last-close-forward rule as Item 16 — a
                    symbol that stops trading before end_date is
                    flagged `stale`, never dropped from the weighted
                    aggregate.
  Sharpe            Reported only when the window is >= 60 bars;
                    shorter windows return `None` with an explicit
                    reason rather than an annualised number nobody
                    should trust on a fortnight of data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import backtest
from . import cohorts as cohorts_mod

MIN_BARS_FOR_SHARPE = 60


def _resolve_window(panels: dict[str, pd.DataFrame], entry_date: str,
                    end_date: str | None
                    ) -> tuple[pd.DatetimeIndex, str] | None:
    """(window_dates, resolved_end_str), or None if pending — end_date
    resolves against the FULL universe calendar (not just the cohort's
    own symbols), matching Item 16's `_milestone_reached` reasoning:
    the shared calendar is what lets staleness be distinguished from
    "hasn't aged that far yet" once individual symbols drop out."""
    all_dates = backtest.common_dates(panels)
    entry_ts = pd.Timestamp(entry_date)
    if end_date is None:
        resolved_end = all_dates[-1]
    else:
        end_ts = pd.Timestamp(end_date)
        idx = all_dates[all_dates <= end_ts]
        if len(idx) == 0:
            return None
        resolved_end = idx[-1]
    if resolved_end <= entry_ts:
        return None
    window = all_dates[(all_dates >= entry_ts) & (all_dates <= resolved_end)]
    return window, str(resolved_end.date())


def _symbol_window(panel: pd.DataFrame, entry_date: str,
                   window_dates: pd.DatetimeIndex
                   ) -> tuple[float, pd.Series, bool] | None:
    """(entry_px, closes-reindexed-to-window-ffilled, stale) for one
    symbol, or None if it never reached entry_date at all."""
    t = cohorts_mod._signal_row(panel, entry_date)
    if t is None:
        return None
    entry_price = panel["open"].iloc[t + 1]
    if pd.isna(entry_price) or entry_price <= 0:
        return None
    closes = panel["close"].reindex(window_dates).ffill()
    if closes.isna().all():
        return None
    stale = panel.index[-1] < window_dates[-1]
    return float(entry_price), closes, stale


def _max_drawdown(curve: pd.Series) -> dict:
    cummax = curve.cummax()
    dd = curve / cummax - 1
    trough_date = dd.idxmin()
    trough_pct = float(dd.min())
    peak_date = curve.loc[:trough_date].idxmax()
    return {"pct": round(trough_pct, 4),
           "peak_date": str(peak_date.date()),
           "trough_date": str(trough_date.date())}


def evaluate_performance(cohort: dict, panels: dict[str, pd.DataFrame],
                         universe_symbols: list[str],
                         min_turnover_cr: float,
                         end_date: str | None = None,
                         benchmark: pd.Series | None = None,
                         cost_pct: float = backtest.DEFAULT_COST_PCT
                         ) -> dict | None:
    """The locked metric set (ROADMAP Item 17): cumulative return g/n,
    excess vs. the same-entry-date universe baseline and vs. Nifty,
    annualised vol, max drawdown with dates, hit rates, best/worst
    contributors, and an equity curve — computed identically for a
    forward cohort (default end = latest bar) or a replay cohort
    (any end up to data availability). Returns None if the cohort is
    still pending, or if `end_date` resolves to no evaluable window."""
    if cohort["status"] == cohorts_mod.STATUS_PENDING or cohort["entry_date"] is None:
        return None
    symbols = list(cohort["symbols"])
    weights = cohort["weights"]["by_symbol"]
    entry_date = cohort["entry_date"]

    resolved = _resolve_window(panels, entry_date, end_date)
    if resolved is None:
        return None
    window_dates, resolved_end = resolved

    per_symbol: dict[str, dict] = {}
    curves: dict[str, pd.Series] = {}
    weight_total = 0.0
    for sym in symbols:
        panel = panels.get(sym)
        if panel is None:
            continue
        sw = _symbol_window(panel, entry_date, window_dates)
        if sw is None:
            continue
        entry_px, closes, stale = sw
        end_px = float(closes.iloc[-1])
        if pd.isna(end_px):
            continue
        ret_gross = end_px / entry_px - 1
        ret_net = ret_gross - cost_pct / 100
        w = weights.get(sym, 0.0)
        weight_total += w
        sym_curve = 100 * closes / entry_px
        curves[sym] = sym_curve
        per_symbol[sym] = {
            "entry_px": round(entry_px, 2), "end_px": round(end_px, 2),
            "return_gross": round(ret_gross, 4),
            "return_net": round(ret_net, 4),
            "weight": w, "stale": stale,
            "max_drawdown": _max_drawdown(sym_curve),
        }
    if not curves or weight_total <= 0:
        return None

    # weighted cohort equity curve, renormalized to the symbols that
    # actually resolved (same renormalization Item 16's milestone
    # aggregate already uses when some symbols return None) — the set
    # of resolved symbols is fixed for the whole window (staleness only
    # changes which values are carried-forward, not membership), so one
    # fixed per-symbol multiplier applies across every date.
    frame = pd.DataFrame(curves)
    w_series = pd.Series({s: weights.get(s, 0.0) / weight_total
                          for s in curves})
    cohort_curve = frame.mul(w_series, axis=1).sum(axis=1)

    gross = float(cohort_curve.iloc[-1] / 100 - 1)
    net = gross - cost_pct / 100

    baseline_ret = backtest.daily_baseline_returns(
        panels, universe_symbols, min_turnover_cr)
    base_window = baseline_ret.reindex(window_dates).fillna(0.0)
    base_window.iloc[0] = 0.0  # entry_date is the "100" point, not a move
    baseline_curve = 100 * (1 + base_window).cumprod()
    baseline_gross = float(baseline_curve.iloc[-1] / 100 - 1)
    baseline_net = baseline_gross - cost_pct / 100

    nifty_curve = None
    nifty_gross = nifty_net = None
    if benchmark is not None:
        px = benchmark.reindex(window_dates).ffill().bfill()
        if px.notna().any():
            nifty_curve = 100 * px / px.iloc[0]
            nifty_gross = float(nifty_curve.iloc[-1] / 100 - 1)
            nifty_net = nifty_gross - cost_pct / 100

    daily_rets = cohort_curve.pct_change().dropna()
    n_bars = len(window_dates)
    vol = (float(daily_rets.std() * np.sqrt(252))
          if len(daily_rets) > 1 else None)
    if n_bars >= MIN_BARS_FOR_SHARPE and daily_rets.std():
        sharpe = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252))
        sharpe_note = None
    else:
        sharpe = None
        sharpe_note = "window too short (<60 bars)"

    max_dd = _max_drawdown(cohort_curve)

    for s, v in per_symbol.items():
        v["excess_gross"] = round(v["return_gross"] - baseline_gross, 4)
        v["excess_net"] = round(v["return_net"] - baseline_net, 4)
        # renormalized weight (w_series), not the raw stored weight, so
        # contributions sum exactly to the cohort's own gross return
        # even when some symbols didn't resolve and weight_total < 1.
        v["contribution_gross"] = round(
            float(w_series.get(s, 0.0)) * v["return_gross"], 4)

    symbol_rets = [v["return_gross"] for v in per_symbol.values()]
    hit_rate_positive = float(np.mean([r > 0 for r in symbol_rets]))
    hit_rate_vs_baseline = float(np.mean(
        [r > baseline_gross for r in symbol_rets]))

    contributors = sorted(
        ({"symbol": s, "weight": v["weight"],
          "contribution_gross": v["contribution_gross"],
          "return_gross": v["return_gross"],
          "max_drawdown_pct": v["max_drawdown"]["pct"]}
         for s, v in per_symbol.items()),
        key=lambda c: c["contribution_gross"], reverse=True)

    return {
        "entry_date": entry_date, "end_date": resolved_end,
        "n_bars": n_bars,
        "gross": round(gross, 4), "net": round(net, 4),
        "excess_gross_baseline": round(gross - baseline_gross, 4),
        "excess_net_baseline": round(net - baseline_net, 4),
        "excess_gross_nifty": (round(gross - nifty_gross, 4)
                               if nifty_gross is not None else None),
        "excess_net_nifty": (round(net - nifty_net, 4)
                             if nifty_net is not None else None),
        "annualized_vol": round(vol, 4) if vol is not None else None,
        "max_drawdown": max_dd,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sharpe_note": sharpe_note,
        "hit_rate_positive": round(hit_rate_positive, 4),
        "hit_rate_vs_baseline": round(hit_rate_vs_baseline, 4),
        "contributors": contributors,
        "per_symbol": per_symbol,
        "equity_curve": {
            "dates": [str(d.date()) for d in window_dates],
            "cohort": [round(float(v), 3) for v in cohort_curve.values],
            "baseline": [round(float(v), 3) for v in baseline_curve.values],
            "nifty": ([round(float(v), 3) for v in nifty_curve.values]
                     if nifty_curve is not None else None),
        },
    }
