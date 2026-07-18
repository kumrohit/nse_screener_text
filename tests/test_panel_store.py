"""Tests for the indicator-panel cache (ROADMAP Item 20 P1 —
screener/panel_store.py)."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest

from screener import config, indicators, panel_store


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """`config.price_store()`/`universe_file()`/`benchmark_store()` defer
    to plain `PRICE_STORE`/`UNIVERSE_FILE`/`BENCHMARK_STORE` attributes
    for the DEFAULT universe specifically (config.py's own comment: kept
    so existing `monkeypatch.setattr(config, "PRICE_STORE", ...)`-style
    fixtures keep working) — bound once at real-DATA_DIR import time, so
    patching `config.DATA_DIR` alone does NOT redirect a "nifty500"
    price-store path (same gotcha `tests/conftest.py`'s `_force_demo_mode`
    exists for). A test using the default-universe id that skips this
    would seed/read the REAL local price store instead of an isolated
    tmp one. `config.indicator_store()` has no such indirection (always
    derives fresh from DATA_DIR, cohorts_file()-style), so only the
    price-side attributes need patching here."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PRICE_STORE", tmp_path / "nifty500" /
                        "prices.parquet")
    monkeypatch.setattr(config, "UNIVERSE_FILE", tmp_path / "nifty500" /
                        "universe.csv")
    monkeypatch.setattr(config, "BENCHMARK_STORE", tmp_path / "nifty500" /
                        "benchmark.parquet")
    return tmp_path


def _synthetic_prices(n_symbols=3, n_bars=300) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2023-01-02", periods=n_bars)
    frames = []
    for k in range(n_symbols):
        closes = 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, n_bars))
        high = closes * 1.01
        low = closes * 0.99
        openp = pd.Series(closes).shift(1).fillna(closes[0]).values
        vol = rng.uniform(1e5, 1e6, n_bars)
        frames.append(pd.DataFrame({
            "symbol": f"SYM{k}", "date": dates,
            "open": openp, "high": high, "low": low,
            "close": closes, "volume": vol,
        }))
    return pd.concat(frames, ignore_index=True)


def _seed_price_store(data_dir, universe_id, prices):
    store = config.price_store(universe_id)
    store.parent.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(store, index=False)
    return store


class TestSaveAndLoadRoundtrip:
    def test_cached_panels_equal_freshly_built_exactly(self, tmp_data_dir):
        prices = _synthetic_prices()
        built = indicators.build_panels(prices)
        panel_store.save("nifty500", built)
        cached = panel_store._load_cached("nifty500")
        assert cached is not None
        assert set(cached) == set(built)
        for sym in built:
            pd.testing.assert_frame_equal(
                cached[sym], built[sym], check_freq=False)

    def test_load_or_build_returns_equivalent_panels_on_hit(
            self, tmp_data_dir):
        prices = _synthetic_prices()
        _seed_price_store(tmp_data_dir, "nifty500", prices)
        built = panel_store.load_or_build("nifty500", prices=prices)
        # second call: cache hit, no prices argument at all
        cached = panel_store.load_or_build("nifty500")
        for sym in built:
            pd.testing.assert_frame_equal(
                cached[sym], built[sym], check_freq=False)


class TestCacheHitSkipsPrices:
    def test_cache_hit_never_reads_prices_store(self, tmp_data_dir,
                                                monkeypatch):
        prices = _synthetic_prices()
        price_path = _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.load_or_build("nifty500", prices=prices)  # miss: builds+saves

        # the price store must still EXIST for the mtime invalidation
        # check itself (that's cheap — one stat() call, not a read) —
        # what a cache hit must never do is call pd.read_parquet on IT
        # specifically (the indicator cache's own parquet still needs
        # a real pd.read_parquet call, so guard by path, not blanket)
        real_read_parquet = pd.read_parquet

        def _guard(path, *a, **k):
            if str(path) == str(price_path):
                raise AssertionError(
                    "cache hit must not read the price store's contents")
            return real_read_parquet(path, *a, **k)
        monkeypatch.setattr(pd, "read_parquet", _guard)

        cached = panel_store.load_or_build("nifty500")
        assert len(cached) == 3

    def test_load_or_build_raises_clear_error_on_total_miss(
            self, tmp_data_dir):
        with pytest.raises(FileNotFoundError, match="nifty500"):
            panel_store.load_or_build("nifty500")


class TestInvalidation:
    def test_stale_after_price_store_mtime_changes(self, tmp_data_dir):
        prices = _synthetic_prices()
        store = _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.save("nifty500", indicators.build_panels(prices))
        assert panel_store._load_cached("nifty500") is not None

        time.sleep(1.01)  # mtime granularity
        store.write_bytes(store.read_bytes())  # rewrite -> new mtime
        assert panel_store._load_cached("nifty500") is None

    def test_stale_after_schema_version_bump(self, tmp_data_dir, monkeypatch):
        prices = _synthetic_prices()
        _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.save("nifty500", indicators.build_panels(prices))
        assert panel_store._load_cached("nifty500") is not None

        monkeypatch.setattr(panel_store, "SCHEMA_VERSION", 999)
        assert panel_store._load_cached("nifty500") is None

    def test_missing_meta_file_is_a_clean_miss(self, tmp_data_dir):
        prices = _synthetic_prices()
        _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.save("nifty500", indicators.build_panels(prices))
        panel_store._meta_path("nifty500").unlink()
        assert panel_store._load_cached("nifty500") is None

    def test_corrupted_meta_file_is_a_clean_miss_not_a_crash(
            self, tmp_data_dir):
        prices = _synthetic_prices()
        _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.save("nifty500", indicators.build_panels(prices))
        panel_store._meta_path("nifty500").write_text("{not valid json")
        assert panel_store._load_cached("nifty500") is None

    def test_no_cache_yet_is_none_not_an_error(self, tmp_data_dir):
        assert panel_store._load_cached("nifty500") is None


class TestPerUniverseIsolation:
    def test_two_universes_cache_independently(self, tmp_data_dir):
        prices_a = _synthetic_prices(n_symbols=2)
        prices_b = _synthetic_prices(n_symbols=4)
        panel_store.save("nifty500", indicators.build_panels(prices_a))
        panel_store.save("nse_full", indicators.build_panels(prices_b))
        assert len(panel_store._load_cached("nifty500")) == 2
        assert len(panel_store._load_cached("nse_full")) == 4

    def test_save_creates_universe_directory(self, tmp_data_dir):
        assert not (tmp_data_dir / "nse_etf").exists()
        prices = _synthetic_prices(n_symbols=1)
        panel_store.save("nse_etf", indicators.build_panels(prices))
        assert config.indicator_store("nse_etf").exists()


class TestMetaContents:
    def test_meta_records_schema_version_and_price_mtime(
            self, tmp_data_dir):
        prices = _synthetic_prices()
        store = _seed_price_store(tmp_data_dir, "nifty500", prices)
        panel_store.save("nifty500", indicators.build_panels(prices))
        meta = json.loads(panel_store._meta_path("nifty500").read_text())
        assert meta["schema_version"] == panel_store.SCHEMA_VERSION
        assert meta["price_mtime"] == pytest.approx(store.stat().st_mtime)
