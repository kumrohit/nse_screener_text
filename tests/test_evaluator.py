"""Tests against constructed series where the correct answer is known.

Three archetypes:
  UPTREND_PULLBACK — steady uptrend that dips to the 50 EMA and bounces.
    MUST match "support at ema_50 + uptrend".
  BREAKDOWN — uptrend that slices through the 50 EMA and closes well below.
    MUST NOT match support; must not be an uptrend at the end.
  SIDEWAYS — flat noise. Must not match trend conditions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
sys.path.insert(0, "/home/claude/nse_screener")

from screener import dsl, indicators                       # noqa: E402
from screener.evaluator import evaluate_symbol, run_screen  # noqa: E402

RNG = np.random.default_rng(42)


def _mk_ohlcv(closes: np.ndarray, vol_last_ratio: float = 1.0) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(closes, index=dates)
    high = close * (1 + 0.004)
    low = close * (1 - 0.004)
    openp = close.shift(1).fillna(close.iloc[0])
    vol = pd.Series(1_000_000.0, index=dates)
    vol.iloc[-1] *= vol_last_ratio
    df = pd.DataFrame({"open": openp, "high": high, "low": low,
                       "close": close, "volume": vol})
    return indicators.compute_panel(df)


def uptrend_pullback_panel() -> pd.DataFrame:
    """300 bars up ~0.15%/day, then pull back to the EMA50 and bounce."""
    n = 300
    base = 100 * np.cumprod(1 + np.full(n, 0.0015))
    closes = base.copy()
    # engineered pullback over last 6 bars: dip toward ema then recover
    dip = np.array([0.0, -0.01, -0.022, -0.032, -0.022, -0.012])
    closes[-6:] = closes[-7] * (1 + dip)
    panel = _mk_ohlcv(closes)
    # force the touch: set the low of bar -3 exactly onto the EMA50
    ema50 = panel["ema_50"].iloc[-3]
    panel.iloc[-3, panel.columns.get_loc("low")] = ema50 * 1.001
    return panel


def breakdown_panel() -> pd.DataFrame:
    n = 300
    base = 100 * np.cumprod(1 + np.full(n, 0.0015))
    closes = base.copy()
    closes[-8:] = closes[-9] * np.cumprod(np.full(8, 0.975))  # -2.5%/day
    return _mk_ohlcv(closes)


def sideways_panel() -> pd.DataFrame:
    closes = 100 + np.cumsum(RNG.normal(0, 0.2, 300))
    return _mk_ohlcv(closes)


SUPPORT_UPTREND = {
    "logic": "AND",
    "conditions": [
        {"type": "support_at_ma", "ma": "ema_50",
         "tolerance_pct": 1.5, "lookback": 3},
        {"type": "trend", "direction": "up"},
    ],
}


class TestSupportAtEMA:
    def test_pullback_matches(self):
        assert evaluate_symbol(uptrend_pullback_panel(), SUPPORT_UPTREND)

    def test_breakdown_rejected(self):
        assert not evaluate_symbol(breakdown_panel(), SUPPORT_UPTREND)

    def test_sideways_rejected(self):
        assert not evaluate_symbol(sideways_panel(), SUPPORT_UPTREND)

    def test_uptrend_without_touch_rejected(self):
        """Strong uptrend far above EMA50 shouldn't count as 'support'."""
        n = 300
        closes = 100 * np.cumprod(1 + np.full(n, 0.002))
        panel = _mk_ohlcv(closes)
        assert not evaluate_symbol(panel, SUPPORT_UPTREND)


class TestTrend:
    def test_up(self):
        p = _mk_ohlcv(100 * np.cumprod(1 + np.full(300, 0.0015)))
        assert evaluate_symbol(p, {"conditions": [
            {"type": "trend", "direction": "up"}]})

    def test_down(self):
        p = _mk_ohlcv(100 * np.cumprod(1 - np.full(300, 0.0015)))
        assert evaluate_symbol(p, {"conditions": [
            {"type": "trend", "direction": "down"}]})


class TestCross:
    def test_golden_cross_detected(self):
        # downtrend then sharp sustained rally forces ema20 above ema50
        closes = np.concatenate([
            100 * np.cumprod(1 - np.full(150, 0.001)),
            None or 100 * np.cumprod(1 - np.full(150, 0.001))[-1]
            * np.cumprod(1 + np.full(60, 0.008)),
        ])
        p = _mk_ohlcv(closes)
        fast_over = (p["ema_20"] > p["ema_50"]).astype(int).diff()
        cross_pos = np.where(fast_over == 1)[0]
        assert len(cross_pos) > 0, "synthetic series should cross"
        i = int(cross_pos[-1])
        sub = p.iloc[: i + 2]
        assert evaluate_symbol(sub, {"conditions": [
            {"type": "cross", "fast": "ema_20", "slow": "ema_50",
             "direction": "above", "lookback": 3}]})

    def test_no_false_cross(self):
        p = _mk_ohlcv(100 * np.cumprod(1 + np.full(300, 0.0015)))
        assert not evaluate_symbol(p, {"conditions": [
            {"type": "cross", "fast": "ema_20", "slow": "ema_50",
             "direction": "above", "lookback": 3}]})


class TestVolumeAndRange:
    def test_volume_spike(self):
        p = _mk_ohlcv(100 + np.zeros(300), vol_last_ratio=2.0)
        assert evaluate_symbol(p, {"conditions": [
            {"type": "volume_spike", "min_ratio": 1.5}]})
        assert not evaluate_symbol(p, {"conditions": [
            {"type": "volume_spike", "min_ratio": 2.5}]})

    def test_rsi_oversold_on_selloff(self):
        closes = np.concatenate([
            100 + np.zeros(250),
            100 * np.cumprod(1 - np.full(50, 0.01)),
        ])
        p = _mk_ohlcv(closes)
        assert evaluate_symbol(p, {"conditions": [
            {"type": "range", "field": "rsi", "max": 30}]})

    def test_change(self):
        closes = np.concatenate([100 + np.zeros(270),
                                 100 * np.cumprod(1 + np.full(30, 0.005))])
        p = _mk_ohlcv(closes)
        assert evaluate_symbol(p, {"conditions": [
            {"type": "change", "field": "close", "window": 21,
             "op": ">", "value_pct": 5}]})


class TestDSLValidation:
    def test_unknown_field_rejected(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "compare", "left": "pe_ratio",
                 "op": ">", "right": 10}]})

    def test_unknown_type_rejected(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "vibes"}]})

    def test_describe_roundtrip(self):
        txt = dsl.describe(dsl.validate(SUPPORT_UPTREND))
        assert "EMA 50" in txt and "uptrend" in txt


class TestRunScreen:
    def test_screen_over_universe(self):
        panels = {
            "GOODCO": uptrend_pullback_panel(),
            "BADCO": breakdown_panel(),
            "FLATCO": sideways_panel(),
        }
        res = run_screen(panels, dsl.validate(SUPPORT_UPTREND))
        assert list(res["symbol"]) == ["GOODCO"]

    def test_as_of_historical(self):
        panel = uptrend_pullback_panel()
        spec = dict(SUPPORT_UPTREND, as_of=str(panel.index[100].date()))
        # at bar 100 there was no engineered pullback yet
        assert not evaluate_symbol(panel, spec, spec["as_of"])


# ---------------------------------------------------------------- phase 2
from screener import sr  # noqa: E402
from screener.indicators import compute_weekly_panel  # noqa: E402
from tests.golden_harness import load_fixtures, canon  # noqa: E402


def range_bound_panel() -> pd.DataFrame:
    """Oscillates between ~100 (support) and ~110 (resistance), currently
    sitting just above support. Both levels have multiple touches."""
    seg = np.concatenate([
        np.linspace(100, 110, 25), np.linspace(110, 100, 25)])
    closes = np.concatenate([np.tile(seg, 6), np.linspace(100, 101.5, 10)])
    return _mk_ohlcv(closes)


class TestSwingSR:
    def test_levels_found(self):
        p = range_bound_panel()
        levels = sr.sr_levels(p, len(p) - 1)
        prices = [l for l, _ in levels]
        assert any(abs(x - 100) / 100 < 0.02 for x in prices), prices
        assert any(abs(x - 110) / 110 < 0.02 for x in prices), prices

    def test_near_support_matches(self):
        p = range_bound_panel()
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "near_support", "tolerance_pct": 2.5}]}))

    def test_near_resistance_rejected_at_support(self):
        p = range_bound_panel()
        assert not evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "near_resistance", "tolerance_pct": 2.0}]}))

    def test_breakout(self):
        seg = np.concatenate([
            np.linspace(100, 110, 25), np.linspace(110, 100, 25)])
        closes = np.concatenate([
            np.tile(seg, 6), np.linspace(100, 118, 8)])  # blast through 110
        p = _mk_ohlcv(closes)
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "breakout_resistance", "lookback": 5}]}))

    def test_no_lookahead_in_pivots(self):
        """A pivot must not exist until k bars print after it."""
        p = range_bound_panel()
        ph, _ = sr.find_pivots(p, k=5)
        assert not ph.iloc[-5:].any()


class TestWeekly:
    def test_weekly_uptrend(self):
        p = _mk_ohlcv(100 * np.cumprod(1 + np.full(600, 0.0015)))
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up", "timeframe": "weekly"}]})
        assert evaluate_symbol(p, spec)

    def test_weekly_field_restriction(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "range", "field": "adx", "min": 25,
                 "timeframe": "weekly"}]})

    def test_weekly_resample_shape(self):
        p = _mk_ohlcv(100 + np.zeros(300))
        w = compute_weekly_panel(p)
        assert 55 <= len(w) <= 65  # ~300/5 weeks


class TestRelStrength:
    def test_outperformer(self):
        p = _mk_ohlcv(100 * np.cumprod(1 + np.full(300, 0.002)))
        bench = pd.Series(100 * np.cumprod(1 + np.full(300, 0.0005)),
                          index=p.index)
        spec = dsl.validate({"conditions": [
            {"type": "rel_strength", "window": 63, "op": ">",
             "value_pct": 0}]})
        assert evaluate_symbol(p, spec, benchmark=bench)
        assert not evaluate_symbol(p, spec, benchmark=p["close"])  # vs self

    def test_missing_benchmark_fails_loud(self):
        p = _mk_ohlcv(100 + np.zeros(300))
        spec = dsl.validate({"conditions": [
            {"type": "rel_strength", "window": 63, "op": ">",
             "value_pct": 0}]})
        with pytest.raises(RuntimeError):
            evaluate_symbol(p, spec)


class TestGoldenOffline:
    def test_all_expected_specs_valid(self):
        for case in load_fixtures():
            if case["expected"] == {"error": True}:
                continue
            spec = dsl.validate(case["expected"])
            assert dsl.describe(spec)
            canon(spec)  # canonicalisation must not raise


# ---------------------------------------------------------------- web/explain
from fastapi.testclient import TestClient  # noqa: E402
from screener.webapp import app  # noqa: E402
from screener import explain  # noqa: E402


class TestExplain:
    def test_evidence_agrees_with_evaluator(self):
        p = uptrend_pullback_panel()
        ev = explain.explain_symbol(p, dsl.validate(SUPPORT_UPTREND))
        assert len(ev) == 2 and all(e["passed"] for e in ev)
        assert "EMA" in ev[0]["evidence"] or "ema" in ev[0]["evidence"]
        # breakdown: same conditions, both must be marked failed
        ev2 = explain.explain_symbol(breakdown_panel(),
                                     dsl.validate(SUPPORT_UPTREND))
        assert not any(e["passed"] for e in ev2)

    def test_weekly_and_rel_strength_evidence(self):
        p = _mk_ohlcv(100 * np.cumprod(1 + np.full(600, 0.002)))
        bench = pd.Series(100 * np.cumprod(1 + np.full(600, 0.0005)),
                          index=p.index)
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up", "timeframe": "weekly"},
            {"type": "rel_strength", "window": 63, "op": ">",
             "value_pct": 0}]})
        ev = explain.explain_symbol(p, spec, benchmark=bench)
        assert ev[0]["evidence"].startswith("[weekly]")
        assert ev[0]["passed"] and ev[1]["passed"]
        assert "pct pts" in ev[1]["evidence"]


class TestWebAPI:
    client = TestClient(app)

    def test_status_demo_mode(self):
        r = self.client.get("/api/status")
        assert r.status_code == 200
        assert r.json()["mode"] == "demo"  # no price store in CI

    def test_screen_endpoint_full_payload(self):
        r = self.client.post("/api/screen", json={"spec": SUPPORT_UPTREND})
        assert r.status_code == 200
        j = r.json()
        syms = [m["symbol"] for m in j["matches"]]
        assert "PULLBK" in syms and "BRKDWN" not in syms
        m = next(m for m in j["matches"] if m["symbol"] == "PULLBK")
        assert m["conditions_passed"] == m["conditions_total"] == 2
        assert all(e["passed"] and e["evidence"] for e in m["evidence"])
        assert j["english"].startswith("Screening for")
        assert j["stats"]["universe"] == 8
        assert "methodology" in j

    def test_screen_rejects_bad_spec(self):
        r = self.client.post("/api/screen", json={"spec": {
            "conditions": [{"type": "compare", "left": "pe_ratio",
                            "op": ">", "right": 5}]}})
        assert r.status_code == 422 and "pe_ratio" in r.json()["error"]

    def test_near_misses_reported(self):
        # oversold AND uptrend is contradictory in demo set -> near misses
        spec = {"logic": "AND", "conditions": [
            {"type": "range", "field": "rsi", "max": 30},
            {"type": "trend", "direction": "up"}]}
        j = self.client.post("/api/screen", json={"spec": spec}).json()
        assert j["stats"]["matched"] == 0
        assert j["stats"]["near_misses"] >= 1
        nm = j["near_misses"][0]
        assert nm["conditions_passed"] == 1


# ---------------------------------------------------------------- verify
from screener import verify  # noqa: E402


def _synth_store(n_sym=10, bars=1300, inject_bad=False):
    frames = []
    for k in range(n_sym):
        closes = 100 * np.cumprod(1 + RNG.normal(0.0005, 0.01, bars))
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize()
                               - pd.offsets.BDay(1), periods=bars)
        frames.append(pd.DataFrame({
            "date": dates, "symbol": f"SYM{k}",
            "open": closes, "high": closes * 1.01, "low": closes * 0.99,
            "close": closes, "volume": 1e6}))
    prices = pd.concat(frames, ignore_index=True)
    if inject_bad:
        prices.loc[5, "high"] = -1              # impossible bar
        prices = pd.concat([prices, prices.iloc[[10]]])  # duplicate
    uni = pd.DataFrame({"symbol": [f"SYM{k}" for k in range(n_sym + 1)],
                        "name": "x", "industry": "y"})  # 1 missing symbol
    bench = pd.Series(100 * np.cumprod(1 + np.full(bars, 0.0004)),
                      index=pd.bdate_range(
                          end=pd.Timestamp.today().normalize()
                          - pd.offsets.BDay(1), periods=bars))
    return prices, uni, bench


class TestVerify:
    def test_healthy_store_passes(self):
        prices, uni, bench = _synth_store()
        from screener.indicators import build_panels
        res = verify.verify_store(prices, uni, bench, build_panels(prices))
        fails = [n for n, s, _ in res if s == verify.FAIL]
        assert not fails, fails
        # 1 of 11 symbols missing -> coverage should be WARN not FAIL
        cov = next(x for x in res if x[0] == "symbol coverage")
        assert cov[1] == verify.WARN

    def test_bad_store_fails(self):
        prices, uni, bench = _synth_store(inject_bad=True)
        res = verify.verify_store(prices, uni, bench)
        by = {n: s for n, s, _ in res}
        assert by["bar integrity"] == verify.FAIL
        assert by["duplicate bars"] == verify.FAIL

    def test_missing_benchmark_fails(self):
        prices, uni, _ = _synth_store(n_sym=3, bars=600)
        res = verify.verify_store(prices, uni, None)
        by = {n: s for n, s, _ in res}
        assert by["benchmark (Nifty)"] == verify.FAIL
        assert verify.print_report(res) == 1


class TestJumpDiagnostics:
    def test_split_like_flagged(self):
        prices, uni, _ = _synth_store(n_sym=2, bars=400)
        # engineer an unadjusted 1:2 split on SYM0 and a -45% crash on SYM1
        i0 = prices[prices.symbol == "SYM0"].index[200]
        c0 = prices.loc[i0 - 1, "close"] if False else None
        mask0 = (prices.symbol == "SYM0") & (prices.index >= i0)
        prices.loc[mask0, ["open", "high", "low", "close"]] *= 0.5
        i1 = prices[prices.symbol == "SYM1"].index[300]
        mask1 = (prices.symbol == "SYM1") & (prices.index >= i1)
        prices.loc[mask1, ["open", "high", "low", "close"]] *= 0.55
        j = verify.list_jumps(prices)
        assert len(j) == 2
        hints = dict(zip(j["symbol"], j["hint"]))
        assert "UNADJUSTED" in hints["SYM0"]
        assert "real event" in hints["SYM1"]


# ---------------------------------------------------------------- patterns
def _candle_panel(rows):
    """rows: list of (o,h,l,c); padded with 60 flat warm-up bars."""
    pad = [(100, 100.5, 99.5, 100)] * 60
    data = pad + rows
    dates = pd.bdate_range("2024-01-01", periods=len(data))
    df = pd.DataFrame(data, columns=["open", "high", "low", "close"],
                      index=dates)
    df["volume"] = 1e6
    return indicators.compute_panel(df)


class TestCandles:
    def _match(self, panel, pattern, lookback=1):
        return evaluate_symbol(panel, dsl.validate({"conditions": [
            {"type": "candle", "pattern": pattern, "lookback": lookback}]}))

    def test_inside_bar(self):
        p = _candle_panel([(100, 106, 94, 103), (101, 104, 96, 99)])
        assert self._match(p, "inside_bar")
        assert not self._match(p, "nr7")

    def test_nr7(self):
        rows = [(100, 108, 92, 100)] * 6 + [(100, 101, 99.5, 100.5)]
        assert self._match(_candle_panel(rows), "nr7")

    def test_bullish_engulfing(self):
        p = _candle_panel([(104, 104.5, 99, 100), (99.5, 106, 99, 105)])
        assert self._match(p, "bullish_engulfing")
        assert not self._match(p, "bearish_engulfing")

    def test_hammer_and_star(self):
        h = _candle_panel([(103, 103.6, 95, 103.4)])   # long lower wick
        assert self._match(h, "hammer")
        s = _candle_panel([(97, 105, 96.6, 96.8)])     # long upper wick
        assert self._match(s, "shooting_star")
        assert not self._match(s, "hammer")

    def test_lookback(self):
        p = _candle_panel([(100, 106, 94, 103), (101, 104, 96, 99),
                           (98, 107, 97, 106)])  # inside bar 1 bar ago
        assert not self._match(p, "inside_bar", lookback=1)
        assert self._match(p, "inside_bar", lookback=2)


class TestConsolidation:
    def test_tight_range_and_flat_base(self):
        up = 100 * np.cumprod(1 + np.full(280, 0.002))
        flat = np.concatenate([up, up[-1] * (1 + RNG.normal(0, 0.004, 20))])
        p = _mk_ohlcv(flat)
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "tight_range", "bars": 15, "max_range_pct": 8}]}))
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "flat_base"}]}))
        # far below the 52w high -> tight range yes, flat base no
        crash = np.concatenate([up, up[-1] * 0.6
                                * (1 + RNG.normal(0, 0.004, 30))])
        pc = _mk_ohlcv(crash)
        assert evaluate_symbol(pc, dsl.validate({"conditions": [
            {"type": "tight_range", "bars": 15, "max_range_pct": 8}]}))
        assert not evaluate_symbol(pc, dsl.validate({"conditions": [
            {"type": "flat_base"}]}))

    def test_bb_squeeze(self):
        wild = 100 * np.cumprod(1 + RNG.normal(0, 0.02, 280))
        calm = np.concatenate([wild, wild[-1]
                               * (1 + RNG.normal(0, 0.001, 25))])
        assert evaluate_symbol(_mk_ohlcv(calm), dsl.validate({"conditions": [
            {"type": "bb_squeeze", "percentile": 20}]}))
        assert not evaluate_symbol(_mk_ohlcv(wild), dsl.validate(
            {"conditions": [{"type": "bb_squeeze", "percentile": 5}]}))


class TestPresets:
    def test_all_presets_validate_and_describe(self):
        from screener import presets
        assert len(presets.PRESETS) >= 12
        ids = [p["id"] for p in presets.PRESETS]
        assert len(ids) == len(set(ids))
        for p in presets.PRESETS:
            assert dsl.describe(dsl.validate(p["spec"]))
            assert p["description"] and p["group"]

    def test_presets_endpoint_and_screen(self):
        client = TestClient(app)
        r = client.get("/api/presets")
        assert r.status_code == 200
        items = r.json()
        assert any(i["id"] == "support_50ema_uptrend" for i in items)
        assert all("english" in i and "spec" in i for i in items)
        # every preset must actually run against the demo universe
        for i in items:
            rr = client.post("/api/screen", json={"spec": i["spec"]})
            assert rr.status_code == 200, i["id"]

    def test_pattern_explain(self):
        from screener import explain
        p = _candle_panel([(100, 106, 94, 103), (101, 104, 96, 99)])
        ev = explain.explain_symbol(p, dsl.validate({"conditions": [
            {"type": "candle", "pattern": "inside_bar"}]}))
        assert ev[0]["passed"] and "inside_bar on" in ev[0]["evidence"]
