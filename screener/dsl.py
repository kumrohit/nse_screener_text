"""Filter DSL — the contract between the NL parser and the evaluator.

A screen is a JSON object:

{
  "logic": "AND" | "OR",
  "conditions": [ <condition>, ... ],
  "as_of": "latest"            # ISO date supported for historical screens
}

Condition types (v1):

1. compare      — {"type":"compare","left":"close","op":">","right":"ema_50"}
                  right may be a field name or a number.
2. proximity    — {"type":"proximity","target":"low","ref":"ema_50",
                   "tolerance_pct":1.5,"lookback":3}
                  True if `target` came within tolerance of `ref` in the
                  last `lookback` bars.
3. trend        — {"type":"trend","direction":"up"|"down"}
                  Canonical definition (up): close > ema_50 > ema_200
                  AND ema_50 slope > 0. Down is the mirror.
4. support_at_ma — {"type":"support_at_ma","ma":"ema_50",
                    "tolerance_pct":1.5,"lookback":3}
                  Canonical "taking support": within the lookback window the
                  low touched the MA (within tolerance) while the close held
                  at/above it, and the latest close is above the MA.
5. cross        — {"type":"cross","fast":"ema_20","slow":"ema_50",
                   "direction":"above"|"below","lookback":3}
6. volume_spike — {"type":"volume_spike","min_ratio":1.5}
7. range        — {"type":"range","field":"rsi","min":40,"max":60}
8. change       — {"type":"change","field":"close","window":21,
                   "op":">","value_pct":10}

Every field name must exist in the indicator panel — validation fails loud
otherwise. This module also renders the compiled screen back to plain
English so the user can confirm the interpretation before trusting results.
"""
from __future__ import annotations

from typing import Any

VALID_OPS = {">", ">=", "<", "<="}

KNOWN_FIELDS = {
    "open", "high", "low", "close", "volume",
    "ema_10", "ema_20", "ema_50", "ema_100", "ema_200",
    "ema_10_slope", "ema_20_slope", "ema_50_slope", "ema_100_slope",
    "ema_200_slope",
    "sma_20", "sma_50", "sma_200",
    "rsi", "atr", "atr_pct", "adx", "plus_di", "minus_di",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_width_pct",
    "vol_avg_20", "vol_ratio", "turnover_cr",
    "high_52w", "low_52w", "pct_from_52w_high", "pct_from_52w_low",
    "roc_5", "roc_21", "roc_63",
}

CONDITION_TYPES = {
    "compare", "proximity", "trend", "support_at_ma", "cross",
    "volume_spike", "range", "change",
    "near_support", "near_resistance", "breakout_resistance",
    "rel_strength",
    "candle", "tight_range", "bb_squeeze", "flat_base",
    "sector", "rs_percentile", "sector_rank",
}

# Nifty 500 universe file's exact industry strings (NSE classification).
# `sector` conditions validate against this so an unmapped adjective fails
# loud instead of matching nothing silently.
KNOWN_SECTORS = {
    "Automobile and Auto Components", "Capital Goods", "Chemicals",
    "Construction", "Construction Materials", "Consumer Durables",
    "Consumer Services", "Diversified", "Fast Moving Consumer Goods",
    "Financial Services", "Healthcare", "Information Technology",
    "Media Entertainment & Publication", "Metals & Mining",
    "Oil Gas & Consumable Fuels", "Power", "Realty", "Services",
    "Telecommunication", "Textiles",
}

CANDLE_PATTERNS = {
    "inside_bar", "nr7", "bullish_engulfing", "bearish_engulfing",
    "hammer", "shooting_star",
}

# Fields available on the weekly panel (subset — see indicators.compute_weekly_panel)
WEEKLY_FIELDS = {
    "open", "high", "low", "close", "volume",
    "ema_10", "ema_20", "ema_40",
    "ema_10_slope", "ema_20_slope", "ema_40_slope",
    "rsi", "roc_4", "roc_13",
}
# Condition types that accept an optional "timeframe": "daily"|"weekly"
TIMEFRAME_AWARE = {"compare", "range", "trend", "change", "cross"}


class DSLValidationError(ValueError):
    pass


def _require(cond: dict, keys: list[str]) -> None:
    missing = [k for k in keys if k not in cond]
    if missing:
        raise DSLValidationError(
            f"condition {cond.get('type')}: missing keys {missing}")


def _check_field(name: Any, where: str) -> None:
    if not isinstance(name, str) or name not in KNOWN_FIELDS:
        raise DSLValidationError(
            f"{where}: unknown field '{name}'. "
            f"Must be one of the indicator panel columns.")


def validate(screen: dict) -> dict:
    if not isinstance(screen, dict):
        raise DSLValidationError("screen must be a JSON object")
    logic = screen.get("logic", "AND")
    if logic not in ("AND", "OR"):
        raise DSLValidationError(f"logic must be AND/OR, got {logic!r}")
    conds = screen.get("conditions")
    if not isinstance(conds, list) or not conds:
        raise DSLValidationError("conditions must be a non-empty list")

    for c in conds:
        ctype = c.get("type")
        if ctype not in CONDITION_TYPES:
            raise DSLValidationError(f"unknown condition type {ctype!r}")

        tf = c.get("timeframe", "daily")
        if tf not in ("daily", "weekly"):
            raise DSLValidationError(f"timeframe must be daily/weekly, got {tf!r}")
        if tf == "weekly" and ctype not in TIMEFRAME_AWARE:
            raise DSLValidationError(
                f"condition '{ctype}' does not support weekly timeframe")
        fields_ok = WEEKLY_FIELDS if tf == "weekly" else KNOWN_FIELDS

        def _check_field(name, where, _ok=fields_ok, _tf=tf):
            if not isinstance(name, str) or name not in _ok:
                raise DSLValidationError(
                    f"{where}: unknown field '{name}' for {_tf} timeframe.")

        if ctype == "compare":
            _require(c, ["left", "op", "right"])
            _check_field(c["left"], "compare.left")
            if not isinstance(c["right"], (int, float)):
                _check_field(c["right"], "compare.right")
            if c["op"] not in VALID_OPS:
                raise DSLValidationError(f"bad op {c['op']!r}")
        elif ctype == "proximity":
            _require(c, ["target", "ref", "tolerance_pct"])
            _check_field(c["target"], "proximity.target")
            _check_field(c["ref"], "proximity.ref")
        elif ctype == "trend":
            if c.get("direction") not in ("up", "down"):
                raise DSLValidationError("trend.direction must be up/down")
        elif ctype == "support_at_ma":
            _require(c, ["ma"])
            _check_field(c["ma"], "support_at_ma.ma")
        elif ctype == "cross":
            _require(c, ["fast", "slow", "direction"])
            _check_field(c["fast"], "cross.fast")
            _check_field(c["slow"], "cross.slow")
            if c["direction"] not in ("above", "below"):
                raise DSLValidationError("cross.direction must be above/below")
        elif ctype == "volume_spike":
            _require(c, ["min_ratio"])
        elif ctype == "range":
            _require(c, ["field"])
            _check_field(c["field"], "range.field")
            if "min" not in c and "max" not in c:
                raise DSLValidationError("range needs min and/or max")
        elif ctype == "change":
            _require(c, ["field", "window", "op", "value_pct"])
            _check_field(c["field"], "change.field")
            if c["op"] not in VALID_OPS:
                raise DSLValidationError(f"bad op {c['op']!r}")
        elif ctype in ("near_support", "near_resistance"):
            _require(c, ["tolerance_pct"])
        elif ctype == "breakout_resistance":
            pass  # lookback optional, defaults applied in evaluator
        elif ctype == "rel_strength":
            _require(c, ["window", "op", "value_pct"])
            if c["op"] not in VALID_OPS:
                raise DSLValidationError(f"bad op {c['op']!r}")
        elif ctype == "candle":
            _require(c, ["pattern"])
            if c["pattern"] not in CANDLE_PATTERNS:
                raise DSLValidationError(
                    f"unknown candle pattern {c['pattern']!r}; "
                    f"one of {sorted(CANDLE_PATTERNS)}")
        elif ctype == "tight_range":
            _require(c, ["max_range_pct"])
        elif ctype == "bb_squeeze":
            pass  # percentile/lookback have defaults
        elif ctype == "flat_base":
            pass  # all keys have defaults
        elif ctype == "sector":
            _require(c, ["in"])
            sectors = c["in"]
            if not isinstance(sectors, list) or not sectors:
                raise DSLValidationError("sector.in must be a non-empty list")
            unknown = [s for s in sectors if s not in KNOWN_SECTORS]
            if unknown:
                raise DSLValidationError(
                    f"sector.in: unknown sector(s) {unknown}. "
                    f"Must be one of {sorted(KNOWN_SECTORS)}")
        elif ctype == "rs_percentile":
            _require(c, ["op", "value"])
            if c["op"] not in VALID_OPS:
                raise DSLValidationError(f"bad op {c['op']!r}")
        elif ctype == "sector_rank":
            has_top, has_bottom = "top" in c, "bottom" in c
            if has_top == has_bottom:
                raise DSLValidationError(
                    "sector_rank needs exactly one of 'top'/'bottom'")
            n = c.get("top", c.get("bottom"))
            if not isinstance(n, int) or n < 1:
                raise DSLValidationError(
                    "sector_rank top/bottom must be a positive integer")
    return screen


# ---------------------------------------------------------------- echo
def _fmt_field(f: Any) -> str:
    return str(f) if isinstance(f, (int, float)) else f.upper().replace(
        "_", " ")


def describe_condition(c: dict) -> str:
    """Plain-English rendering of a single condition."""
    parts: list[str] = []
    _append_condition(parts, c)
    return parts[0] if parts else str(c)


def describe(screen: dict) -> str:
    parts: list[str] = []
    for c in screen["conditions"]:
        _append_condition(parts, c)
    joiner = " AND " if screen.get("logic", "AND") == "AND" else " OR "
    return "Screening for: " + joiner.join(parts)


def _append_condition(parts: list, c: dict) -> None:
        t = c["type"]
        if t == "compare":
            parts.append(f"{_fmt_field(c['left'])} {c['op']} "
                         f"{_fmt_field(c['right'])}")
        elif t == "proximity":
            parts.append(
                f"{_fmt_field(c['target'])} within {c['tolerance_pct']}% of "
                f"{_fmt_field(c['ref'])} in last {c.get('lookback', 3)} bars")
        elif t == "trend":
            d = c["direction"]
            parts.append(
                f"{'up' if d == 'up' else 'down'}trend (close "
                f"{'>' if d == 'up' else '<'} EMA50 "
                f"{'>' if d == 'up' else '<'} EMA200, EMA50 "
                f"{'rising' if d == 'up' else 'falling'})")
        elif t == "support_at_ma":
            parts.append(
                f"took support at {_fmt_field(c['ma'])} within last "
                f"{c.get('lookback', 3)} bars (low touched within "
                f"{c.get('tolerance_pct', 1.5)}%, close held above)")
        elif t == "cross":
            parts.append(
                f"{_fmt_field(c['fast'])} crossed {c['direction']} "
                f"{_fmt_field(c['slow'])} in last {c.get('lookback', 3)} bars")
        elif t == "volume_spike":
            parts.append(f"volume ≥ {c['min_ratio']}× its 20-day average")
        elif t == "range":
            lo, hi = c.get("min"), c.get("max")
            if lo is not None and hi is not None:
                parts.append(f"{_fmt_field(c['field'])} between {lo} and {hi}")
            elif lo is not None:
                parts.append(f"{_fmt_field(c['field'])} ≥ {lo}")
            else:
                parts.append(f"{_fmt_field(c['field'])} ≤ {hi}")
        elif t == "change":
            parts.append(
                f"{_fmt_field(c['field'])} {c['window']}-bar change "
                f"{c['op']} {c['value_pct']}%")
        elif t == "near_support":
            parts.append(
                f"close within {c['tolerance_pct']}% of nearest swing "
                "support level")
        elif t == "near_resistance":
            parts.append(
                f"close within {c['tolerance_pct']}% below nearest swing "
                "resistance level")
        elif t == "breakout_resistance":
            parts.append(
                f"broke above a prior resistance level within last "
                f"{c.get('lookback', 5)} bars")
        elif t == "rel_strength":
            parts.append(
                f"{c['window']}-bar return minus Nifty {c['op']} "
                f"{c['value_pct']}%")
        elif t == "candle":
            nm = c["pattern"].replace("_", " ")
            lb = c.get("lookback", 1)
            parts.append(f"{nm} candle" +
                         (f" within last {lb} bars" if lb > 1 else
                          " on the latest bar"))
        elif t == "tight_range":
            parts.append(
                f"{c.get('bars', 10)}-bar range ≤ {c['max_range_pct']}%")
        elif t == "bb_squeeze":
            parts.append(
                f"Bollinger bandwidth in bottom {c.get('percentile', 20)}% "
                f"of its {c.get('lookback', 252)}-bar history")
        elif t == "flat_base":
            parts.append(
                f"flat base: {c.get('bars', 20)}-bar range ≤ "
                f"{c.get('max_range_pct', 12)}% within "
                f"{c.get('max_from_52w_high_pct', 15)}% of 52-week high")
        elif t == "sector":
            parts.append("sector in " + ", ".join(c["in"]))
        elif t == "rs_percentile":
            parts.append(
                f"{c.get('window', 63)}-bar relative-strength percentile "
                f"{c['op']} {c['value']}")
        elif t == "sector_rank":
            w = c.get("window", 63)
            if "top" in c:
                parts.append(
                    f"stock's sector in the top {c['top']} by "
                    f"{w}-bar equal-weight momentum")
            else:
                parts.append(
                    f"stock's sector in the bottom {c['bottom']} by "
                    f"{w}-bar equal-weight momentum")
        if c.get("timeframe") == "weekly" and parts:
            parts[-1] += " [weekly]"
