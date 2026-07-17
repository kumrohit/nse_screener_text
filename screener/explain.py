"""Evidence layer — for every condition, the actual numbers behind the
pass/fail decision, so the UI can show *why* a stock matched.

Each explainer returns:
    {"description": str,   # plain-English condition (from dsl)
     "passed": bool,       # via the same evaluator functions (no drift)
     "evidence": str,      # human sentence with the observed values
     "values": dict}       # raw numbers for the UI

Pass/fail is always delegated to evaluator.cond_* so the explanation can
never disagree with the screen itself; this module only adds observability.
"""
from __future__ import annotations

import pandas as pd

from . import dsl, evaluator, indicators, sr


def _f(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _pctdiff(a, b):
    if any(v is None or pd.isna(v) for v in (a, b)) or b == 0:
        return None
    return round(100 * (a / b - 1), 2)


def _date(panel, j):
    return str(panel.index[j].date())


# ------------------------------------------------------------ explainers
def _ex_compare(panel, c, i):
    lv = evaluator._val(panel, c["left"], i)
    rv = evaluator._val(panel, c["right"], i)
    ev = (f"{c['left']} = {_f(lv)} {c['op']} "
          f"{c['right']} = {_f(rv)}"
          + (f" (gap {_pctdiff(lv, rv)}%)"
             if not isinstance(c["right"], (int, float)) else ""))
    return ev, {"left": _f(lv), "right": _f(rv)}


def _ex_proximity(panel, c, i):
    lb = int(c.get("lookback", 3))
    lo = max(0, i - lb + 1)
    tgt, ref = panel[c["target"]], panel[c["ref"]]
    dists = ((tgt.iloc[lo:i + 1] - ref.iloc[lo:i + 1]).abs()
             / ref.iloc[lo:i + 1] * 100)
    j = int(dists.idxmin() == dists.index[dists.argmin()]
            and lo + int(dists.argmin()))
    ev = (f"closest approach {_f(dists.min())}% on {_date(panel, j)} "
          f"({c['target']} {_f(tgt.iloc[j])} vs {c['ref']} "
          f"{_f(ref.iloc[j])}), tolerance {c['tolerance_pct']}%")
    return ev, {"min_distance_pct": _f(dists.min())}


def _ex_trend(panel, c, i):
    close, e50 = panel["close"].iloc[i], panel["ema_50"].iloc[i]
    e200, slope = panel["ema_200"].iloc[i], panel["ema_50_slope"].iloc[i]
    ev = (f"close {_f(close)}, EMA50 {_f(e50)} "
          f"({_pctdiff(close, e50)}% away), EMA200 {_f(e200)}, "
          f"EMA50 5-bar slope {_f(slope, 3)} "
          f"({'rising' if slope and slope > 0 else 'falling/flat'})")
    return ev, {"close": _f(close), "ema_50": _f(e50),
                "ema_200": _f(e200), "ema_50_slope": _f(slope, 3)}


def _ex_support_at_ma(panel, c, i):
    ma = c["ma"]
    lb = int(c.get("lookback", 3))
    lo = max(0, i - lb + 1)
    win = panel.iloc[lo:i + 1]
    dist = (win["low"] - win[ma]).abs() / win[ma] * 100
    j = lo + int(dist.argmin())
    touch = min(float(dist.min()),
                0.0 if (win["low"] < win[ma]).any() else float(dist.min()))
    held_min = float((win["close"] / win[ma] - 1).min() * 100)
    close, mav = panel["close"].iloc[i], panel[ma].iloc[i]
    ev = (f"low touched {ma} on {_date(panel, j)} "
          f"(low {_f(panel['low'].iloc[j])} vs {ma} "
          f"{_f(panel[ma].iloc[j])}, distance {_f(touch)}%); "
          f"worst close-vs-{ma} in window {_f(held_min)}%; "
          f"latest close {_f(close)} is {_pctdiff(close, mav)}% above {ma}")
    return ev, {"touch_distance_pct": _f(touch),
                "worst_hold_pct": _f(held_min),
                "close_vs_ma_pct": _pctdiff(close, mav)}


def _ex_cross(panel, c, i):
    lb = int(c.get("lookback", 3))
    fast, slow = panel[c["fast"]], panel[c["slow"]]
    cross_j = None
    for j in range(max(1, i - lb + 1), i + 1):
        pd_, cd = fast.iloc[j - 1] - slow.iloc[j - 1], \
            fast.iloc[j] - slow.iloc[j]
        if pd.isna(pd_) or pd.isna(cd):
            continue
        if (c["direction"] == "above" and pd_ <= 0 < cd) or \
           (c["direction"] == "below" and pd_ >= 0 > cd):
            cross_j = j
    if cross_j is None:
        ev = (f"no {c['direction']}-cross of {c['fast']}/{c['slow']} in "
              f"last {lb} bars (current gap "
              f"{_pctdiff(fast.iloc[i], slow.iloc[i])}%)")
        return ev, {}
    ev = (f"{c['fast']} crossed {c['direction']} {c['slow']} on "
          f"{_date(panel, cross_j)} ({_f(fast.iloc[cross_j])} vs "
          f"{_f(slow.iloc[cross_j])})")
    return ev, {"cross_date": _date(panel, cross_j)}


def _ex_volume_spike(panel, c, i):
    v, a = panel["volume"].iloc[i], panel["vol_avg_20"].iloc[i]
    r = panel["vol_ratio"].iloc[i]
    ev = (f"volume {int(v):,} vs 20-day avg {int(a):,} — ratio "
          f"{_f(r)}× (threshold {c['min_ratio']}×)")
    return ev, {"ratio": _f(r)}


def _ex_range(panel, c, i):
    v = panel[c["field"]].iloc[i]
    bounds = " / ".join(
        s for s in (f"min {c['min']}" if "min" in c else "",
                    f"max {c['max']}" if "max" in c else "") if s)
    return f"{c['field']} = {_f(v)} ({bounds})", {c["field"]: _f(v)}


def _ex_change(panel, c, i):
    w = int(c["window"])
    s = panel[c["field"]]
    if i - w < 0:
        return "insufficient history for window", {}
    chg = _pctdiff(s.iloc[i], s.iloc[i - w])
    ev = (f"{c['field']} moved {chg}% over {w} bars "
          f"({_f(s.iloc[i - w])} on {_date(panel, i - w)} → "
          f"{_f(s.iloc[i])}); threshold {c['op']} {c['value_pct']}%")
    return ev, {"change_pct": chg}


def _ex_near_support(panel, c, i):
    sup, res = sr.nearest_levels(panel, i)
    close = panel["close"].iloc[i]
    if sup is None:
        return "no qualifying swing support below price (need ≥2 pivot touches in 250 bars)", {}
    d = round(100 * (close - sup) / sup, 2)
    ev = (f"nearest swing support {_f(sup)} (close {_f(close)} is {d}% "
          f"above it, tolerance {c['tolerance_pct']}%)"
          + (f"; next resistance {_f(res)}" if res else ""))
    return ev, {"support": _f(sup), "distance_pct": d}


def _ex_near_resistance(panel, c, i):
    sup, res = sr.nearest_levels(panel, i)
    close = panel["close"].iloc[i]
    if res is None:
        return "no qualifying swing resistance above price", {}
    d = round(100 * (res - close) / close, 2)
    ev = (f"nearest swing resistance {_f(res)} (close {_f(close)} is {d}% "
          f"below it, tolerance {c['tolerance_pct']}%)")
    return ev, {"resistance": _f(res), "distance_pct": d}


def _ex_breakout_resistance(panel, c, i):
    lb = int(c.get("lookback", 5))
    j = i - lb
    if j < 30:
        return "insufficient history for breakout lookback", {}
    _sup, res = sr.nearest_levels(panel, j)
    close_then, close_now = panel["close"].iloc[j], panel["close"].iloc[i]
    if res is None:
        return (f"no resistance level existed above price as of "
                f"{_date(panel, j)}"), {}
    ev = (f"resistance {_f(res)} stood above close {_f(close_then)} on "
          f"{_date(panel, j)}; close now {_f(close_now)} "
          f"({_pctdiff(close_now, res)}% vs level)")
    return ev, {"level": _f(res), "close_now": _f(close_now)}


def _ex_rel_strength(panel, c, i, benchmark):
    w = int(c["window"])
    if benchmark is None or i - w < 0:
        return "benchmark unavailable or insufficient history", {}
    b = benchmark.reindex(panel.index, method="ffill")
    s_ret = _pctdiff(panel["close"].iloc[i], panel["close"].iloc[i - w])
    b_ret = _pctdiff(b.iloc[i], b.iloc[i - w])
    diff = None if None in (s_ret, b_ret) else round(s_ret - b_ret, 2)
    ev = (f"stock {s_ret}% vs Nifty {b_ret}% over {w} bars — "
          f"relative {diff} pct pts (threshold {c['op']} {c['value_pct']})")
    return ev, {"stock_ret_pct": s_ret, "nifty_ret_pct": b_ret,
                "relative_pct": diff}


def _ex_sector(panel, c, i, symbol, sector_by_symbol):
    sec = sector_by_symbol.get(symbol) if sector_by_symbol is not None \
        else None
    if sec is None or pd.isna(sec):
        return "sector unknown for this symbol", {}
    ev = f"sector: {sec} (target: {', '.join(c['in'])})"
    return ev, {"sector": sec}


def _ex_rs_percentile(panel, c, i, symbol, cross_section):
    if cross_section is None or symbol not in cross_section.index:
        return "cross-sectional data unavailable", {}
    row = cross_section.loc[symbol]
    if c.get("basis") == "mom_12_1":
        val = row["mom_12_1_percentile"]
        if pd.isna(val):
            return "insufficient history for 12-1 momentum percentile", {}
        ev = (f"12-1 momentum {_f(row['mom_12_1'])}% ranks at the "
              f"{_f(val, 1)}th percentile (threshold {c['op']} "
              f"{c['value']})")
        return ev, {"mom_12_1": _f(row["mom_12_1"]),
                    "mom_12_1_percentile": _f(val, 1)}
    val = row["rs_percentile"]
    if pd.isna(val):
        return "insufficient history for RS percentile", {}
    ev = (f"{c.get('window', 63)}-bar return {_f(row['ret_pct'])}% ranks "
          f"at the {_f(val, 1)}th percentile (threshold {c['op']} "
          f"{c['value']})")
    return ev, {"ret_pct": _f(row["ret_pct"]), "rs_percentile": _f(val, 1)}


def _ex_atr_pct_percentile(panel, c, i, symbol, cross_section):
    if cross_section is None or symbol not in cross_section.index:
        return "cross-sectional data unavailable", {}
    row = cross_section.loc[symbol]
    val = row["atr_percentile"]
    if pd.isna(val):
        return "insufficient history for ATR% percentile", {}
    ev = (f"ATR% {_f(row['atr_pct'])} ranks at the {_f(val, 1)}th "
          f"volatility percentile (threshold {c['op']} {c['value']})")
    return ev, {"atr_pct": _f(row["atr_pct"]), "atr_percentile": _f(val, 1)}


def _ex_sector_rank(panel, c, i, symbol, cross_section):
    if cross_section is None or symbol not in cross_section.index:
        return "cross-sectional data unavailable", {}
    row = cross_section.loc[symbol]
    rank, n, sec, sret = (row["sector_rank"], row["n_sectors"],
                         row["sector"], row["sector_ret_pct"])
    if pd.isna(rank):
        return "sector momentum unavailable (insufficient history)", {}
    top = (cross_section.drop_duplicates("sector")
          .dropna(subset=["sector_rank"]).sort_values("sector_rank").head(3))
    leaders = ", ".join(f"{r['sector']} ({_f(r['sector_ret_pct'])}%)"
                        for _, r in top.iterrows())
    label, thresh = ("top", c["top"]) if "top" in c else ("bottom", c["bottom"])
    ev = (f"sector '{sec}' ranked {int(rank)}/{int(n)} by "
          f"{c.get('window', 63)}-bar equal-weight return "
          f"({_f(sret)}%); {label} {thresh} required; leaders: {leaders}")
    return ev, {"sector": sec, "sector_rank": int(rank), "n_sectors": int(n),
               "sector_ret_pct": _f(sret)}


def _ex_breadth(panel, c, i, breadth):
    if breadth is None or breadth.get("pct_above_200dma") is None:
        return "breadth data unavailable (insufficient universe history)", {}
    pct, pct_hi = breadth["pct_above_200dma"], breadth.get("pct_at_20d_high")
    ev = (f"{_f(pct, 1)}% of the universe above its 200-day SMA "
         f"(threshold: {'≥' if c['direction'] == 'positive' else '<'} 50%)"
         + (f"; {_f(pct_hi, 1)}% making new 20-day highs" if pct_hi is not None
            else ""))
    return ev, {"pct_above_200dma": _f(pct, 1),
               "pct_at_20d_high": _f(pct_hi, 1) if pct_hi is not None else None}


def _ex_threshold_cross(panel, c, i):
    lb = int(c.get("lookback", 3))
    level = float(c["level"])
    s = panel[c["field"]]
    lo = max(1, i - lb + 1)
    cross_j = None
    for j in range(lo, i + 1):
        prev, cur = s.iloc[j - 1], s.iloc[j]
        if pd.isna(prev) or pd.isna(cur):
            continue
        if (c["direction"] == "above" and prev <= level < cur) or \
           (c["direction"] == "below" and prev >= level > cur):
            cross_j = j
    if cross_j is None:
        ev = (f"no {c['direction']}-cross of {c['field']} through "
              f"{level} in last {lb} bars (current {_f(s.iloc[i])})")
        return ev, {}
    ev = (f"{c['field']} crossed {c['direction']} {level} on "
          f"{_date(panel, cross_j)} ({_f(s.iloc[cross_j - 1])} → "
          f"{_f(s.iloc[cross_j])})")
    return ev, {"cross_date": _date(panel, cross_j)}


def _ex_persistence(panel, c, i):
    bars = int(c["bars"])
    lo = i - bars + 1
    if lo < 0:
        return "insufficient history for persistence window", {}
    win = panel[c["field"]].iloc[lo: i + 1]
    ev = (f"{c['field']} over last {bars} bars: min {_f(win.min())}, "
          f"max {_f(win.max())} (need all {c['op']} {c['value']})")
    return ev, {"min": _f(win.min()), "max": _f(win.max())}


def _ex_divergence(panel, c, i):
    from . import sr
    lb = int(c.get("lookback", 40))
    osc_field = c["oscillator"]
    lo = max(0, i - lb + 1)
    win = panel.iloc[lo: i + 1]
    if len(win) < 2 * sr.PIVOT_K + 1:
        return f"insufficient history for a {lb}-bar pivot search", {}
    ph, pl = sr.find_pivots(win, k=sr.PIVOT_K)
    bullish = c["kind"] == "bullish"
    mask = pl if bullish else ph
    price_field = "low" if bullish else "high"
    pivot_dates = list(win.index[mask])
    if len(pivot_dates) < 2:
        return (f"fewer than two confirmed {price_field} pivots in last "
               f"{lb} bars", {})
    p1, p2 = pivot_dates[-2], pivot_dates[-1]
    price1, price2 = win[price_field].loc[p1], win[price_field].loc[p2]
    osc1, osc2 = win[osc_field].loc[p1], win[osc_field].loc[p2]
    ev = (f"pivot {price_field}s: {p1.date()} {_f(price1)} "
          f"({osc_field} {_f(osc1)}) → {p2.date()} {_f(price2)} "
          f"({osc_field} {_f(osc2)})")
    return ev, {"pivot1_date": str(p1.date()), "pivot1_price": _f(price1),
               "pivot1_osc": _f(osc1), "pivot2_date": str(p2.date()),
               "pivot2_price": _f(price2), "pivot2_osc": _f(osc2)}


_EXPLAINERS = {
    "compare": _ex_compare, "proximity": _ex_proximity, "trend": _ex_trend,
    "support_at_ma": _ex_support_at_ma, "cross": _ex_cross,
    "volume_spike": _ex_volume_spike, "range": _ex_range,
    "change": _ex_change, "near_support": _ex_near_support,
    "near_resistance": _ex_near_resistance,
    "breakout_resistance": _ex_breakout_resistance,
    "threshold_cross": _ex_threshold_cross,
    "persistence": _ex_persistence,
    "divergence": _ex_divergence,
}


def explain_symbol(panel: pd.DataFrame, screen: dict,
                   as_of: str | None = None,
                   benchmark: pd.Series | None = None,
                   symbol: str | None = None,
                   sector_by_symbol: pd.Series | None = None,
                   cross_section: dict[int, pd.DataFrame] | None = None,
                   breadth: dict | None = None
                   ) -> list[dict]:
    """One evidence dict per condition, in screen order."""
    weekly = None
    i = evaluator._row_at(panel, as_of)
    out = []
    for c in screen["conditions"]:
        if c.get("timeframe") == "weekly":
            if weekly is None:
                weekly = indicators.compute_weekly_panel(panel)
            wi = evaluator._row_at(weekly, as_of)
            passed = (evaluator._weekly_trend(weekly, c, wi)
                      if c["type"] == "trend"
                      else evaluator.DISPATCH[c["type"]](weekly, c, wi))
            fn = _EXPLAINERS[c["type"]]
            # weekly trend uses different EMAs — build evidence directly
            if c["type"] == "trend":
                close = weekly["close"].iloc[wi]
                e20, e40 = weekly["ema_20"].iloc[wi], weekly["ema_40"].iloc[wi]
                sl = weekly["ema_20_slope"].iloc[wi]
                ev = (f"weekly close {_f(close)}, EMA20w {_f(e20)}, "
                      f"EMA40w {_f(e40)}, EMA20w slope {_f(sl, 3)}")
                vals = {"close": _f(close), "ema_20w": _f(e20),
                        "ema_40w": _f(e40)}
            else:
                ev, vals = fn(weekly, c, wi)
            ev = "[weekly] " + ev
        elif c["type"] == "rel_strength":
            passed = evaluator.cond_rel_strength(panel, c, i,
                                                 benchmark=benchmark)
            ev, vals = _ex_rel_strength(panel, c, i, benchmark)
        elif c["type"] == "sector":
            passed = evaluator.cond_sector(
                panel, c, i, symbol=symbol, sector_by_symbol=sector_by_symbol)
            ev, vals = _ex_sector(panel, c, i, symbol, sector_by_symbol)
        elif c["type"] == "breadth":
            passed = evaluator.cond_breadth(panel, c, i, breadth=breadth)
            ev, vals = _ex_breadth(panel, c, i, breadth)
        elif c["type"] in ("rs_percentile", "sector_rank",
                          "atr_pct_percentile"):
            cs = (cross_section or {}).get(int(c.get("window", 63)))
            fn_eval = {"rs_percentile": evaluator.cond_rs_percentile,
                      "sector_rank": evaluator.cond_sector_rank,
                      "atr_pct_percentile":
                          evaluator.cond_atr_pct_percentile}[c["type"]]
            fn_ex = {"rs_percentile": _ex_rs_percentile,
                     "sector_rank": _ex_sector_rank,
                     "atr_pct_percentile": _ex_atr_pct_percentile}[c["type"]]
            passed = fn_eval(panel, c, i, symbol=symbol, cross_section=cs)
            ev, vals = fn_ex(panel, c, i, symbol, cs)
        else:
            passed = evaluator.DISPATCH[c["type"]](panel, c, i)
            ev, vals = _EXPLAINERS[c["type"]](panel, c, i)
        out.append({"description": dsl.describe_condition(c),
                    "passed": bool(passed), "evidence": ev,
                    "values": vals})
    return out


# ------------------------------------------------------------ patterns
def _ex_candle(panel, c, i):
    lb = int(c.get("lookback", 1))
    fn = evaluator.PATTERNS[c["pattern"]]
    hit = next((j for j in range(i, max(0, i - lb + 1) - 1, -1)
                if fn(panel, j)), None)
    if hit is None:
        b = panel.iloc[i]
        ev = (f"no {c['pattern']} in last {lb} bar(s); latest bar "
              f"O {_f(b['open'])} H {_f(b['high'])} L {_f(b['low'])} "
              f"C {_f(b['close'])}")
        return ev, {}
    b = panel.iloc[hit]
    ev = (f"{c['pattern']} on {_date(panel, hit)} — O {_f(b['open'])} "
          f"H {_f(b['high'])} L {_f(b['low'])} C {_f(b['close'])}")
    return ev, {"pattern_date": _date(panel, hit)}


def _ex_gap(panel, c, i):
    lb = int(c.get("lookback", 3))
    min_pct = float(c.get("min_gap_pct", 2.0))
    direction = c["direction"]
    lo = max(1, i - lb + 1)
    hit = None
    for j in range(lo, i + 1):
        prev_close, o = panel["close"].iloc[j - 1], panel["open"].iloc[j]
        if pd.isna(prev_close) or pd.isna(o) or prev_close == 0:
            continue
        gap_pct = 100 * (o / prev_close - 1)
        if ((direction == "up" and gap_pct >= min_pct)
                or (direction == "down" and gap_pct <= -min_pct)):
            hit = (j, gap_pct)
    if hit is None:
        return (f"no {direction}-gap ≥ {min_pct}% in last {lb} bars", {})
    j, gap_pct = hit
    ev = (f"gapped {direction} {_f(gap_pct)}% on {_date(panel, j)} "
          f"(open {_f(panel['open'].iloc[j])} vs prior close "
          f"{_f(panel['close'].iloc[j - 1])})")
    return ev, {"gap_date": _date(panel, j), "gap_pct": _f(gap_pct)}


def _ex_tight_range(panel, c, i):
    bars = int(c.get("bars", 10))
    if i - bars + 1 < 0:
        return "insufficient history", {}
    win = panel.iloc[i - bars + 1: i + 1]
    span = _f(100 * (win["high"].max() - win["low"].min())
              / win["low"].min())
    ev = (f"{bars}-bar span {span}% "
          f"({_f(win['low'].min())}–{_f(win['high'].max())}), "
          f"limit {c['max_range_pct']}%")
    return ev, {"range_pct": span}


def _ex_bb_squeeze(panel, c, i):
    import numpy as np
    lb = int(c.get("lookback", 252))
    hist = panel["bb_width_pct"].iloc[max(0, i - lb + 1): i + 1].dropna()
    cur = panel["bb_width_pct"].iloc[i]
    if pd.isna(cur) or len(hist) < 60:
        return "insufficient bandwidth history", {}
    pctile = _f(100 * (hist < cur).mean(), 1)
    ev = (f"bandwidth {_f(cur)}% sits at the {pctile}th percentile of "
          f"{len(hist)} bars (threshold: bottom {c.get('percentile', 20)}%)")
    return ev, {"bandwidth_pct": _f(cur), "percentile": pctile}


def _ex_flat_base(panel, c, i):
    ev_r, vals = _ex_tight_range(
        panel, {"bars": c.get("bars", 20),
                "max_range_pct": c.get("max_range_pct", 12)}, i)
    off = panel["pct_from_52w_high"].iloc[i]
    ev = (f"{ev_r}; close {_f(off, 1)}% from 52-week high "
          f"(limit −{c.get('max_from_52w_high_pct', 15)}%)")
    vals["pct_from_52w_high"] = _f(off, 1)
    return ev, vals


_EXPLAINERS.update({
    "candle": _ex_candle, "tight_range": _ex_tight_range,
    "bb_squeeze": _ex_bb_squeeze, "flat_base": _ex_flat_base,
    "gap": _ex_gap,
})
