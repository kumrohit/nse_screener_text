"""Preset screen library — curated, named DSL specs.

Exposed as a dropdown in the web UI (GET /api/presets), and in the CLI via
`screener presets` / `screener screen --preset <id>`. Every preset is
validated at import time, so a DSL change that breaks a preset fails tests
immediately rather than surfacing as a runtime error in the UI.
"""
from __future__ import annotations

from . import dsl

PRESETS: list[dict] = [
    {
        "id": "support_50ema_uptrend",
        "name": "Support at 50 EMA in uptrend",
        "group": "Trend continuation",
        "description": "Pullback to the 50 EMA that held on a closing "
                       "basis, within an established uptrend.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "support_at_ma", "ma": "ema_50",
             "tolerance_pct": 1.5, "lookback": 3},
            {"type": "trend", "direction": "up"}]},
    },
    {
        "id": "support_20ema_strong_trend",
        "name": "Shallow pullback to 20 EMA (strong trend)",
        "group": "Trend continuation",
        "description": "ADX-confirmed trend with a shallow dip to the "
                       "20 EMA — momentum leaders resting.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "range", "field": "adx", "min": 25},
            {"type": "support_at_ma", "ma": "ema_20",
             "tolerance_pct": 1.5, "lookback": 3},
            {"type": "trend", "direction": "up"}]},
    },
    {
        "id": "golden_cross",
        "name": "Golden cross",
        "group": "Trend change",
        "description": "EMA50 crossed above EMA200 within the last week.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "cross", "fast": "ema_50", "slow": "ema_200",
             "direction": "above", "lookback": 5}]},
    },
    {
        "id": "weekly_up_daily_dip",
        "name": "Weekly uptrend, daily dip",
        "group": "Trend continuation",
        "description": "Multi-timeframe: higher-timeframe trend intact, "
                       "short-term oversold.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "trend", "direction": "up", "timeframe": "weekly"},
            {"type": "range", "field": "rsi", "max": 40}]},
    },
    {
        "id": "breakout_volume",
        "name": "Resistance breakout on volume",
        "group": "Breakouts",
        "description": "Close above a prior multi-touch swing resistance "
                       "with expanded volume.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "breakout_resistance", "lookback": 5},
            {"type": "volume_spike", "min_ratio": 1.5}]},
    },
    {
        "id": "flat_base_52w",
        "name": "Flat base near 52-week high",
        "group": "Breakouts",
        "description": "Extended tight range close to the highs — the "
                       "classic pre-breakout structure. Watch, don't chase.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "flat_base", "bars": 20, "max_range_pct": 12,
             "max_from_52w_high_pct": 15},
            {"type": "trend", "direction": "up"}]},
    },
    {
        "id": "nr7_squeeze",
        "name": "NR7 inside a volatility squeeze",
        "group": "Breakouts",
        "description": "Narrowest range in 7 bars while Bollinger "
                       "bandwidth sits in its bottom quintile — coiled "
                       "for expansion (direction unknown).",
        "spec": {"logic": "AND", "conditions": [
            {"type": "candle", "pattern": "nr7"},
            {"type": "bb_squeeze", "percentile": 20, "lookback": 252}]},
    },
    {
        "id": "inside_bar_uptrend",
        "name": "Inside bar in uptrend",
        "group": "Breakouts",
        "description": "One-bar consolidation within a trend; break of "
                       "the inside bar's range is the trigger.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "candle", "pattern": "inside_bar"},
            {"type": "trend", "direction": "up"}]},
    },
    {
        "id": "hammer_at_support",
        "name": "Hammer at swing support",
        "group": "Reversals",
        "description": "Rejection candle printed at a multi-touch "
                       "horizontal support level.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "candle", "pattern": "hammer", "lookback": 2},
            {"type": "near_support", "tolerance_pct": 3.0}]},
    },
    {
        "id": "engulfing_washout",
        "name": "Bullish engulfing after washout",
        "group": "Reversals",
        "description": "Engulfing reversal candle with RSI still "
                       "depressed — early mean-reversion signal.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "candle", "pattern": "bullish_engulfing",
             "lookback": 2},
            {"type": "range", "field": "rsi", "max": 40}]},
    },
    {
        "id": "oversold_quality",
        "name": "Oversold above the 200 EMA",
        "group": "Reversals",
        "description": "RSI washed out but the long-term trend is "
                       "intact — dips in leaders, not falling knives.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "range", "field": "rsi", "max": 30},
            {"type": "compare", "left": "close", "op": ">",
             "right": "ema_200"}]},
    },
    {
        "id": "near_support_outperformer",
        "name": "At support, beating the Nifty",
        "group": "Relative strength",
        "description": "Sitting on horizontal support while quietly "
                       "outperforming the index over the quarter.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "near_support", "tolerance_pct": 2.5},
            {"type": "rel_strength", "window": 63, "op": ">",
             "value_pct": 0}]},
    },
    {
        "id": "high_momentum_52w",
        "name": "Momentum near 52-week high",
        "group": "Relative strength",
        "description": "Strong-trend names within striking distance of "
                       "new highs.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "range", "field": "pct_from_52w_high", "min": -5},
            {"type": "range", "field": "adx", "min": 25},
            {"type": "rel_strength", "window": 63, "op": ">",
             "value_pct": 0}]},
    },
    {
        "id": "distribution_watch",
        "name": "Distribution watch (bearish)",
        "group": "Bearish",
        "description": "Downtrend rallying into swing resistance — "
                       "short-side or exit-timing screen.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "trend", "direction": "down"},
            {"type": "near_resistance", "tolerance_pct": 2.5}]},
    },
    {
        "id": "sector_leader_pullback",
        "name": "Pullback in a leading sector",
        "group": "Relative strength",
        "description": "50 EMA support inside a sector that's among the "
                       "top 3 by 3-month equal-weight momentum — buying "
                       "strength, not hope.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "sector_rank", "window": 63, "top": 3},
            {"type": "support_at_ma", "ma": "ema_50",
             "tolerance_pct": 1.5, "lookback": 3}]},
    },
    {
        "id": "rs_leader_near_high",
        "name": "RS leader near 52-week high",
        "group": "Relative strength",
        "description": "Top-quintile 3-month relative strength, still "
                       "within striking distance of new highs.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "rs_percentile", "window": 63, "op": ">=",
             "value": 80},
            {"type": "range", "field": "pct_from_52w_high", "min": -5}]},
    },
    {
        "id": "lagging_sector_bounce",
        "name": "Bounce in a lagging sector (contrarian)",
        "group": "Bearish",
        "description": "Contrarian/mean-reversion only: a hammer reversal "
                       "in a sector among the bottom 3 by momentum. Not a "
                       "trend-following signal — the sector is weak by "
                       "construction; this is a bet on a short-term bounce.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "sector_rank", "window": 63, "bottom": 3},
            {"type": "candle", "pattern": "hammer", "lookback": 2}]},
    },
]

# fail fast: an invalid preset is a bug, not a runtime condition
_BY_ID = {}
for _p in PRESETS:
    dsl.validate(_p["spec"])
    _BY_ID[_p["id"]] = _p


def get(preset_id: str) -> dict:
    if preset_id not in _BY_ID:
        raise KeyError(
            f"unknown preset '{preset_id}'; available: "
            + ", ".join(sorted(_BY_ID)))
    return _BY_ID[preset_id]
