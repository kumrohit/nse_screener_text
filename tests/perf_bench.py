"""Performance-regression harness (ROADMAP Item 20 P5).

Excluded from the default test run (`pyproject.toml`'s `addopts = "-m
'not perf'"`) — these are wall-clock timings against a synthetic
500-symbol universe, not unit tests, and their runtime doesn't belong
in the everyday `pytest tests/` loop. Run explicitly before every
version tag:

    pytest -m perf tests/perf_bench.py -v

Baselines below are the ROADMAP Item 20 measured numbers (chat
sandbox, 500 synthetic symbols x 1250 bars, 2026-07-13 — see
TECHNICAL_DESIGN.md §12m). A regression fails at >2x the recorded
baseline: loose enough to absorb machine-to-machine variance, tight
enough to catch a real slowdown before it ships.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from screener import dsl, evaluator, indicators

pytestmark = pytest.mark.perf

N_SYMBOLS = 500
N_BARS = 1250

BASELINE_BUILD_PANELS_SEC = 17.7
BASELINE_SCREEN_CHEAP_SEC = 0.11
BASELINE_SCREEN_SR_SEC = 0.6
REGRESSION_FACTOR = 2.0


def _synthetic_prices(n_symbols: int = N_SYMBOLS,
                      n_bars: int = N_BARS) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2021-06-01", periods=n_bars)
    frames = []
    for k in range(n_symbols):
        closes = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n_bars))
        high = closes * 1.01
        low = closes * 0.99
        openp = pd.Series(closes).shift(1).fillna(closes[0]).to_numpy()
        vol = rng.uniform(5e5, 5e6, n_bars)
        frames.append(pd.DataFrame({
            "symbol": f"SYM{k:04d}", "date": dates,
            "open": openp, "high": high, "low": low,
            "close": closes, "volume": vol,
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def synthetic_prices():
    return _synthetic_prices()


@pytest.fixture(scope="module")
def synthetic_panels(synthetic_prices):
    return indicators.build_panels(synthetic_prices)


class TestBuildPanelsBaseline:
    def test_build_panels_within_regression_bound(self, synthetic_prices):
        t0 = time.perf_counter()
        panels = indicators.build_panels(synthetic_prices)
        elapsed = time.perf_counter() - t0
        assert len(panels) == N_SYMBOLS
        assert elapsed < BASELINE_BUILD_PANELS_SEC * REGRESSION_FACTOR, (
            f"build_panels took {elapsed:.1f}s on {N_SYMBOLS} symbols, "
            f"baseline {BASELINE_BUILD_PANELS_SEC}s — >2x regression")


class TestScreenBaseline:
    def test_cheap_condition_screen_within_regression_bound(
            self, synthetic_panels):
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "range", "field": "rsi", "max": 60}]})
        t0 = time.perf_counter()
        evaluator.run_screen(synthetic_panels, spec)
        elapsed = time.perf_counter() - t0
        assert elapsed < BASELINE_SCREEN_CHEAP_SEC * REGRESSION_FACTOR, (
            f"cheap-condition screen took {elapsed:.2f}s, baseline "
            f"{BASELINE_SCREEN_CHEAP_SEC}s — >2x regression")

    def test_sr_condition_screen_within_regression_bound(
            self, synthetic_panels):
        spec = dsl.validate({"conditions": [
            {"type": "near_support", "tolerance_pct": 2.0}]})
        t0 = time.perf_counter()
        evaluator.run_screen(synthetic_panels, spec)
        elapsed = time.perf_counter() - t0
        assert elapsed < BASELINE_SCREEN_SR_SEC * REGRESSION_FACTOR, (
            f"S/R-condition screen took {elapsed:.2f}s, baseline "
            f"{BASELINE_SCREEN_SR_SEC}s — >2x regression")


class TestIndicatorCacheBaseline:
    """P1's whole point: a warm cache load must be dramatically faster
    than a cold build, not merely 'not slower.'"""

    def test_cache_hit_is_much_faster_than_cold_build(
            self, tmp_path, monkeypatch, synthetic_prices):
        from screener import config, panel_store
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        universe_id = "perf_bench_universe"
        store = config.price_store(universe_id)
        store.parent.mkdir(parents=True, exist_ok=True)
        synthetic_prices.to_parquet(store, index=False)

        t0 = time.perf_counter()
        panels = panel_store.load_or_build(universe_id, prices=synthetic_prices)
        cold_elapsed = time.perf_counter() - t0
        assert len(panels) == N_SYMBOLS

        t0 = time.perf_counter()
        cached = panel_store.load_or_build(universe_id)
        warm_elapsed = time.perf_counter() - t0
        assert len(cached) == N_SYMBOLS
        assert warm_elapsed < cold_elapsed / 3, (
            f"cache hit ({warm_elapsed:.2f}s) should be dramatically "
            f"faster than a cold build ({cold_elapsed:.2f}s)")
