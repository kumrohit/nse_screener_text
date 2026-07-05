"""Evaluator — pure functions from (panel, condition) -> bool.

Each condition evaluates against a single symbol's indicator panel, as of a
given row position. `run_screen` maps over all panels and assembles the
result table. No natural language anywhere in this module.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ------------------------------------------------------------ helpers
def _val(panel: pd.DataFrame, field, i: int):
    """Field value at row i; numbers pass through."""
    if isinstance(field, (int, float)):
        return field
    return panel[field].iloc[i]


def _cmp(a, op: str, b) -> bool:
    if pd.isna(a) or pd.isna(b):
        return False
    return {"": None, ">": a > b, ">=": a >= b,
            "<": a < b, "<=": a <= b}[op]


# ------------------------------------------------------------ conditions
def cond_compare(panel, c, i) -> bool:
    return _cmp(_val(panel, c["left"], i), c["op"], _val(panel, c["right"], i))


def cond_proximity(panel, c, i) -> bool:
    lb = int(c.get("lookback", 3))
    tgt = panel[c["target"]].iloc[max(0, i - lb + 1): i + 1]
    ref = panel[c["ref"]].iloc[max(0, i - lb + 1): i + 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = ((tgt - ref).abs() / ref * 100)
    return bool((dist <= c["tolerance_pct"]).any())


def cond_trend(panel, c, i) -> bool:
    close = panel["close"].iloc[i]
    e50, e200 = panel["ema_50"].iloc[i], panel["ema_200"].iloc[i]
    slope = panel["ema_50_slope"].iloc[i]
    if any(pd.isna(x) for x in (close, e50, e200, slope)):
        return False
    if c["direction"] == "up":
        return close > e50 > e200 and slope > 0
    return close < e50 < e200 and slope < 0


def cond_support_at_ma(panel, c, i) -> bool:
    """Canonical 'taking support': within lookback, low dipped to within
    tolerance of the MA, close held at/above the MA on every bar of the
    window, and the latest close is above the MA."""
    ma, tol = c["ma"], float(c.get("tolerance_pct", 1.5))
    lb = int(c.get("lookback", 3))
    lo = max(0, i - lb + 1)
    win = panel.iloc[lo: i + 1]
    if win[ma].isna().any():
        return False
    touched = ((win["low"] - win[ma]).abs() / win[ma] * 100 <= tol) | \
              (win["low"] < win[ma])           # pierced but recovered counts
    held = win["close"] >= win[ma] * (1 - tol / 100)
    latest_above = panel["close"].iloc[i] > panel[ma].iloc[i]
    return bool(touched.any() and held.all() and latest_above)


def cond_cross(panel, c, i) -> bool:
    lb = int(c.get("lookback", 3))
    lo = max(1, i - lb + 1)
    fast, slow = panel[c["fast"]], panel[c["slow"]]
    for j in range(lo, i + 1):
        prev_diff = fast.iloc[j - 1] - slow.iloc[j - 1]
        cur_diff = fast.iloc[j] - slow.iloc[j]
        if pd.isna(prev_diff) or pd.isna(cur_diff):
            continue
        if c["direction"] == "above" and prev_diff <= 0 < cur_diff:
            return True
        if c["direction"] == "below" and prev_diff >= 0 > cur_diff:
            return True
    return False


def cond_volume_spike(panel, c, i) -> bool:
    return _cmp(panel["vol_ratio"].iloc[i], ">=", float(c["min_ratio"]))


def cond_range(panel, c, i) -> bool:
    v = panel[c["field"]].iloc[i]
    if pd.isna(v):
        return False
    if "min" in c and v < c["min"]:
        return False
    if "max" in c and v > c["max"]:
        return False
    return True


def cond_change(panel, c, i) -> bool:
    w = int(c["window"])
    if i - w < 0:
        return False
    s = panel[c["field"]]
    base = s.iloc[i - w]
    if pd.isna(base) or base == 0 or pd.isna(s.iloc[i]):
        return False
    chg = 100 * (s.iloc[i] / base - 1)
    return _cmp(chg, c["op"], float(c["value_pct"]))


def cond_near_support(panel, c, i, ctx=None) -> bool:
    from . import sr
    sup, _res = sr.nearest_levels(panel, i)
    if sup is None:
        return False
    close = panel["close"].iloc[i]
    return _cmp(100 * (close - sup) / sup, "<=", float(c["tolerance_pct"]))


def cond_near_resistance(panel, c, i, ctx=None) -> bool:
    from . import sr
    _sup, res = sr.nearest_levels(panel, i)
    if res is None:
        return False
    close = panel["close"].iloc[i]
    return _cmp(100 * (res - close) / close, "<=",
                float(c["tolerance_pct"]))


def cond_breakout_resistance(panel, c, i, ctx=None) -> bool:
    """Levels are computed as of `lookback` bars ago (so the breakout bar
    itself doesn't create/destroy the level), then we check that close was
    below the nearest resistance then and is above it now, with an
    optional confirmation buffer."""
    from . import sr
    lb = int(c.get("lookback", 5))
    buf = float(c.get("buffer_pct", 0.0))
    j = i - lb
    if j < 30:
        return False
    _sup, res = sr.nearest_levels(panel, j)
    if res is None:
        return False
    return bool(panel["close"].iloc[i] > res * (1 + buf / 100))


def cond_rel_strength(panel, c, i, benchmark: pd.Series | None = None
                      ) -> bool:
    """Stock return minus benchmark return over `window` bars, aligned on
    the panel's dates."""
    if benchmark is None:
        raise RuntimeError(
            "rel_strength condition requires benchmark data. "
            "Run `python -m screener.cli update` to fetch the Nifty index.")
    w = int(c["window"])
    if i - w < 0:
        return False
    dates = panel.index
    b = benchmark.reindex(dates, method="ffill")
    b0, b1 = b.iloc[i - w], b.iloc[i]
    s0, s1 = panel["close"].iloc[i - w], panel["close"].iloc[i]
    if any(pd.isna(x) for x in (b0, b1, s0, s1)) or b0 == 0 or s0 == 0:
        return False
    rs = 100 * ((s1 / s0) - (b1 / b0))
    return _cmp(rs, c["op"], float(c["value_pct"]))


def cond_sector(panel, c, i, symbol: str | None = None,
               sector_by_symbol: pd.Series | None = None) -> bool:
    if sector_by_symbol is None or symbol is None:
        raise RuntimeError(
            "sector condition requires universe metadata. Run "
            "`python -m screener.cli backfill` (or pass universe=... to "
            "run_screen) so industry classifications are available.")
    sec = sector_by_symbol.get(symbol)
    if sec is None or pd.isna(sec):
        return False
    return sec in c["in"]


def cond_rs_percentile(panel, c, i, symbol: str | None = None,
                       cross_section: pd.DataFrame | None = None) -> bool:
    if cross_section is None:
        raise RuntimeError(
            "rs_percentile condition requires the cross-sectional "
            "pre-pass (screener.cross_section). This is wired up "
            "automatically by run_screen/webapp when universe is passed.")
    if symbol not in cross_section.index:
        return False
    val = cross_section.loc[symbol, "rs_percentile"]
    if pd.isna(val):
        return False
    return _cmp(float(val), c["op"], float(c["value"]))


def cond_sector_rank(panel, c, i, symbol: str | None = None,
                     cross_section: pd.DataFrame | None = None) -> bool:
    if cross_section is None:
        raise RuntimeError(
            "sector_rank condition requires the cross-sectional "
            "pre-pass (screener.cross_section). This is wired up "
            "automatically by run_screen/webapp when universe is passed.")
    if symbol not in cross_section.index:
        return False
    rank, n = (cross_section.loc[symbol, "sector_rank"],
              cross_section.loc[symbol, "n_sectors"])
    if pd.isna(rank) or pd.isna(n):
        return False
    if "top" in c:
        return bool(rank <= c["top"])
    return bool(rank > (n - c["bottom"]))


DISPATCH = {
    "compare": cond_compare, "proximity": cond_proximity,
    "trend": cond_trend, "support_at_ma": cond_support_at_ma,
    "cross": cond_cross, "volume_spike": cond_volume_spike,
    "range": cond_range, "change": cond_change,
    "near_support": cond_near_support,
    "near_resistance": cond_near_resistance,
    "breakout_resistance": cond_breakout_resistance,
}

# Condition types that need cross-symbol context beyond a single panel
# (universe metadata / the cross-sectional pre-pass) — dispatched
# specially in evaluate_symbol/explain_symbol rather than via DISPATCH.
CROSS_SECTIONAL_TYPES = {"sector", "rs_percentile", "sector_rank"}

# weekly trend uses the weekly panel's own EMA set
def _weekly_trend(wpanel, c, i) -> bool:
    close = wpanel["close"].iloc[i]
    e20, e40 = wpanel["ema_20"].iloc[i], wpanel["ema_40"].iloc[i]
    slope = wpanel["ema_20_slope"].iloc[i]
    if any(pd.isna(x) for x in (close, e20, e40, slope)):
        return False
    if c["direction"] == "up":
        return close > e20 > e40 and slope > 0
    return close < e20 < e40 and slope < 0


# ------------------------------------------------------------ runner
def _row_at(panel: pd.DataFrame, as_of) -> int | None:
    if as_of and as_of != "latest":
        ts = pd.Timestamp(as_of)
        idx = panel.index[panel.index <= ts]
        if len(idx) == 0:
            return None
        return panel.index.get_loc(idx[-1])
    return len(panel) - 1


def evaluate_symbol(panel: pd.DataFrame, screen: dict,
                    as_of: str | None = None,
                    weekly: pd.DataFrame | None = None,
                    benchmark: pd.Series | None = None,
                    symbol: str | None = None,
                    sector_by_symbol: pd.Series | None = None,
                    cross_section: dict[int, pd.DataFrame] | None = None
                    ) -> bool:
    from . import indicators

    i = _row_at(panel, as_of)
    if i is None:
        return False

    def one(c: dict) -> bool:
        if c.get("timeframe") == "weekly":
            nonlocal weekly
            if weekly is None:
                weekly = indicators.compute_weekly_panel(panel)
            wi = _row_at(weekly, as_of)
            if wi is None:
                return False
            if c["type"] == "trend":
                return _weekly_trend(weekly, c, wi)
            return DISPATCH[c["type"]](weekly, c, wi)
        if c["type"] == "rel_strength":
            return cond_rel_strength(panel, c, i, benchmark=benchmark)
        if c["type"] == "sector":
            return cond_sector(panel, c, i, symbol=symbol,
                               sector_by_symbol=sector_by_symbol)
        if c["type"] in ("rs_percentile", "sector_rank"):
            cs = (cross_section or {}).get(int(c.get("window", 63)))
            fn = cond_rs_percentile if c["type"] == "rs_percentile" \
                else cond_sector_rank
            return fn(panel, c, i, symbol=symbol, cross_section=cs)
        return DISPATCH[c["type"]](panel, c, i)

    results = (one(c) for c in screen["conditions"])
    return all(results) if screen.get("logic", "AND") == "AND" \
        else any(results)


def _cross_sectional_context(screen: dict, panels: dict[str, pd.DataFrame],
                             universe: pd.DataFrame | None, as_of):
    """Sector lookup + the cross-sectional pre-pass, computed once per
    screen (not per symbol) — the structural change that makes
    `sector`/`rs_percentile`/`sector_rank` conditions affordable."""
    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else None)
    windows = {int(c.get("window", 63)) for c in screen["conditions"]
              if c["type"] in ("rs_percentile", "sector_rank")}
    cross_section = {}
    if windows and universe is not None:
        from . import cross_section as cs_mod
        cross_section = {w: cs_mod.build_cross_section(panels, universe,
                                                        as_of, w)
                         for w in windows}
    return sector_by_symbol, cross_section


def run_screen(panels: dict[str, pd.DataFrame], screen: dict,
               universe: pd.DataFrame | None = None,
               min_turnover_cr: float = 0.0,
               benchmark: pd.Series | None = None) -> pd.DataFrame:
    as_of = screen.get("as_of", "latest")
    sector_by_symbol, cross_section = _cross_sectional_context(
        screen, panels, universe, as_of)
    rows = []
    for sym, panel in panels.items():
        last = panel.iloc[-1]
        if min_turnover_cr and not (
                pd.notna(last["turnover_cr"])
                and panel["turnover_cr"].tail(20).median()
                >= min_turnover_cr):
            continue
        if evaluate_symbol(panel, screen, as_of, benchmark=benchmark,
                           symbol=sym, sector_by_symbol=sector_by_symbol,
                           cross_section=cross_section):
            rows.append({
                "symbol": sym,
                "close": round(last["close"], 2),
                "pct_vs_ema50":
                    round(100 * (last["close"] / last["ema_50"] - 1), 2)
                    if pd.notna(last["ema_50"]) else np.nan,
                "rsi": round(last["rsi"], 1),
                "vol_ratio": round(last["vol_ratio"], 2)
                    if pd.notna(last["vol_ratio"]) else np.nan,
                "ret_1m_pct": round(last["roc_21"], 1)
                    if pd.notna(last["roc_21"]) else np.nan,
                "ret_3m_pct": round(last["roc_63"], 1)
                    if pd.notna(last["roc_63"]) else np.nan,
                "pct_from_52w_high": round(last["pct_from_52w_high"], 1)
                    if pd.notna(last["pct_from_52w_high"]) else np.nan,
            })
    out = pd.DataFrame(rows)
    if not out.empty and universe is not None:
        out = out.merge(universe[["symbol", "name", "industry"]],
                        on="symbol", how="left")
        out = out.sort_values("ret_3m_pct", ascending=False)\
                 .reset_index(drop=True)
    return out


# ------------------------------------------------------------ patterns
# Exact definitions (TECHNICAL_DESIGN.md §9a). j indexes a single bar.
def _body(p, j):
    return abs(p["close"].iloc[j] - p["open"].iloc[j])


def _pat_inside_bar(p, j) -> bool:
    return (j >= 1
            and p["high"].iloc[j] < p["high"].iloc[j - 1]
            and p["low"].iloc[j] > p["low"].iloc[j - 1])


def _pat_nr7(p, j) -> bool:
    if j < 6:
        return False
    rng = (p["high"] - p["low"]).iloc[j - 6: j + 1]
    return bool(rng.iloc[-1] == rng.min() and (rng.iloc[:-1] > rng.iloc[-1]).all())


def _pat_bullish_engulfing(p, j) -> bool:
    if j < 1:
        return False
    po, pc = p["open"].iloc[j - 1], p["close"].iloc[j - 1]
    o, c = p["open"].iloc[j], p["close"].iloc[j]
    return pc < po and c > o and o <= pc and c >= po


def _pat_bearish_engulfing(p, j) -> bool:
    if j < 1:
        return False
    po, pc = p["open"].iloc[j - 1], p["close"].iloc[j - 1]
    o, c = p["open"].iloc[j], p["close"].iloc[j]
    return pc > po and c < o and o >= pc and c <= po


def _pat_hammer(p, j) -> bool:
    h, l = p["high"].iloc[j], p["low"].iloc[j]
    o, c = p["open"].iloc[j], p["close"].iloc[j]
    rng = h - l
    if rng <= 0:
        return False
    lower = min(o, c) - l
    upper = h - max(o, c)
    return lower >= 2 * _body(p, j) and upper <= 0.3 * rng


def _pat_shooting_star(p, j) -> bool:
    h, l = p["high"].iloc[j], p["low"].iloc[j]
    o, c = p["open"].iloc[j], p["close"].iloc[j]
    rng = h - l
    if rng <= 0:
        return False
    lower = min(o, c) - l
    upper = h - max(o, c)
    return upper >= 2 * _body(p, j) and lower <= 0.3 * rng


PATTERNS = {
    "inside_bar": _pat_inside_bar, "nr7": _pat_nr7,
    "bullish_engulfing": _pat_bullish_engulfing,
    "bearish_engulfing": _pat_bearish_engulfing,
    "hammer": _pat_hammer, "shooting_star": _pat_shooting_star,
}


def cond_candle(panel, c, i) -> bool:
    lb = int(c.get("lookback", 1))
    fn = PATTERNS[c["pattern"]]
    return any(fn(panel, j) for j in range(max(0, i - lb + 1), i + 1))


def cond_gap(panel, c, i) -> bool:
    """Open gapped away from the prior bar's close by at least
    min_gap_pct, on any bar within the lookback window."""
    lb = int(c.get("lookback", 3))
    min_pct = float(c.get("min_gap_pct", 2.0))
    direction = c["direction"]
    lo = max(1, i - lb + 1)
    for j in range(lo, i + 1):
        prev_close, o = panel["close"].iloc[j - 1], panel["open"].iloc[j]
        if pd.isna(prev_close) or pd.isna(o) or prev_close == 0:
            continue
        gap_pct = 100 * (o / prev_close - 1)
        if direction == "up" and gap_pct >= min_pct:
            return True
        if direction == "down" and gap_pct <= -min_pct:
            return True
    return False


def cond_tight_range(panel, c, i) -> bool:
    bars = int(c.get("bars", 10))
    if i - bars + 1 < 0:
        return False
    win = panel.iloc[i - bars + 1: i + 1]
    lo = win["low"].min()
    if pd.isna(lo) or lo <= 0:
        return False
    span = 100 * (win["high"].max() - lo) / lo
    return _cmp(span, "<=", float(c["max_range_pct"]))


def cond_bb_squeeze(panel, c, i) -> bool:
    lb = int(c.get("lookback", 252))
    pct = float(c.get("percentile", 20))
    hist = panel["bb_width_pct"].iloc[max(0, i - lb + 1): i + 1].dropna()
    cur = panel["bb_width_pct"].iloc[i]
    if pd.isna(cur) or len(hist) < 60:
        return False
    return _cmp(cur, "<=", float(np.percentile(hist, pct)))


def cond_flat_base(panel, c, i) -> bool:
    """Extended tight range near the 52-week high — pre-breakout base."""
    bars = int(c.get("bars", 20))
    max_range = float(c.get("max_range_pct", 12))
    max_off_high = float(c.get("max_from_52w_high_pct", 15))
    if not cond_tight_range(panel, {"bars": bars,
                                    "max_range_pct": max_range}, i):
        return False
    off = panel["pct_from_52w_high"].iloc[i]
    return _cmp(off, ">=", -max_off_high)


DISPATCH.update({
    "candle": cond_candle, "tight_range": cond_tight_range,
    "bb_squeeze": cond_bb_squeeze, "flat_base": cond_flat_base,
    "gap": cond_gap,
})
