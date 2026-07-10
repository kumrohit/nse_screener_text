"""Tests for the universe registry (ROADMAP Item 15 Phase A —
registry-lite). The scoped goal for this slice: prove the abstraction
(per-universe storage, `--universe` CLI threading, a state cache keyed
by universe) with zero behaviour change for the one registered universe,
`nifty500` — not add a second universe yet.
"""
from __future__ import annotations

import sys

import pandas as pd
import pytest

from screener import config, universes


class TestRegistry:
    def test_default_universe_registered(self):
        u = universes.get("nifty500")
        assert u.id == "nifty500"
        assert u.benchmark_ticker == "^NSEI"
        assert "constituents" in u.survivorship_note
        assert "delisted" in u.survivorship_note

    def test_unknown_universe_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="bogus_universe"):
            universes.get("bogus_universe")

    def test_default_universe_constant_is_registered(self):
        assert universes.DEFAULT_UNIVERSE in universes.UNIVERSES


class TestConfigPathHelpers:
    def test_default_universe_paths_match_plain_attributes(self):
        assert config.price_store() == config.PRICE_STORE
        assert config.price_store(universes.DEFAULT_UNIVERSE) == \
            config.PRICE_STORE
        assert config.universe_file() == config.UNIVERSE_FILE
        assert config.benchmark_store() == config.BENCHMARK_STORE

    def test_hypothetical_second_universe_gets_its_own_directory(self):
        """No second universe is registered yet, but the path helper
        itself must already generalise — this is the whole point of
        building the registry now rather than when nse_full lands."""
        p = config.price_store("nse_full")
        assert p == config.DATA_DIR / "nse_full" / "prices.parquet"
        assert p != config.PRICE_STORE


class TestLegacyMigration:
    def test_migration_moves_flat_files_into_universe_dir(self, tmp_path,
                                                           monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "prices.parquet").write_bytes(b"fake-prices")
        (data_dir / "nifty500.csv").write_text("symbol\nFOO\n")
        (data_dir / "benchmark.parquet").write_bytes(b"fake-benchmark")

        monkeypatch.setattr(config, "DATA_DIR", data_dir)
        monkeypatch.setattr(config, "PRICE_STORE",
                            data_dir / "nifty500" / "prices.parquet")
        monkeypatch.setattr(config, "UNIVERSE_FILE",
                            data_dir / "nifty500" / "universe.csv")
        monkeypatch.setattr(config, "BENCHMARK_STORE",
                            data_dir / "nifty500" / "benchmark.parquet")

        config._migrate_legacy_nifty500_layout()

        assert (data_dir / "nifty500" / "prices.parquet").read_bytes() == \
            b"fake-prices"
        assert (data_dir / "nifty500" / "universe.csv").exists()
        assert (data_dir / "nifty500" / "benchmark.parquet").read_bytes() \
            == b"fake-benchmark"
        assert not (data_dir / "prices.parquet").exists()

    def test_migration_idempotent_second_call_is_a_noop(self, tmp_path,
                                                         monkeypatch):
        data_dir = tmp_path / "data"
        (data_dir / "nifty500").mkdir(parents=True)
        (data_dir / "nifty500" / "prices.parquet").write_bytes(b"real-data")

        monkeypatch.setattr(config, "DATA_DIR", data_dir)
        monkeypatch.setattr(config, "PRICE_STORE",
                            data_dir / "nifty500" / "prices.parquet")
        monkeypatch.setattr(config, "UNIVERSE_FILE",
                            data_dir / "nifty500" / "universe.csv")
        monkeypatch.setattr(config, "BENCHMARK_STORE",
                            data_dir / "nifty500" / "benchmark.parquet")

        config._migrate_legacy_nifty500_layout()  # no legacy files: no-op
        assert (data_dir / "nifty500" / "prices.parquet").read_bytes() == \
            b"real-data"

    def test_migration_harmless_when_nothing_to_migrate(self, tmp_path,
                                                         monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(config, "DATA_DIR", data_dir)
        monkeypatch.setattr(config, "PRICE_STORE",
                            data_dir / "nifty500" / "prices.parquet")
        monkeypatch.setattr(config, "UNIVERSE_FILE",
                            data_dir / "nifty500" / "universe.csv")
        monkeypatch.setattr(config, "BENCHMARK_STORE",
                            data_dir / "nifty500" / "benchmark.parquet")
        config._migrate_legacy_nifty500_layout()  # must not raise
        assert not (data_dir / "nifty500").exists()


class TestUniverseFetchThreading:
    def test_fetch_universe_reads_from_universe_id_path(self, tmp_path,
                                                         monkeypatch):
        # nse_full (not the default universe) so config.universe_file()
        # actually recomputes from DATA_DIR rather than deferring to the
        # UNIVERSE_FILE attribute the way it does for the default.
        from screener import universe as uni_mod
        data_dir = tmp_path / "data"
        (data_dir / "nse_full").mkdir(parents=True)
        (data_dir / "nse_full" / "universe.csv").write_text(
            "symbol,name,industry,yf_ticker\nFOO,Foo Ltd,,FOO.NS\n")
        monkeypatch.setattr(config, "DATA_DIR", data_dir)
        df = uni_mod.fetch_universe(universe_id="nse_full")
        assert list(df["symbol"]) == ["FOO"]

    def test_fetch_universe_unknown_id_raises(self, tmp_path, monkeypatch):
        from screener import universe as uni_mod
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
        with pytest.raises(ValueError, match="no symbol-list fetcher"):
            uni_mod.fetch_universe(universe_id="bogus_universe")


class TestScreenLogUniverseField:
    def test_log_run_writes_universe_and_read_defaults_missing(
            self, tmp_path, monkeypatch):
        from screener import dsl, webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")

        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"}]})
        webapp._log_run(spec, "2026-01-01", {"matched": 0, "evaluated": 0},
                        [], dsl.spec_hash(spec), "nifty500")

        # a pre-Item-15 entry with no "universe" key at all
        import json as _json
        with open(webapp.LOG_FILE, "a") as fh:
            fh.write(_json.dumps({
                "ts": "2020-01-01T00:00:00", "as_of": "2020-01-01",
                "spec": spec, "english": "old entry", "stats": {},
                "config_hash": "x", "spec_hash": "oldhash",
                "matched": [],
            }) + "\n")

        # screen_log() reverses to most-recent-first: [0] is the legacy
        # entry appended last (no "universe" key, defaulted on read),
        # [1] is the explicit one written via _log_run above.
        client_entries = webapp.screen_log(limit=10)
        assert client_entries[0]["universe"] == "nifty500"
        assert client_entries[1]["universe"] == "nifty500"

    def test_log_run_default_universe_param(self, tmp_path, monkeypatch):
        from screener import dsl, webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"}]})
        webapp._log_run(spec, "2026-01-01", {"matched": 0, "evaluated": 0},
                        [], dsl.spec_hash(spec))  # universe_id omitted
        entry = webapp.screen_log(limit=1)[0]
        assert entry["universe"] == "nifty500"


class TestCLIUniverseFlag:
    def test_screen_dry_run_accepts_universe_flag(self, capsys, monkeypatch):
        from screener import cli
        monkeypatch.setattr(sys, "argv", [
            "screener", "screen", "--json", "--dry-run",
            "--universe", "nifty500",
            '{"conditions":[{"type":"trend","direction":"up"}]}'])
        cli.main()
        out = capsys.readouterr().out
        assert "uptrend" in out

    def test_screen_rejects_unknown_universe(self, monkeypatch):
        from screener import cli
        monkeypatch.setattr(sys, "argv", [
            "screener", "screen", "--json", "--dry-run",
            "--universe", "bogus_universe",
            '{"conditions":[{"type":"trend","direction":"up"}]}'])
        with pytest.raises(SystemExit):
            cli.main()

    def test_screen_dry_run_accepts_nse_full(self, capsys, monkeypatch):
        from screener import cli
        monkeypatch.setattr(sys, "argv", [
            "screener", "screen", "--json", "--dry-run",
            "--universe", "nse_full",
            '{"conditions":[{"type":"trend","direction":"up"}]}'])
        cli.main()  # dry-run: must not raise, no data access needed
        out = capsys.readouterr().out
        assert "uptrend" in out

    def test_screen_dry_run_accepts_nse_etf(self, capsys, monkeypatch):
        from screener import cli
        monkeypatch.setattr(sys, "argv", [
            "screener", "screen", "--json", "--dry-run",
            "--universe", "nse_etf",
            '{"conditions":[{"type":"trend","direction":"up"}]}'])
        cli.main()
        out = capsys.readouterr().out
        assert "uptrend" in out


class TestNseFullUniverse:
    def test_registered_with_stricter_liquidity_gate(self):
        u = universes.get("nse_full")
        assert u.id == "nse_full"
        assert u.liquidity_gate_cr > universes.get("nifty500").liquidity_gate_cr
        assert "sector" in u.survivorship_note.lower()

    def test_fetch_filters_to_eq_series_and_leaves_industry_empty(
            self, tmp_path, monkeypatch):
        from screener import universe as uni_mod

        raw_csv = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, "
            "PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "FOO,Foo Limited,EQ,01-JAN-2000,10,1,INE000000001,10\n"
            "BAR,Bar Limited,BE,01-JAN-2000,10,1,INE000000002,10\n"
        )

        class _FakeResp:
            status_code = 200
            text = raw_csv
            def raise_for_status(self): pass

        monkeypatch.setattr(uni_mod.requests, "get",
                            lambda *a, **k: _FakeResp())
        ufile = tmp_path / "nse_full" / "universe.csv"
        df = uni_mod._fetch_nse_full(force_refresh=True, ufile=ufile)

        assert list(df["symbol"]) == ["FOO"]  # BE series excluded
        assert df["yf_ticker"].iloc[0] == "FOO.NS"
        assert df["industry"].isna().all()
        assert ufile.exists()  # cached for next call

    def test_fetch_falls_back_to_cache_on_network_failure(self, tmp_path,
                                                           monkeypatch):
        from screener import universe as uni_mod
        data_dir = tmp_path / "data"
        (data_dir / "nse_full").mkdir(parents=True)
        (data_dir / "nse_full" / "universe.csv").write_text(
            "symbol,name,industry,yf_ticker\nCACHED,Cached Ltd,,CACHED.NS\n")
        monkeypatch.setattr(config, "DATA_DIR", data_dir)

        def _boom(*a, **k):
            raise ConnectionError("offline")
        monkeypatch.setattr(uni_mod.requests, "get", _boom)

        df = uni_mod.fetch_universe(force_refresh=True, universe_id="nse_full")
        assert list(df["symbol"]) == ["CACHED"]


class TestNseEtfUniverse:
    def test_registered_as_an_equity_index_universe(self):
        u = universes.get("nse_etf")
        assert u.id == "nse_etf"
        assert u.benchmark_ticker == "^NSEI"
        assert "equity-index" in u.survivorship_note.lower() or \
            "curated" in u.survivorship_note.lower()

    def test_fetch_filters_to_curated_symbols_only(self, tmp_path,
                                                    monkeypatch):
        from screener import universe as uni_mod

        raw_csv = (
            "Symbol,Underlying,SecurityName,DateofListing,MarketLot,"
            "ISINNumber,FaceValue\n"
            "NIFTYBEES,Nifty50,NIPINDETFNIFTYBEES,08-Jan-02,1,"
            "INF204KB14I2,1\n"
            "GOLDBEES,Gold,NIPINDETFGOLDBEES,19-Mar-07,1,INF204KB17I5,1\n"
        )

        class _FakeResp:
            status_code = 200
            text = raw_csv
            def raise_for_status(self): pass

        monkeypatch.setattr(uni_mod.requests, "get",
                            lambda *a, **k: _FakeResp())
        ufile = tmp_path / "nse_etf" / "universe.csv"
        df = uni_mod._fetch_nse_etf(force_refresh=True, ufile=ufile)

        # NIFTYBEES is in the curated whitelist, GOLDBEES is not (gold,
        # excluded by the "equity-index ETFs only" v1 scope) even though
        # both are present in the raw NSE fetch.
        assert list(df["symbol"]) == ["NIFTYBEES"]
        assert df["yf_ticker"].iloc[0] == "NIFTYBEES.NS"
        assert df["industry"].isna().all()

    def test_curated_symbol_set_excludes_known_non_equity_etfs(self):
        from screener.universe import NSE_ETF_SYMBOLS
        assert "GOLDBEES" not in NSE_ETF_SYMBOLS
        assert "LIQUIDBEES" not in NSE_ETF_SYMBOLS
        assert "NIFTYBEES" in NSE_ETF_SYMBOLS
        assert "BANKBEES" in NSE_ETF_SYMBOLS

    def test_fetch_falls_back_to_cache_on_network_failure(self, tmp_path,
                                                           monkeypatch):
        from screener import universe as uni_mod
        data_dir = tmp_path / "data"
        (data_dir / "nse_etf").mkdir(parents=True)
        (data_dir / "nse_etf" / "universe.csv").write_text(
            "symbol,name,industry,yf_ticker\nCACHED,Cached Ltd,,CACHED.NS\n")
        monkeypatch.setattr(config, "DATA_DIR", data_dir)

        def _boom(*a, **k):
            raise ConnectionError("offline")
        monkeypatch.setattr(uni_mod.requests, "get", _boom)

        df = uni_mod.fetch_universe(force_refresh=True, universe_id="nse_etf")
        assert list(df["symbol"]) == ["CACHED"]


class TestLiquidityGateThreading:
    def test_default_universe_defers_to_overridable_constant(self,
                                                              monkeypatch):
        monkeypatch.setattr(config, "MIN_MEDIAN_TURNOVER_CR", 1.25)
        assert config.liquidity_gate_cr() == 1.25
        assert config.liquidity_gate_cr("nifty500") == 1.25

    def test_nse_full_uses_its_own_registry_value_not_the_global(self,
                                                                  monkeypatch):
        monkeypatch.setattr(config, "MIN_MEDIAN_TURNOVER_CR", 1.25)
        assert config.liquidity_gate_cr("nse_full") == \
            universes.get("nse_full").liquidity_gate_cr
        assert config.liquidity_gate_cr("nse_full") != 1.25
