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
        with pytest.raises(ValueError, match="nse_full"):
            universes.get("nse_full")

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
        from screener import universe as uni_mod
        data_dir = tmp_path / "data"
        (data_dir / "acme").mkdir(parents=True)
        (data_dir / "acme" / "universe.csv").write_text(
            "symbol,name,industry,yf_ticker\nFOO,Foo Ltd,Services,FOO.NS\n")
        monkeypatch.setattr(config, "DATA_DIR", data_dir)
        df = uni_mod.fetch_universe(universe_id="acme")
        assert list(df["symbol"]) == ["FOO"]


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
            "--universe", "nse_full",
            '{"conditions":[{"type":"trend","direction":"up"}]}'])
        with pytest.raises(SystemExit):
            cli.main()
