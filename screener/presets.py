"""Preset screen library — curated, named DSL specs.

Exposed as a dropdown in the web UI (GET /api/presets), and in the CLI via
`screener presets` / `screener screen --preset <id>`. Every preset is
validated at import time, so a DSL change that breaks a preset fails tests
immediately rather than surfacing as a runtime error in the UI.

Every preset carries an `evidence` object (ROADMAP Item 9):
    {"basis": "academic" | "practitioner" | "mixed",
     "sources": [short citation strings],
     "finding": "one-line summary of what the evidence says",
     "caveat": "the honest limitation, stated plainly"}
Full citations, magnitude, and India-specific evidence live in
LITERATURE.md — this module points back to it, not the reverse. A preset
with no dedicated academic study says so directly rather than inventing
one; `"sources": []` is a legitimate, honest value.
"""
from __future__ import annotations

from . import dsl, universes
from .evaluator import SECTOR_DEPENDENT_TYPES

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
        "evidence": {
            "basis": "mixed",
            "sources": ["Moskowitz, Ooi & Pedersen (2012)"],
            "finding": "The trend filter is grounded in time-series "
                       "momentum (LITERATURE.md §3); the specific "
                       "'pullback to a moving average, held on a closing "
                       "basis' entry timing is a standard practitioner "
                       "overlay with no dedicated academic study of its "
                       "own.",
            "caveat": "No backtest of this exact pullback-entry "
                      "construction has been run against a control group "
                      "(screen backtesting is parked — ROADMAP §3).",
        },
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
        "evidence": {
            "basis": "mixed",
            "sources": ["Moskowitz, Ooi & Pedersen (2012)"],
            "finding": "Same trend grounding as support_50ema_uptrend, "
                       "with ADX added as a practitioner trend-strength "
                       "filter (no dedicated academic study of the ADX "
                       "threshold used here).",
            "caveat": "Shallower pullback + trend-strength filter is a "
                      "narrower screen; not independently validated.",
        },
    },
    {
        "id": "golden_cross",
        "name": "Golden cross",
        "group": "Trend change",
        "description": "EMA50 crossed above EMA200 within the last week.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "cross", "fast": "ema_50", "slow": "ema_200",
             "direction": "above", "lookback": 5}]},
        "evidence": {
            "basis": "mixed",
            "sources": ["Brock, Lakonishok & LeBaron (1992)",
                       "Sullivan, Timmermann & White (1999)"],
            "finding": "Moving-average crossover systems are the class "
                       "BLL (1992) tested and found predictive in-sample "
                       "(LITERATURE.md §4).",
            "caveat": "Sullivan, Timmermann & White's data-snooping "
                      "critique applies directly to crossover rules — "
                      "after-cost, out-of-sample profitability is "
                      "contested. The specific 50/200-day pairing is "
                      "folklore convention, not the exact rule tested.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Multi-timeframe trend/pullback combination; no "
                       "dedicated academic study of this exact "
                       "weekly-trend + daily-RSI construction.",
            "caveat": "Each component (weekly trend, RSI oversold) is a "
                      "standard technical construction; their combination "
                      "here is not independently backtested.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Classic breakout-on-volume construction; no "
                       "dedicated academic study of this exact "
                       "swing-resistance definition reviewed for this "
                       "document.",
            "caveat": "Multi-touch S/R levels are a standard charting "
                      "construction, not a statistically validated one.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Consolidation-breakout pattern (LITERATURE.md "
                       "§8) — widely used, no controlled academic study "
                       "establishing predictive power for this specific "
                       "range/proximity construction.",
            "caveat": "Explicitly labeled unvalidated in LITERATURE.md — "
                      "pattern-based setups are vulnerable to "
                      "look-elsewhere bias in informal review.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Volatility-contraction pattern; no dedicated "
                       "academic study reviewed for this document.",
            "caveat": "Direction-agnostic by design — a squeeze predicts "
                      "expansion, not which way. Treat as a watchlist "
                      "trigger, not a directional signal.",
        },
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
        "evidence": {
            "basis": "mixed",
            "sources": ["Moskowitz, Ooi & Pedersen (2012)"],
            "finding": "Trend filter grounded in time-series momentum "
                       "(LITERATURE.md §3); the inside-bar entry pattern "
                       "itself has no dedicated academic study reviewed "
                       "here.",
            "caveat": "Candlestick pattern efficacy is a generally "
                      "contested area of the literature; not verified "
                      "for this exact pattern in this document.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Classic candlestick reversal pattern at a "
                       "charting support level; no dedicated academic "
                       "study reviewed for this document.",
            "caveat": "Candlestick pattern predictive power is generally "
                      "contested in the academic literature net of "
                      "trading costs.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Candlestick reversal + RSI oversold combination; "
                       "no dedicated academic study reviewed for this "
                       "document.",
            "caveat": "Same candlestick-efficacy caveat as "
                      "hammer_at_support.",
        },
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
        "evidence": {
            "basis": "mixed",
            "sources": ["Moskowitz, Ooi & Pedersen (2012)"],
            "finding": "The 'above the 200 EMA' quality filter is the "
                       "same regime-filter grounding as family 3 "
                       "(LITERATURE.md); RSI-oversold as a mean-reversion "
                       "trigger within that regime has no dedicated "
                       "academic study reviewed here.",
            "caveat": "Short-term RSI-based mean-reversion signals are "
                      "not covered by the vetted literature list in "
                      "LITERATURE.md.",
        },
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
        "evidence": {
            "basis": "mixed",
            "sources": ["Jegadeesh & Titman (1993)"],
            "finding": "The relative-strength-vs-benchmark leg is loosely "
                       "grounded in cross-sectional momentum "
                       "(LITERATURE.md §1) but uses a raw excess-return "
                       "threshold rather than a cross-sectional "
                       "percentile rank or the 12-1 skip-month "
                       "construction — see momentum_12_1_leaders for the "
                       "literature's exact construction.",
            "caveat": "The support-level component has no academic "
                      "grounding reviewed in this document.",
        },
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
        "evidence": {
            "basis": "academic",
            "sources": ["George & Hwang (2004)"],
            "finding": "Directly implements the 52-week-high anchoring "
                       "effect (LITERATURE.md §2): proximity to the "
                       "52-week high predicts continued outperformance, "
                       "distinct from trailing-return momentum. ADX and "
                       "the relative-strength floor are added trend/RS "
                       "confirmation on top of the core academic signal.",
            "caveat": "George & Hwang's sample is US equities 1963-2001 — "
                      "not independently confirmed in Indian data at "
                      "time of writing.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Mirror-image technical construction (downtrend + "
                       "resistance rejection); no dedicated academic "
                       "study reviewed for this document.",
            "caveat": "Bearish/short-side signals carry the same "
                      "unvalidated-pattern caveats as the long-side "
                      "practitioner presets above.",
        },
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
        "evidence": {
            "basis": "mixed",
            "sources": ["Jegadeesh & Titman (1993)"],
            "finding": "Sector-level equal-weight momentum ranking is a "
                       "mechanical extension of the cross-sectional "
                       "momentum construction (LITERATURE.md §1) applied "
                       "to sector aggregates rather than individual "
                       "stocks; this specific extension is not "
                       "independently validated in the literature "
                       "reviewed.",
            "caveat": "Equal-weight sector construction is an "
                      "approximation — no free-float/cap-weighted sector "
                      "index data is available to this screener.",
        },
    },
    {
        "id": "rs_leader_near_high",
        "name": "RS leader near 52-week high",
        "group": "Relative strength",
        "description": "Top-quintile 3-month relative strength, still "
                       "within striking distance of new highs.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "rs_percentile", "window": 63, "basis": "return",
             "op": ">=", "value": 80},
            {"type": "range", "field": "pct_from_52w_high", "min": -5}]},
        "evidence": {
            "basis": "mixed",
            "sources": ["Jegadeesh & Titman (1993)", "George & Hwang (2004)"],
            "finding": "Combines a window-based relative-strength "
                       "percentile ranking — a practitioner variant of "
                       "the academic cross-sectional momentum "
                       "construction, using a raw window return rather "
                       "than the literature's 12-1 skip-month "
                       "construction (see momentum_12_1_leaders) — with "
                       "George-Hwang 52-week-high proximity "
                       "(LITERATURE.md §2).",
            "caveat": "The RS leg is not the exact academic construction; "
                      "treat as a practitioner variant grounded in, but "
                      "not identical to, family 1.",
        },
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
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Contrarian sector + candlestick combination; no "
                       "dedicated academic study reviewed for this "
                       "document.",
            "caveat": "Deliberately contrarian to the momentum evidence "
                      "in LITERATURE.md §1 — a bet that a specific "
                      "reversal pattern overrides sector-level momentum "
                      "persistence, which is the higher-evidence default.",
        },
    },
    {
        "id": "weekly_squeeze",
        "name": "Weekly uptrend, daily volatility squeeze",
        "group": "Breakouts",
        "description": "Higher-timeframe trend intact while the daily "
                       "chart coils into its tightest Bollinger bandwidth "
                       "in a year — a base building inside a bigger "
                       "uptrend, not a standalone squeeze.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "trend", "direction": "up", "timeframe": "weekly"},
            {"type": "bb_squeeze", "percentile": 20, "lookback": 252}]},
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Multi-timeframe trend + volatility-contraction "
                       "combination; no dedicated academic study "
                       "reviewed for this document.",
            "caveat": "Same direction-agnostic-squeeze caveat as "
                      "nr7_squeeze.",
        },
    },
    {
        "id": "gap_up_followthrough",
        "name": "Gap-up with volume follow-through",
        "group": "Breakouts",
        "description": "A recent gap-up (open ≥2% above the prior close) "
                       "confirmed by expanded volume and an intact "
                       "uptrend — the gap wasn't a one-day wonder.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 5},
            {"type": "volume_spike", "min_ratio": 1.5},
            {"type": "trend", "direction": "up"}]},
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Gap-and-go continuation pattern; no dedicated "
                       "academic study reviewed for this document.",
            "caveat": "Not to be confused with post-earnings-announcement "
                      "drift (an academic effect) — this screener has no "
                      "earnings-date data and cannot distinguish an "
                      "earnings gap from any other gap.",
        },
    },

    # ------------------------------------------------------ v0.9 additions
    # ROADMAP Item 9 — see LITERATURE.md for the full review each of these
    # points back to.
    {
        "id": "momentum_12_1_leaders",
        "name": "12-1 momentum leaders",
        "group": "Relative strength",
        "description": "Top-quintile 12-month momentum, most recent "
                       "month excluded (the academic skip-month "
                       "construction), with an extra liquidity floor so "
                       "the ranking isn't led by thinly traded names.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "rs_percentile", "basis": "mom_12_1", "op": ">=",
             "value": 80},
            {"type": "range", "field": "turnover_cr", "min": 5}]},
        "evidence": {
            "basis": "academic",
            "sources": ["Jegadeesh & Titman (1993)", "Jegadeesh (1990)",
                       "Sehgal & Balakrishnan (2002)"],
            "finding": "Directly implements cross-sectional momentum's "
                       "canonical 12-1 skip-month construction "
                       "(LITERATURE.md §1), replicated in Indian equities "
                       "by Sehgal & Balakrishnan (2002).",
            "caveat": "Momentum crashes (Daniel & Moskowitz 2016) — this "
                      "screener is long-only with no volatility scaling "
                      "or crash-mitigation overlay.",
        },
    },
    {
        "id": "near_52w_high_ghw",
        "name": "52-week-high anchoring (GHW)",
        "group": "Relative strength",
        "description": "Close within 5% of the 52-week high with a "
                       "relative-strength floor — the George-Hwang "
                       "signal in its more direct form (no ADX filter).",
        "spec": {"logic": "AND", "conditions": [
            {"type": "range", "field": "pct_from_52w_high", "min": -5},
            {"type": "rs_percentile", "window": 63, "basis": "return",
             "op": ">=", "value": 60}]},
        "evidence": {
            "basis": "academic",
            "sources": ["George & Hwang (2004)"],
            "finding": "52-week-high proximity predicts continued "
                       "outperformance, and does so distinctly from "
                       "trailing-return momentum (LITERATURE.md §2).",
            "caveat": "Not independently confirmed in Indian data at "
                      "time of writing; developed-market sample "
                      "(1963-2001).",
        },
    },
    {
        "id": "tsmom_regime",
        "name": "Time-series momentum regime",
        "group": "Trend continuation",
        "description": "Price above its 200-day average with a positive "
                       "12-month return — the raw time-series momentum "
                       "signal, not a pullback-entry variant.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "compare", "left": "close", "op": ">",
             "right": "sma_200"},
            {"type": "range", "field": "roc_252", "min": 0}]},
        "evidence": {
            "basis": "academic",
            "sources": ["Moskowitz, Ooi & Pedersen (2012)", "Faber (2007)"],
            "finding": "Direct implementation: positive trailing "
                       "12-month return, confirmed by price above a "
                       "long moving average (Faber's practitioner "
                       "long-form of the same effect — LITERATURE.md §3).",
            "caveat": "Slow to react by construction (200-day filter); "
                      "whipsaw risk in choppy, range-bound markets.",
        },
    },
    {
        "id": "ma_timing_highvol",
        "name": "MA-timing in high-volatility names",
        "group": "Trend continuation",
        "description": "Uptrend filter restricted to the top-volatility "
                       "tercile of the universe, where moving-average "
                       "timing rules were found most profitable.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "atr_pct_percentile", "op": ">=", "value": 70}]},
        "evidence": {
            "basis": "mixed",
            "sources": ["Brock, Lakonishok & LeBaron (1992)",
                       "Han, Yang & Zhou (2013)",
                       "Sullivan, Timmermann & White (1999)"],
            "finding": "Han, Yang & Zhou found MA-rule profitability "
                       "concentrated in high-idiosyncratic-volatility "
                       "stocks (LITERATURE.md §4) — this screen applies "
                       "the existing trend filter only to that subset.",
            "caveat": "The Sullivan-Timmermann-White data-snooping "
                      "critique of MA rules generally still applies; "
                      "high-volatility names also carry higher drawdown "
                      "risk mechanically.",
        },
    },
    {
        "id": "volume_momentum",
        "name": "Volume-confirmed momentum",
        "group": "Relative strength",
        "description": "Strong relative strength with a rising volume "
                       "ratio — the 'attention-confirmed' half of the "
                       "Lee-Swaminathan volume/momentum interaction.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "rs_percentile", "window": 63, "basis": "return",
             "op": ">=", "value": 70},
            {"type": "volume_spike", "min_ratio": 1.3}]},
        "evidence": {
            "basis": "academic",
            "sources": ["Lee & Swaminathan (2000)"],
            "finding": "Volume predicts momentum duration and magnitude; "
                       "this implements the simpler, more conservative "
                       "'attention-confirmed continuation' slice of the "
                       "paper's volume/momentum interaction matrix "
                       "(LITERATURE.md §5), not the full "
                       "high-volume-winner/low-volume-loser matrix.",
            "caveat": "Volume is a moderating signal in the original "
                      "study, not a stand-alone one — this preset "
                      "requires both legs, consistent with that.",
        },
    },
    {
        "id": "lowvol_defensive",
        "name": "Low-volatility defensive",
        "group": "Reversals",
        "description": "Bottom-tercile realized volatility with an "
                       "intact long-term trend — a defensive bucket, not "
                       "a momentum bet.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "atr_pct_percentile", "op": "<=", "value": 30},
            {"type": "compare", "left": "close", "op": ">",
             "right": "sma_200"}]},
        "evidence": {
            "basis": "academic",
            "sources": ["Blitz & van Vliet (2007)",
                       "Ang, Hodrick, Xing & Zhang (2006)"],
            "finding": "Low-volatility stocks earn superior "
                       "risk-adjusted returns — the 'volatility puzzle' "
                       "(LITERATURE.md §6). The trend filter avoids "
                       "surfacing low-volatility names that are simply "
                       "in a slow bleed.",
            "caveat": "Can lag badly in strong bull-market momentum "
                      "regimes by construction; not independently "
                      "confirmed in Indian data in this review.",
        },
    },
    {
        "id": "minervini_stage2",
        "name": "Minervini stage 2 template",
        "group": "Trend continuation",
        "description": "The full practitioner trend template: price "
                       "stacked above rising 50/150/200-day averages, "
                       "well clear of the 52-week low, within range of "
                       "the 52-week high, with a relative-strength floor.",
        "spec": {"logic": "AND", "conditions": [
            {"type": "compare", "left": "close", "op": ">",
             "right": "sma_50"},
            {"type": "compare", "left": "sma_50", "op": ">",
             "right": "sma_150"},
            {"type": "compare", "left": "sma_150", "op": ">",
             "right": "sma_200"},
            {"type": "compare", "left": "sma_200_slope", "op": ">",
             "right": 0},
            {"type": "range", "field": "pct_from_52w_low", "min": 30},
            {"type": "range", "field": "pct_from_52w_high", "min": -25},
            {"type": "rs_percentile", "window": 63, "basis": "return",
             "op": ">=", "value": 70}]},
        "evidence": {
            "basis": "practitioner",
            "sources": [],
            "finding": "Full Minervini/O'Neil 'stage 2' trend template "
                       "(LITERATURE.md §7) — explicitly practitioner, "
                       "weak academic support, real survivorship-bias "
                       "risk in its supporting case-study folklore.",
            "caveat": "'sma_200 rising' reuses this codebase's existing "
                      "5-bar-slope convention (same as the `trend` "
                      "condition's EMA50 slope check), which is shorter "
                      "than the ~1-month rise Minervini's template "
                      "specifies — a documented approximation, not the "
                      "literal original rule. No peer-reviewed "
                      "out-of-sample test of this exact multi-part "
                      "conjunction is known to the author of "
                      "LITERATURE.md.",
        },
    },
]

# Which universes a preset is meaningful on (ROADMAP Item 15 follow-up).
# Computed from the spec, not hand-tagged per preset: a preset that uses
# a sector/sector_rank condition only makes sense on a universe that
# actually carries sector/industry data (today: nifty500 only — nse_full's
# raw NSE listing has none, see evaluator.sector_data_gap_warning). This
# way a newly-added preset is tagged correctly automatically instead of
# relying on someone remembering to set a field by hand.
_SECTOR_ONLY_UNIVERSES = (universes.DEFAULT_UNIVERSE,)


def _preset_universes(spec: dict) -> list[str]:
    uses_sector = any(c.get("type") in SECTOR_DEPENDENT_TYPES
                      for c in spec.get("conditions", []))
    ids = _SECTOR_ONLY_UNIVERSES if uses_sector else tuple(universes.UNIVERSES)
    return list(ids)


# fail fast: an invalid preset is a bug, not a runtime condition
_BY_ID = {}
for _p in PRESETS:
    dsl.validate(_p["spec"])
    _p["universes"] = _preset_universes(_p["spec"])
    _BY_ID[_p["id"]] = _p


def get(preset_id: str) -> dict:
    if preset_id not in _BY_ID:
        raise KeyError(
            f"unknown preset '{preset_id}'; available: "
            + ", ".join(sorted(_BY_ID)))
    return _BY_ID[preset_id]
