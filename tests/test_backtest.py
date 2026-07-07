"""Tests for the screen backtester (ROADMAP Item 14 — screener/backtest.py).

Covers the locked methodology (cooldown dedup, entry-at-open[t+1],
same-date universe baseline, gross/net costs, <30-events suppression,
deterministic bootstrap) and the "CRITICAL acceptance test" from the
spec: vectorized signals must exactly match evaluator.evaluate_symbol()
— for cheap condition types everywhere, for expensive/stride-approximated
ones only at the stride-grid dates (the documented approximation).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from screener import backtest, dsl, indicators

RNG = np.random.default_rng(7)


# ------------------------------------------------------------ shared helpers
def _mk_ohlcv(closes: np.ndarray, band: float = 0.004,
              vol: float = 1_000_000.0) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(closes, index=dates)
    high, low = close * (1 + band), close * (1 - band)
    openp = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(vol, index=dates)
    df = pd.DataFrame({"open": openp, "high": high, "low": low,
                       "close": close, "volume": volume})
    return indicators.compute_panel(df)


def _universe_df(symbols, sectors=None):
    sectors = sectors or ["Information Technology"] * len(symbols)
    return pd.DataFrame({"symbol": symbols, "name": symbols,
                        "industry": sectors})


def _random_walk_panels(n_symbols=12, n_bars=320, seed=0):
    rng = np.random.default_rng(seed)
    panels = {}
    for k in range(n_symbols):
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.012, n_bars))
        panels[f"SYM{k}"] = _mk_ohlcv(closes)
    return panels


# ============================================================ dedup
class TestDedupEvents:
    def test_consecutive_true_days_collapse_to_one_event(self):
        idx_true = np.arange(10, 18)  # 8 consecutive True bars
        events = backtest._dedup_events(idx_true, cooldown=20)
        assert events == [10]

    def test_recurrence_after_cooldown_creates_second_event(self):
        idx_true = np.concatenate([np.arange(10, 18), np.arange(40, 45)])
        events = backtest._dedup_events(idx_true, cooldown=20)
        assert events == [10, 40]

    def test_recurrence_within_cooldown_stays_one_event(self):
        idx_true = np.concatenate([np.arange(10, 18), np.arange(25, 28)])
        events = backtest._dedup_events(idx_true, cooldown=20)
        assert events == [10]  # 25-10=15 <= 20, still suppressed


# ============================================================ entry convention
class TestEntryConvention:
    def test_forward_return_uses_open_t_plus_1(self):
        n = 300
        closes = np.full(n, 100.0)
        closes[150] = 1000.0  # single-day spike; reverts next bar
        panel = _mk_ohlcv(closes)
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 500}]}
        result = backtest.backtest_spec(
            {"SPK": panel}, None, screen, horizons=(5,),
            min_turnover_cr=0, sensitivity=False)
        assert len(result["events"]) == 1
        ev = result["events"][0]
        t = panel.index.get_loc(pd.Timestamp(ev["signal_date"]))
        expected_entry = panel["open"].iloc[t + 1]
        expected_gross = panel["close"].iloc[t + 5] / expected_entry - 1
        assert expected_entry == pytest.approx(panel["close"].iloc[t])
        assert ev["gross_5"] == pytest.approx(expected_gross)

    def test_event_near_panel_end_excluded_only_from_long_horizons(self):
        n = 300
        closes = np.full(n, 100.0)
        closes[n - 8] = 500.0  # spike leaves room for h=5 but not h=60
        panel = _mk_ohlcv(closes)
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 400}]}
        result = backtest.backtest_spec(
            {"SPK": panel}, None, screen, horizons=(5, 60),
            min_turnover_cr=0, sensitivity=False)
        assert len(result["events"]) == 1
        ev = result["events"][0]
        assert ev["gross_5"] is not None and not pd.isna(ev["gross_5"])
        assert pd.isna(ev["gross_60"])


# ============================================================ JSON serialization
class TestJSONSerialization:
    def test_result_with_horizon_truncated_events_is_json_serializable(self):
        """Regression: an event whose horizon runs past the panel's end
        stores NaN in that event's row. The first live /api/backtest
        call crashed with a 500 because json.dumps() rejects raw NaN —
        the result dict must carry `None` there instead."""
        n = 300
        closes = np.full(n, 100.0)
        closes[n - 8] = 500.0  # spike leaves no room for a 60-bar horizon
        panel = _mk_ohlcv(closes)
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 400}]}
        result = backtest.backtest_spec(
            {"SPK": panel}, None, screen, horizons=(5, 60),
            min_turnover_cr=0, sensitivity=False)
        assert any(e["gross_60"] is None for e in result["events"])
        json.dumps(result)  # must not raise


# ============================================================ baseline
class TestBaseline:
    def test_hand_computed_three_symbol_baseline(self):
        n, h = 300, 5
        rA = np.full(n, 100.0)
        rA[150] = 1000.0  # A's own signal + entry-day distortion
        panelA = _mk_ohlcv(rA)
        panelB = _mk_ohlcv(100 * np.cumprod(np.full(n, 1.001)))
        panelC = _mk_ohlcv(100 * np.cumprod(np.full(n, 1.002)))
        panels = {"A": panelA, "B": panelB, "C": panelC}

        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 500}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(h,), min_turnover_cr=0,
            sensitivity=False)
        assert len(result["events"]) == 1
        ev = result["events"][0]
        t = panelA.index.get_loc(pd.Timestamp(ev["signal_date"]))

        a_fwd = panelA["close"].iloc[t + h] / panelA["open"].iloc[t + 1] - 1
        b_fwd = panelB["close"].iloc[t + h] / panelB["open"].iloc[t + 1] - 1
        c_fwd = panelC["close"].iloc[t + h] / panelC["open"].iloc[t + 1] - 1
        expected_baseline = (a_fwd + b_fwd + c_fwd) / 3

        assert ev["gross_5"] == pytest.approx(a_fwd)
        assert ev["baseline_5"] == pytest.approx(expected_baseline)
        assert ev["excess_gross_5"] == pytest.approx(a_fwd - expected_baseline)


# ============================================================ costs
class TestCosts:
    def test_net_equals_gross_minus_round_trip_cost(self):
        n = 300
        closes = np.full(n, 100.0)
        closes[150] = 500.0
        panel = _mk_ohlcv(closes)
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 400}]}
        result = backtest.backtest_spec(
            {"SPK": panel}, None, screen, horizons=(5, 20),
            cost_pct=0.30, min_turnover_cr=0, sensitivity=False)
        ev = result["events"][0]
        assert ev["net_5"] == pytest.approx(ev["gross_5"] - 0.30 / 100)
        assert ev["net_20"] == pytest.approx(ev["gross_20"] - 0.30 / 100)


# ============================================================ insufficient events
class TestInsufficientEvents:
    def test_below_min_events_suppresses_stats(self):
        panels = _random_walk_panels(n_symbols=3, n_bars=200, seed=1)
        # a spec that fires (if ever) on very few dates across 3 symbols
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 1e9}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(20,), min_turnover_cr=0,
            min_events=30, sensitivity=False)
        h = result["horizons"][20]
        assert h["count"] == 0
        assert h["insufficient"] is True
        assert h["excess_net"] is None


# ============================================================ bootstrap determinism
class TestBootstrapDeterminism:
    def _events_df(self, n=40, seed=0):
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range("2022-01-03", periods=n)
        gross = rng.normal(0.01, 0.05, n)
        base = rng.normal(0.0, 0.03, n)
        df = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(n)],
            "signal_date": dates,
            "gross_20": gross, "net_20": gross - 0.003,
            "baseline_20": base,
            "excess_gross_20": gross - base,
            "excess_net_20": (gross - 0.003) - base,
        })
        return df

    def test_same_seed_reproducible(self):
        df = self._events_df()
        r1 = backtest._horizon_stats(df, 20, min_events=30,
                                     bootstrap_n=500, bootstrap_seed=7)
        r2 = backtest._horizon_stats(df, 20, min_events=30,
                                     bootstrap_n=500, bootstrap_seed=7)
        assert r1["bootstrap_ci_excess_net_mean"] == \
            r2["bootstrap_ci_excess_net_mean"]

    def test_different_seed_differs(self):
        df = self._events_df()
        r1 = backtest._horizon_stats(df, 20, min_events=30,
                                     bootstrap_n=500, bootstrap_seed=7)
        r2 = backtest._horizon_stats(df, 20, min_events=30,
                                     bootstrap_n=500, bootstrap_seed=8)
        assert r1["bootstrap_ci_excess_net_mean"] != \
            r2["bootstrap_ci_excess_net_mean"]


# ============================================================ engineered edge / null
def _drift_raw_frames(n_symbols=40, n_bars=300, drift_pct=5.0,
                      drift_bars=20, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n_bars)
    frames, own_marker = {}, {}
    for k in range(n_symbols):
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.002, n_bars))
        marker_pos = int(rng.integers(60, n_bars - drift_bars - 10))
        end = marker_pos + 1 + drift_bars
        drift_path = np.linspace(0, drift_pct / 100, drift_bars)
        closes[marker_pos + 1:end] = closes[marker_pos] * (1 + drift_path)
        closes[end:] = closes[end - 1]
        close = pd.Series(closes, index=dates)
        high, low = close * 1.004, close * 0.996
        openp = close.shift(1).fillna(close.iloc[0])
        vol = pd.Series(1_000_000.0, index=dates)
        sym = f"SYM{k}"
        frames[sym] = pd.DataFrame({"open": openp, "high": high,
                                    "low": low, "close": close,
                                    "volume": vol})
        own_marker[sym] = marker_pos
    return frames, own_marker


def _panels_with_spike(frames, marker_positions, ratio=5.0):
    panels = {}
    for sym, df in frames.items():
        df = df.copy()
        col = df.columns.get_loc("volume")
        df.iloc[marker_positions[sym], col] *= ratio
        panels[sym] = indicators.compute_panel(df)
    return panels


class TestEngineeredEdgeAndNull:
    SCREEN = {"conditions": [
        {"type": "volume_spike", "min_ratio": 3.0}]}

    def test_aligned_signal_finds_positive_excess(self):
        frames, own_marker = _drift_raw_frames(seed=2)
        panels = _panels_with_spike(frames, own_marker)
        result = backtest.backtest_spec(
            panels, None, self.SCREEN, horizons=(20,), min_turnover_cr=0,
            sensitivity=False)
        h = result["horizons"][20]
        assert not h["insufficient"]
        assert h["count"] >= 30
        assert h["excess_net"]["mean"] > 0.02  # ~5% engineered, allow slack

    def test_shuffled_signal_finds_no_excess(self):
        frames, own_marker = _drift_raw_frames(seed=2)
        syms = list(frames)
        shuffled = {sym: own_marker[syms[(i + 1) % len(syms)]]
                   for i, sym in enumerate(syms)}
        panels = _panels_with_spike(frames, shuffled)
        result = backtest.backtest_spec(
            panels, None, self.SCREEN, horizons=(20,), min_turnover_cr=0,
            sensitivity=False)
        h = result["horizons"][20]
        assert not h["insufficient"]
        assert abs(h["excess_net"]["mean"]) < 0.015


# ============================================================ sensitivity grid
class TestSensitivityGrid:
    def test_grid_structure_and_verdict(self):
        frames, own_marker = _drift_raw_frames(n_symbols=40, seed=3)
        panels = _panels_with_spike(frames, own_marker)
        screen = {"conditions": [
            {"type": "volume_spike", "min_ratio": 3.0}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(20,), min_turnover_cr=0,
            sensitivity=True)
        grid = result["sensitivity"]
        assert grid is not None
        row = grid[0]
        assert row["param"] == "min_ratio"
        assert len(row["cells"]) == 4
        assert row["verdict"] in (
            "robust across range",
            "edge concentrated at one value — treat as curve-fit")


# ============================================================ vectorizer consistency
class TestVectorizerConsistency:
    def test_cheap_conditions_match_evaluator_everywhere(self):
        panels = _random_walk_panels(n_symbols=10, n_bars=320, seed=4)
        screen = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "range", "field": "rsi", "min": 30, "max": 70},
        ]})
        res = backtest.verify_vectorizer_consistency(
            panels, None, screen, n_samples=150, seed=1)
        assert res["checked"] >= 150
        assert res["mismatches"] == []

    def test_expensive_symbol_type_matches_at_stride_grid(self):
        panels = _random_walk_panels(n_symbols=10, n_bars=320, seed=5)
        screen = dsl.validate({"conditions": [
            {"type": "near_support", "tolerance_pct": 5.0}]})
        res = backtest.verify_vectorizer_consistency(
            panels, None, screen, n_samples=80, seed=2, stride=5)
        assert res["checked"] >= 80
        assert res["mismatches"] == []

    def test_expensive_cross_sectional_type_matches_at_stride_grid(self):
        panels = _random_walk_panels(n_symbols=12, n_bars=320, seed=6)
        universe = _universe_df(list(panels))
        screen = dsl.validate({"conditions": [
            {"type": "rs_percentile", "op": ">", "value": 50}]})
        res = backtest.verify_vectorizer_consistency(
            panels, universe, screen, n_samples=80, seed=3, stride=5)
        assert res["checked"] >= 80
        assert res["mismatches"] == []

    def test_weekly_timeframe_condition_matches_everywhere(self):
        panels = _random_walk_panels(n_symbols=8, n_bars=400, seed=7)
        screen = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up", "timeframe": "weekly"}]})
        res = backtest.verify_vectorizer_consistency(
            panels, None, screen, n_samples=100, seed=4)
        assert res["checked"] >= 100
        assert res["mismatches"] == []


# ============================================================ liquidity filtering
class TestLiquidityFiltering:
    def test_illiquid_symbol_excluded_from_baseline_and_events(self):
        n, h = 300, 5
        closes_a = np.full(n, 100.0)
        closes_a[150] = 500.0
        panel_a = _mk_ohlcv(closes_a, vol=2_000_000.0)  # liquid
        panel_b = _mk_ohlcv(closes_a, vol=10.0)          # illiquid twin

        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 400}]}
        result = backtest.backtest_spec(
            {"A": panel_a, "B": panel_b}, None, screen, horizons=(h,),
            min_turnover_cr=1.0, sensitivity=False)
        # only A should have generated an event (B is illiquid)
        assert {e["symbol"] for e in result["events"]} == {"A"}
        ev = result["events"][0]
        t = panel_a.index.get_loc(pd.Timestamp(ev["signal_date"]))
        a_fwd = panel_a["close"].iloc[t + h] / panel_a["open"].iloc[t + 1] - 1
        # baseline should be A's own return only (B excluded), not the
        # average of A and B
        assert ev["baseline_5"] == pytest.approx(a_fwd)


# ============================================================ metadata
class TestMetadata:
    def test_hypothesis_passthrough(self):
        panels = _random_walk_panels(n_symbols=3, n_bars=200, seed=8)
        screen = {"conditions": [{"type": "range", "field": "rsi",
                                  "min": 0, "max": 100}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(5,), min_turnover_cr=0,
            hypothesis="expect +2% 20-bar excess", sensitivity=False)
        assert result["hypothesis"] == "expect +2% 20-bar excess"

    def test_survivorship_note_always_present(self):
        panels = _random_walk_panels(n_symbols=3, n_bars=200, seed=9)
        screen = {"conditions": [
            {"type": "compare", "left": "close", "op": ">", "right": 1e9}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(5,), min_turnover_cr=0,
            sensitivity=False)
        assert "Survivorship" in result["survivorship_note"]
        assert "current" in result["survivorship_note"].lower()


# ============================================================ event timeline
class TestEventTimeline:
    def test_timeline_counts_sum_to_total_events(self):
        frames, own_marker = _drift_raw_frames(n_symbols=25, seed=10)
        panels = _panels_with_spike(frames, own_marker)
        screen = {"conditions": [
            {"type": "volume_spike", "min_ratio": 3.0}]}
        result = backtest.backtest_spec(
            panels, None, screen, horizons=(20,), min_turnover_cr=0,
            sensitivity=False)
        assert sum(result["event_timeline"].values()) == \
            result["n_events_total"]
        for month_key in result["event_timeline"]:
            assert len(month_key) == 7 and month_key[4] == "-"
