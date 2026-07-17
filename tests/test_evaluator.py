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
from screener.evaluator import (evaluate_symbol, run_screen,  # noqa: E402
                                sector_data_gap_warning)

RNG = np.random.default_rng(42)


def _mk_ohlcv(closes: np.ndarray, vol_last_ratio: float = 1.0,
              band: float = 0.004) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(closes, index=dates)
    high = close * (1 + band)
    low = close * (1 - band)
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

    def test_unknown_sector_rejected(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "sector", "in": ["Crypto"]}]})

    def test_valid_sector_accepted(self):
        spec = dsl.validate({"conditions": [
            {"type": "sector", "in": ["Information Technology"]}]})
        assert "Information Technology" in dsl.describe(spec)

    def test_sector_rank_requires_exactly_one_of_top_bottom(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "sector_rank"}]})
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "sector_rank", "top": 3, "bottom": 3}]})


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


class TestLogSchemaVersioning:
    """ROADMAP Item 18 v1.0 hardening: screen_log/backtest_log entries
    gained a `universe` field at different points (Item 15, Item 16)
    via scattered .setdefault()/`.get(default=...)` calls at different
    read sites — migrate_screen_log_entry()/migrate_backtest_log_entry()
    centralise that into one function per store."""

    def test_migrate_screen_log_entry_defaults_universe(self):
        from screener import webapp
        legacy = {"ts": "x", "spec": {}, "matched": []}
        migrated = webapp.migrate_screen_log_entry(dict(legacy))
        assert migrated["universe"] == "nifty500"
        assert migrated["schema_version"] == \
            webapp.SCREEN_LOG_SCHEMA_VERSION

    def test_migrate_screen_log_entry_preserves_explicit_universe(self):
        from screener import webapp
        entry = {"ts": "x", "universe": "nse_full"}
        migrated = webapp.migrate_screen_log_entry(dict(entry))
        assert migrated["universe"] == "nse_full"

    def test_migrate_backtest_log_entry_defaults_universe(self):
        from screener import webapp
        legacy = {"ts": "x", "spec_hash": "abc"}
        migrated = webapp.migrate_backtest_log_entry(dict(legacy))
        assert migrated["universe"] == "nifty500"
        assert migrated["schema_version"] == \
            webapp.BACKTEST_LOG_SCHEMA_VERSION

    def test_new_screen_log_entries_are_stamped_current(
            self, tmp_path, monkeypatch):
        import json as _json
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE",
                            tmp_path / "screen_log.jsonl")
        webapp._log_run({"conditions": []}, "latest", {"matched": 0},
                        [], "hash1", "nifty500")
        entry = _json.loads(webapp.LOG_FILE.read_text().strip())
        assert entry["schema_version"] == webapp.SCREEN_LOG_SCHEMA_VERSION


class TestGoldenOffline:
    def test_all_expected_specs_valid(self):
        for case in load_fixtures():
            if case["expected"] == {"error": True}:
                continue
            spec = dsl.validate(case["expected"])
            assert dsl.describe(spec)
            canon(spec)  # canonicalisation must not raise


class TestSpecHash:
    """ROADMAP Item 5: screen diffing needs a hash stable under key
    order and default fill, but sensitive to as_of being excluded (the
    same criteria run on a later date is still 'the same screen')."""

    def test_stable_under_key_order(self):
        s1 = {"logic": "AND", "conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "range", "field": "rsi", "max": 30}]}
        s2 = {"conditions": [
            {"field": "rsi", "max": 30, "type": "range"},
            {"direction": "up", "type": "trend"}], "logic": "AND"}
        assert dsl.spec_hash(s1) == dsl.spec_hash(s2)

    def test_stable_under_default_fill(self):
        s1 = {"conditions": [{"type": "support_at_ma", "ma": "ema_50"}]}
        s2 = {"conditions": [{"type": "support_at_ma", "ma": "ema_50",
                              "tolerance_pct": 1.5, "lookback": 3}]}
        assert dsl.spec_hash(s1) == dsl.spec_hash(s2)

    def test_ignores_as_of(self):
        s1 = {"conditions": [{"type": "trend", "direction": "up"}],
             "as_of": "latest"}
        s2 = {"conditions": [{"type": "trend", "direction": "up"}],
             "as_of": "2026-01-01"}
        assert dsl.spec_hash(s1) == dsl.spec_hash(s2)

    def test_differs_for_different_specs(self):
        s1 = {"conditions": [{"type": "trend", "direction": "up"}]}
        s2 = {"conditions": [{"type": "trend", "direction": "down"}]}
        assert dsl.spec_hash(s1) != dsl.spec_hash(s2)


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
        assert j["stats"]["universe"] == 11
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


class TestDataQualityFlags:
    """ROADMAP Item 6: per-symbol flags surfaced on match cards, not
    buried in `verify`. JUMPY/THINHIST/STALECO in demo.py exist solely
    to exercise these."""
    client = TestClient(app)

    def _flags_for(self, symbol):
        spec = {"conditions": [{"type": "range", "field": "rsi", "min": 0}]}
        j = self.client.post("/api/screen", json={"spec": spec}).json()
        row = next((m for m in j["matches"] + j["near_misses"]
                   if m["symbol"] == symbol), None)
        assert row is not None, f"{symbol} not found in results"
        return {f["code"] for f in row["flags"]}

    def test_jump_flag(self):
        assert "jump" in self._flags_for("JUMPY")

    def test_thin_history_flag(self):
        assert "thin_history" in self._flags_for("THINHIST")

    def test_stale_flag(self):
        assert "stale" in self._flags_for("STALECO")

    def test_clean_symbol_has_no_flags(self):
        assert self._flags_for("STEADY") == set()


class TestStaleServerFix:
    """P0 (ROADMAP Item 6): a long-running server must notice
    `python -m screener.cli update` writing a fresh store overnight,
    not keep screening the panels it loaded at startup forever."""

    @staticmethod
    def _write_store(path, dated_closes):
        rows = [{"symbol": "ONLY", "date": pd.Timestamp(d),
                "open": c, "high": c, "low": c, "close": c,
                "volume": 1_000_000.0} for d, c in dated_closes]
        pd.DataFrame(rows).to_parquet(path, index=False)

    def test_state_rebuilds_on_store_mtime_change(self, tmp_path, monkeypatch):
        import time as _time
        from screener import config, data_ingest, universe, webapp

        store = tmp_path / "prices.parquet"
        bars = [(f"2024-01-{i + 1:02d}", 100.0) for i in range(9)] + \
               [("2024-02-01", 100.0)]
        self._write_store(store, bars)

        uni = pd.DataFrame({"symbol": ["ONLY"], "name": ["Only Co"],
                            "industry": ["Services"]})
        monkeypatch.setattr(config, "PRICE_STORE", store)
        monkeypatch.setattr(data_ingest, "assert_fresh",
                            lambda prices: prices["date"].max())
        monkeypatch.setattr(universe, "fetch_universe", lambda *a, **k: uni)
        monkeypatch.setattr(data_ingest, "load_benchmark",
                            lambda *a, **k: None)

        webapp._state.clear()
        try:
            st1 = webapp._load_state()
            assert st1["as_of"] == "2024-02-01"

            _time.sleep(1.05)  # filesystem mtime resolution safety margin
            self._write_store(store, bars + [("2024-04-15", 101.0)])

            st2 = webapp._load_state()
            assert st2["as_of"] == "2024-04-15"
            assert webapp._load_state() is st2  # unchanged mtime -> no rebuild
        finally:
            webapp._state.clear()  # restore demo mode for the rest of the suite

    def test_force_demo_env_var_overrides_real_store(self, tmp_path,
                                                      monkeypatch):
        """ROADMAP Item 11: web/visual's screenshot baseline needs
        deterministic demo data even when a real store exists on disk
        (as it does on any dev machine that's run `backfill`)."""
        from screener import config, data_ingest, universe, webapp

        store = tmp_path / "prices.parquet"
        self._write_store(store, [("2024-01-01", 100.0)] * 15)
        uni = pd.DataFrame({"symbol": ["ONLY"], "name": ["Only Co"],
                            "industry": ["Services"]})
        monkeypatch.setattr(config, "PRICE_STORE", store)
        monkeypatch.setattr(data_ingest, "assert_fresh",
                            lambda prices: prices["date"].max())
        monkeypatch.setattr(universe, "fetch_universe", lambda *a, **k: uni)
        monkeypatch.setattr(data_ingest, "load_benchmark",
                            lambda *a, **k: None)
        monkeypatch.setenv("SCREENER_FORCE_DEMO", "1")

        webapp._state.clear()
        try:
            st = webapp._load_state()
            assert st["mode"] == "demo"
        finally:
            webapp._state.clear()


class TestConfigOverrides:
    def test_config_hash_changes_with_override(self, monkeypatch):
        from screener import config
        h1 = config.config_hash()
        monkeypatch.setattr(config, "MIN_MEDIAN_TURNOVER_CR",
                            config.MIN_MEDIAN_TURNOVER_CR + 1)
        assert config.config_hash() != h1

    def test_load_local_overrides_applies_known_key(self, tmp_path, monkeypatch):
        from screener import config
        toml_path = tmp_path / "config_local.toml"
        toml_path.write_text("MIN_MEDIAN_TURNOVER_CR = 2.5\n")
        monkeypatch.setattr(config, "LOCAL_CONFIG_FILE", toml_path)
        applied = config._load_local_overrides()
        try:
            assert applied == {"MIN_MEDIAN_TURNOVER_CR": 2.5}
            assert config.MIN_MEDIAN_TURNOVER_CR == 2.5
        finally:
            # _load_local_overrides mutates config's globals() directly,
            # which monkeypatch can't auto-undo
            config.MIN_MEDIAN_TURNOVER_CR = 0.5

    def test_load_local_overrides_ignores_unknown_key(self, tmp_path,
                                                       monkeypatch):
        from screener import config
        toml_path = tmp_path / "config_local.toml"
        toml_path.write_text("NOT_A_REAL_SETTING = 42\n")
        monkeypatch.setattr(config, "LOCAL_CONFIG_FILE", toml_path)
        assert config._load_local_overrides() == {}
        assert not hasattr(config, "NOT_A_REAL_SETTING")

    def test_no_file_means_no_overrides(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "LOCAL_CONFIG_FILE",
                            tmp_path / "does_not_exist.toml")
        assert config._load_local_overrides() == {}

    def test_sr_module_aliases_track_config(self):
        from screener import config, sr
        assert sr.PIVOT_K == config.PIVOT_K
        assert sr.SR_LOOKBACK == config.SR_LOOKBACK


class TestHealthEndpoint:
    client = TestClient(app)

    def test_health_reports_demo_mode(self):
        r = self.client.get("/api/health")
        assert r.status_code == 200
        j = r.json()
        assert j["mode"] == "demo"
        assert j["panel_count"] == 11
        assert j["store_mtime"] is None  # nothing on disk in demo mode
        assert j["log_writable"] is True
        assert isinstance(j["version"], str) and j["version"]
        assert "config_hash" in j


class TestUniverseEndpoints:
    """ROADMAP Item 15 Phase A: the webapp universe selector."""
    client = TestClient(app)

    def _reset(self):
        from screener import universes, webapp
        webapp._ACTIVE_UNIVERSE = universes.DEFAULT_UNIVERSE
        webapp._state.clear()

    def test_list_universes_includes_registered_ones(self):
        r = self.client.get("/api/universes")
        assert r.status_code == 200
        ids = {u["id"] for u in r.json()}
        assert {"nifty500", "nse_full"} <= ids
        assert sum(u["active"] for u in r.json()) == 1

    def test_unknown_universe_422s(self):
        r = self.client.post("/api/universe", json={"id": "bogus"})
        assert r.status_code == 422

    def test_switch_falls_back_to_demo_without_a_real_store(
            self, tmp_path, monkeypatch):
        """nse_full's real store exists on THIS dev machine (Item 15
        Phase A backfilled it), so this test must not depend on that —
        point DATA_DIR at an empty tmp dir so nse_full correctly falls
        back to demo mode here, the same way it would in CI."""
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        try:
            r = self.client.post("/api/universe", json={"id": "nse_full"})
            assert r.status_code == 200
            j = r.json()
            assert j["active"] == "nse_full" and j["mode"] == "demo"

            status = self.client.get("/api/status").json()
            assert status["universe_id"] == "nse_full"
            assert status["universe_name"] == "NSE Full (all EQ series)"

            listing = self.client.get("/api/universes").json()
            active = [u for u in listing if u["active"]]
            assert active == [{"id": "nse_full",
                              "name": "NSE Full (all EQ series)",
                              "active": True}]
        finally:
            self._reset()

    def test_backtest_survivorship_note_matches_active_universe(
            self, tmp_path, monkeypatch):
        """Regression: the backtest endpoint used to always print the
        nifty500-worded survivorship caveat regardless of which
        universe was actually active — caught via live testing."""
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        try:
            self.client.post("/api/universe", json={"id": "nse_full"})
            r = self.client.post("/api/backtest", json={
                "spec": {"conditions": [
                    {"type": "trend", "direction": "up"}]},
                "horizons": [5], "sensitivity": False})
            assert r.status_code == 200
            assert "NSE EQ-series" in r.json()["survivorship_note"]
        finally:
            self._reset()


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

    def test_screen_log_no_data_warns(self):
        assert verify.check_screen_log(None)[1] == verify.WARN
        assert verify.check_screen_log([])[1] == verify.WARN

    def test_screen_log_valid_jsonl_passes(self):
        import json
        lines = [json.dumps({"ts": "x", "as_of": "latest", "spec": {},
                             "stats": {}, "matched": []})
                for _ in range(3)]
        name, status, detail = verify.check_screen_log(lines)
        assert status == verify.PASS and "3 entries" in detail

    def test_screen_log_corrupt_line_fails(self):
        import json
        lines = [json.dumps({"ts": "x", "as_of": "latest", "spec": {},
                             "stats": {}, "matched": []}),
                 "{not valid json",
                 json.dumps({"ts": "x"})]  # missing required keys
        name, status, detail = verify.check_screen_log(lines)
        assert status == verify.FAIL
        assert "2/3" in detail

    def test_screen_log_includes_rotated_entries(self):
        import json
        active = [json.dumps({"ts": "x", "as_of": "latest", "spec": {},
                              "stats": {}, "matched": []})]
        rotated = [json.dumps({"ts": "y", "as_of": "latest", "spec": {},
                               "stats": {}, "matched": []})
                  for _ in range(2)]
        name, status, detail = verify.check_screen_log(active, rotated)
        assert status == verify.PASS
        assert "1 active" in detail and "2 rotated" in detail

    def test_screen_log_corrupt_rotated_line_fails(self):
        import json
        active = [json.dumps({"ts": "x", "as_of": "latest", "spec": {},
                              "stats": {}, "matched": []})]
        rotated = ["{not valid json"]
        name, status, detail = verify.check_screen_log(active, rotated)
        assert status == verify.FAIL


class TestScreenLogRotation:
    def test_rotate_moves_overflow_to_archive(self, tmp_path, monkeypatch):
        from screener import webapp
        log = tmp_path / "screen_log.jsonl"
        rotated = tmp_path / "screen_log.rotated.jsonl"
        n = webapp.MAX_LOG_LINES + 50
        log.write_text("\n".join(f'{{"n":{i}}}' for i in range(n)) + "\n")
        monkeypatch.setattr(webapp, "LOG_FILE", log)
        monkeypatch.setattr(webapp, "ROTATED_LOG_FILE", rotated)

        webapp._rotate_log_if_needed()

        active_lines = log.read_text().splitlines()
        rotated_lines = rotated.read_text().splitlines()
        assert len(active_lines) == webapp.MAX_LOG_LINES
        assert len(rotated_lines) == 50
        # oldest entries rotated out, newest kept in the active file
        assert rotated_lines[0] == '{"n":0}'
        assert active_lines[-1] == f'{{"n":{n - 1}}}'

    def test_no_rotation_below_threshold(self, tmp_path, monkeypatch):
        from screener import webapp
        log = tmp_path / "screen_log.jsonl"
        rotated = tmp_path / "screen_log.rotated.jsonl"
        log.write_text("\n".join(f'{{"n":{i}}}' for i in range(10)) + "\n")
        monkeypatch.setattr(webapp, "LOG_FILE", log)
        monkeypatch.setattr(webapp, "ROTATED_LOG_FILE", rotated)

        webapp._rotate_log_if_needed()

        assert len(log.read_text().splitlines()) == 10
        assert not rotated.exists()


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


class TestGap:
    def test_gap_up_detected(self):
        p = _mk_ohlcv(100 + np.zeros(300))
        prev_close = p["close"].iloc[-2]
        p.iloc[-1, p.columns.get_loc("open")] = prev_close * 1.05
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 3}]}))
        assert not evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "gap", "direction": "down", "min_gap_pct": 2.0,
             "lookback": 3}]}))

    def test_gap_outside_lookback_not_detected(self):
        p = _mk_ohlcv(100 + np.zeros(300))
        prev_close = p["close"].iloc[-10]
        p.iloc[-9, p.columns.get_loc("open")] = prev_close * 1.05
        assert not evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 3}]}))
        assert evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 10}]}))

    def test_gap_below_threshold_not_detected(self):
        p = _mk_ohlcv(100 + np.zeros(300))
        prev_close = p["close"].iloc[-2]
        p.iloc[-1, p.columns.get_loc("open")] = prev_close * 1.01
        assert not evaluate_symbol(p, dsl.validate({"conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 3}]}))

    def test_explainer(self):
        from screener import explain
        p = _mk_ohlcv(100 + np.zeros(300))
        prev_close = p["close"].iloc[-2]
        p.iloc[-1, p.columns.get_loc("open")] = prev_close * 1.05
        spec = dsl.validate({"conditions": [
            {"type": "gap", "direction": "up", "min_gap_pct": 2.0,
             "lookback": 3}]})
        ev = explain.explain_symbol(p, spec)
        assert ev[0]["passed"] and "gapped up" in ev[0]["evidence"]

    def test_dsl_validation(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "gap", "direction": "sideways"}]})


class TestPresets:
    def test_all_presets_validate_and_describe(self):
        from screener import presets
        assert len(presets.PRESETS) >= 26
        ids = [p["id"] for p in presets.PRESETS]
        assert len(ids) == len(set(ids))
        for p in presets.PRESETS:
            assert dsl.describe(dsl.validate(p["spec"]))
            assert p["description"] and p["group"]

    def test_evidence_schema(self):
        """ROADMAP Item 9: every preset carries a well-formed evidence
        object pointing back to LITERATURE.md — no preset silently
        skips the annotation pass."""
        from screener import presets
        for p in presets.PRESETS:
            ev = p.get("evidence")
            assert ev is not None, f"{p['id']} missing evidence"
            assert ev["basis"] in ("academic", "practitioner", "mixed")
            assert isinstance(ev["sources"], list)
            assert ev["basis"] != "academic" or ev["sources"], (
                f"{p['id']} claims academic basis with no sources")
            assert ev["finding"] and ev["caveat"]

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

    def test_universes_tag_computed_from_sector_usage(self):
        """ROADMAP Item 15 follow-up: a preset using sector/sector_rank
        is tagged nifty500-only (the only universe with sector data
        today); everything else is tagged for every registered
        universe. Computed from the spec, not hand-maintained."""
        from screener import evaluator, presets, universes as universes_mod
        sector_ids = {p["id"] for p in presets.PRESETS
                     if any(c.get("type") in evaluator.SECTOR_DEPENDENT_TYPES
                           for c in p["spec"]["conditions"])}
        assert sector_ids == {"sector_leader_pullback", "lagging_sector_bounce"}
        for p in presets.PRESETS:
            if p["id"] in sector_ids:
                assert p["universes"] == [universes_mod.DEFAULT_UNIVERSE]
            else:
                assert set(p["universes"]) == set(universes_mod.UNIVERSES)

    def test_presets_endpoint_includes_universes_field(self):
        client = TestClient(app)
        items = client.get("/api/presets").json()
        by_id = {i["id"]: i for i in items}
        assert by_id["sector_leader_pullback"]["universes"] == ["nifty500"]
        assert "nse_full" in by_id["support_50ema_uptrend"]["universes"]

    def test_pattern_explain(self):
        from screener import explain
        p = _candle_panel([(100, 106, 94, 103), (101, 104, 96, 99)])
        ev = explain.explain_symbol(p, dsl.validate({"conditions": [
            {"type": "candle", "pattern": "inside_bar"}]}))
        assert ev[0]["passed"] and "inside_bar on" in ev[0]["evidence"]


class TestAsOfAndSpark:
    client = TestClient(app)

    def test_historical_as_of_metrics(self):
        # metrics must come from the as-of row, not the latest bar
        from screener.webapp import _load_state
        st = _load_state()
        panel = st["panels"]["STEADY"]
        d = str(panel.index[-40].date())
        spec = {"logic": "AND", "as_of": d, "conditions": [
            {"type": "trend", "direction": "up"}]}
        j = self.client.post("/api/screen", json={"spec": spec}).json()
        assert j["as_of"] == d
        m = next(x for x in j["matches"] if x["symbol"] == "STEADY")
        expected = round(float(panel["close"].iloc[-40]), 2)
        assert m["metrics"]["close"] == expected
        assert m["spark"]["dates"][-1] == d

    def test_spark_contains_referenced_series_and_levels(self):
        j = self.client.post("/api/screen", json={"spec": {
            "logic": "AND", "conditions": [
                {"type": "support_at_ma", "ma": "ema_50",
                 "tolerance_pct": 1.5, "lookback": 3},
                {"type": "trend", "direction": "up"}]}}).json()
        sp = j["matches"][0]["spark"]
        assert "ema_50" in sp["series"]
        assert len(sp["close"]) == len(sp["dates"]) <= 60
        j2 = self.client.post("/api/screen", json={"spec": {
            "conditions": [{"type": "near_support",
                            "tolerance_pct": 2.5}]}}).json()
        assert any("support" in m["spark"]["levels"]
                   for m in j2["matches"])

    def test_screen_log_written(self, monkeypatch, tmp_path):
        from screener import webapp
        log = tmp_path / "screen_log.jsonl"
        monkeypatch.setattr(webapp, "LOG_FILE", log)
        before = log.read_text().count("\n") if log.exists() else 0
        self.client.post("/api/screen", json={"spec": {
            "conditions": [{"type": "trend", "direction": "up"}]}})
        after = log.read_text().count("\n")
        assert after == before + 1
        r = self.client.get("/api/log")
        assert r.status_code == 200 and r.json()[0]["matched"]


class TestChartEndpoint:
    """ROADMAP Item 5: full modal chart, lazily fetched per symbol."""
    client = TestClient(app)

    def test_returns_full_bars_with_ohlcv(self):
        from screener import webapp
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        r = self.client.post("/api/chart", json={"symbol": "STEADY",
                                                  "spec": spec})
        assert r.status_code == 200
        j = r.json()
        assert len(j["dates"]) == len(j["open"]) == len(j["close"]) \
            == len(j["volume"]) <= webapp.CHART_BARS
        assert len(j["dates"]) > len(j["dates"][:webapp.SPARK_BARS])

    def test_unknown_symbol_404s(self):
        r = self.client.post("/api/chart", json={
            "symbol": "NOPE", "spec": {"conditions": [
                {"type": "trend", "direction": "up"}]}})
        assert r.status_code == 404

    def test_bad_spec_rejected(self):
        r = self.client.post("/api/chart", json={
            "symbol": "STEADY", "spec": {"conditions": [
                {"type": "compare", "left": "pe_ratio", "op": ">",
                 "right": 5}]}})
        assert r.status_code == 422

    def test_contains_referenced_series_and_levels(self):
        r = self.client.post("/api/chart", json={"symbol": "PULLBK",
                                                  "spec": {
            "logic": "AND", "conditions": [
                {"type": "support_at_ma", "ma": "ema_50",
                 "tolerance_pct": 1.5, "lookback": 3},
                {"type": "trend", "direction": "up"}]}})
        j = r.json()
        assert "ema_50" in j["series"]


class TestAllocateEndpoint:
    """ROADMAP Item 10: portfolio allocation engine, API contract."""
    client = TestClient(app)

    def test_basic_allocation(self):
        r = self.client.post("/api/allocate", json={
            "symbols": ["STEADY", "PULLBK", "BRKDWN"],
            "capital": 100_000, "method": "risk", "risk_pct": 1.0})
        assert r.status_code == 200
        j = r.json()
        assert "positions" in j and "summary" in j and "baseline" in j
        assert "disclaimer" in j and "not investment advice" in j["disclaimer"]

    def test_equal_method_has_no_baseline_key(self):
        r = self.client.post("/api/allocate", json={
            "symbols": ["STEADY", "PULLBK"], "capital": 50_000,
            "method": "equal"})
        assert r.status_code == 200
        assert "baseline" not in r.json()

    def test_invalid_method_422s(self):
        r = self.client.post("/api/allocate", json={
            "symbols": ["STEADY"], "capital": 50_000, "method": "mvo"})
        assert r.status_code == 422

    def test_nonpositive_capital_422s(self):
        r = self.client.post("/api/allocate", json={
            "symbols": ["STEADY"], "capital": 0, "method": "risk"})
        assert r.status_code == 422

    def test_allocation_logged(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "ALLOCATION_LOG_FILE",
                            tmp_path / "allocation_log.jsonl")
        self.client.post("/api/allocate", json={
            "symbols": ["STEADY"], "capital": 20_000, "method": "risk",
            "spec": {"conditions": [{"type": "trend", "direction": "up"}]}})
        assert webapp.ALLOCATION_LOG_FILE.exists()
        import json as _json
        entry = _json.loads(
            webapp.ALLOCATION_LOG_FILE.read_text().strip().splitlines()[-1])
        assert entry["spec_hash"] and entry["capital"] == 20_000
        assert "positions" in entry and "summary" in entry


class TestBacktestEndpoint:
    """ROADMAP Item 14: screen backtester, API contract."""
    client = TestClient(app)

    def test_basic_backtest_e2e(self):
        r = self.client.post("/api/backtest", json={
            "spec": {"conditions": [{"type": "trend", "direction": "up"}]},
            "horizons": [5, 20], "sensitivity": False})
        assert r.status_code == 200
        j = r.json()
        assert set(j["horizons"].keys()) == {"5", "20"}
        assert "survivorship_note" in j and "Survivorship" in j["survivorship_note"]
        assert "events" in j and "n_events_total" in j and "elapsed_sec" in j

    def test_invalid_spec_422s(self):
        r = self.client.post("/api/backtest", json={
            "spec": {"conditions": []}, "sensitivity": False})
        assert r.status_code == 422

    def test_empty_horizons_422s(self):
        r = self.client.post("/api/backtest", json={
            "spec": {"conditions": [{"type": "trend", "direction": "up"}]},
            "horizons": [], "sensitivity": False})
        assert r.status_code == 422

    def test_backtest_logged(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "BACKTEST_LOG_FILE",
                            tmp_path / "backtest_log.jsonl")
        self.client.post("/api/backtest", json={
            "spec": {"conditions": [{"type": "trend", "direction": "up"}]},
            "horizons": [5], "sensitivity": False,
            "hypothesis": "expect positive drift continuation"})
        assert webapp.BACKTEST_LOG_FILE.exists()
        import json as _json
        entry = _json.loads(
            webapp.BACKTEST_LOG_FILE.read_text().strip().splitlines()[-1])
        assert entry["spec_hash"] and entry["hypothesis"] == \
            "expect positive drift continuation"
        assert "horizons" in entry and "n_events_total" in entry
        assert entry["universe"] == "nifty500"


class TestCohortEndpoints:
    """ROADMAP Item 16: cohort tracker, API contract. Demo panels end
    "today", so a freshly-created cohort correctly stays pending (no
    bar exists after today yet) — full lifecycle progression is
    covered at the unit level in tests/test_cohorts.py; this class
    tests the HTTP contract these endpoints must honour."""
    client = TestClient(app)
    SPEC = {"conditions": [{"type": "trend", "direction": "up"}]}

    def test_create_from_symbols_starts_pending(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY", "PULLBK"]})
        assert r.status_code == 200
        j = r.json()
        assert j["status"] == "pending" and j["entry_date"] is None
        assert j["weights"]["method"] == "equal"
        assert set(j["weights"]["by_symbol"]) == {"STEADY", "PULLBK"}

    def test_create_from_positions_uses_allocation_weights(self, tmp_path,
                                                            monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "method": "risk",
            "positions": [{"symbol": "STEADY", "value": 30000},
                         {"symbol": "PULLBK", "value": 10000}]})
        assert r.status_code == 200
        j = r.json()
        assert j["weights"]["method"] == "risk"
        assert j["weights"]["by_symbol"]["STEADY"] == pytest.approx(0.75)

    def test_create_requires_exactly_one_of_symbols_or_positions(self):
        r = self.client.post("/api/cohorts", json={"spec": self.SPEC})
        assert r.status_code == 422
        r2 = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"],
            "positions": [{"symbol": "STEADY", "value": 1}]})
        assert r2.status_code == 422

    def test_create_invalid_spec_422s(self):
        r = self.client.post("/api/cohorts", json={
            "spec": {"conditions": []}, "symbols": ["STEADY"]})
        assert r.status_code == 422

    def test_list_and_get_roundtrip(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]}).json()

        lst = self.client.get("/api/cohorts").json()
        assert any(c["cohort_id"] == created["cohort_id"] for c in lst)
        assert all("current" in c for c in lst)  # always attached, may be None

        got = self.client.get(f"/api/cohorts/{created['cohort_id']}")
        assert got.status_code == 200
        assert got.json()["cohort_id"] == created["cohort_id"]

    def test_get_unknown_cohort_404s(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.get("/api/cohorts/doesnotexist")
        assert r.status_code == 404

    def test_delete_removes_cohort(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]}).json()
        r = self.client.delete(f"/api/cohorts/{created['cohort_id']}")
        assert r.status_code == 200
        j = r.json()
        assert j["removed"] is True and j["tombstoned"] is False
        assert self.client.get(
            f"/api/cohorts/{created['cohort_id']}").status_code == 404

    def test_delete_unknown_cohort_returns_removed_false(
            self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.delete("/api/cohorts/doesnotexist")
        assert r.status_code == 200 and r.json()["removed"] is False

    def test_list_filters_by_spec_hash(self, tmp_path, monkeypatch):
        from screener import config, dsl
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]})
        other_spec = {"conditions": [{"type": "range", "field": "rsi",
                                     "min": 0, "max": 100}]}
        self.client.post("/api/cohorts", json={
            "spec": other_spec, "symbols": ["PULLBK"]})

        target_hash = dsl.spec_hash(dsl.validate(self.SPEC))
        lst = self.client.get(
            f"/api/cohorts?spec_hash={target_hash}").json()
        assert len(lst) == 1
        assert lst[0]["spec_hash"] == target_hash

    def test_scorecard_shape_and_suppression(self, tmp_path, monkeypatch):
        from screener import config, dsl
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]})
        target_hash = dsl.spec_hash(dsl.validate(self.SPEC))
        r = self.client.get(f"/api/scorecard/{target_hash}")
        assert r.status_code == 200
        j = r.json()
        assert j["spec_hash"] == target_hash
        assert j["n_cohorts_total"] == 1
        for h in ("5", "20", "60"):
            assert j["horizons"][h]["insufficient"] is True
        assert "survivorship_free_note" in j and \
            "survivorship-free" in j["survivorship_free_note"].lower()
        assert "replay" in j and j["replay"]["n_cohorts"] == 0


class TestCohortReplayAndPerformanceEndpoints:
    """ROADMAP Item 17: replay-mode creation and the performance panel.
    Demo panels run 2024-03-25 -> 2026-07-10 (600 bars), so an as_of
    ~100 bars back leaves plenty of later data for a replay window."""
    client = TestClient(app)
    SPEC = {"conditions": [{"type": "trend", "direction": "up"}]}

    def test_create_with_as_of_is_replay_mode(self, tmp_path, monkeypatch):
        from screener import config, demo
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        as_of = str(panels["STEADY"].index[-100].date())
        r = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY", "PULLBK"],
            "as_of": as_of})
        assert r.status_code == 200
        j = r.json()
        assert j["mode"] == "replay"
        assert j["as_of"] is not None
        # creation itself doesn't refresh (lazy, on-read, by design) —
        # the next GET resolves entry immediately since as_of is already
        # historical, unlike a forward cohort which waits on real time.
        got = self.client.get(f"/api/cohorts/{j['cohort_id']}").json()
        assert got["status"] != "pending"

    def test_create_as_of_with_no_later_bar_422s(self, tmp_path,
                                                 monkeypatch):
        from screener import config, demo
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        latest = str(panels["STEADY"].index[-1].date())
        r = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"], "as_of": latest})
        assert r.status_code == 422

    def test_replay_cohort_carries_survivorship_note_on_read(
            self, tmp_path, monkeypatch):
        from screener import config, cohorts as cohorts_mod, demo
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        as_of = str(panels["STEADY"].index[-100].date())
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"],
            "as_of": as_of}).json()
        got = self.client.get(f"/api/cohorts/{created['cohort_id']}").json()
        assert got["survivorship_note"] == \
            cohorts_mod.REPLAY_SURVIVORSHIP_NOTE
        # a forward cohort never gets this field attached
        fwd = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["PULLBK"]}).json()
        got_fwd = self.client.get(f"/api/cohorts/{fwd['cohort_id']}").json()
        assert "survivorship_note" not in got_fwd

    def test_no_as_of_still_forward_mode(self, tmp_path, monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]})
        assert r.json()["mode"] == "forward"

    def test_performance_endpoint_shape_for_replay_cohort(
            self, tmp_path, monkeypatch):
        from screener import config, demo
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        as_of = str(panels["STEADY"].index[-100].date())
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY", "PULLBK"],
            "as_of": as_of}).json()
        r = self.client.get(
            f"/api/cohorts/{created['cohort_id']}/performance")
        assert r.status_code == 200
        j = r.json()
        for key in ("gross", "net", "excess_gross_baseline", "sharpe",
                   "max_drawdown", "contributors", "per_symbol",
                   "equity_curve"):
            assert key in j
        assert j["equity_curve"]["dates"][0] == j["entry_date"]

    def test_performance_endpoint_404_unknown_cohort(self, tmp_path,
                                                      monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        r = self.client.get("/api/cohorts/doesnotexist/performance")
        assert r.status_code == 404

    def test_performance_endpoint_422_while_pending(self, tmp_path,
                                                     monkeypatch):
        from screener import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        # demo panels end "today" — a freshly-created forward cohort has
        # no bar after today yet, so it stays pending with no window.
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"]}).json()
        r = self.client.get(
            f"/api/cohorts/{created['cohort_id']}/performance")
        assert r.status_code == 422

    def test_performance_endpoint_end_param(self, tmp_path, monkeypatch):
        from screener import config, demo
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        as_of = str(panels["STEADY"].index[-100].date())
        created = self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"],
            "as_of": as_of}).json()
        mid_end = str(panels["STEADY"].index[-50].date())
        r = self.client.get(
            f"/api/cohorts/{created['cohort_id']}/performance",
            params={"end": mid_end})
        assert r.status_code == 200
        assert r.json()["end_date"] == mid_end

    def test_scorecard_replay_block_walled_off(self, tmp_path,
                                               monkeypatch):
        from screener import config, demo, dsl
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        panels, *_ = demo.build_demo()
        as_of = str(panels["STEADY"].index[-100].date())
        self.client.post("/api/cohorts", json={
            "spec": self.SPEC, "symbols": ["STEADY"], "as_of": as_of})
        target_hash = dsl.spec_hash(dsl.validate(self.SPEC))
        r = self.client.get(f"/api/scorecard/{target_hash}")
        j = r.json()
        assert j["n_cohorts_total"] == 0  # the only cohort is replay-mode
        assert j["replay"]["n_cohorts"] == 1
        assert "NOT part of the OOS scorecard" in j["replay"]["label"]


class TestSectorDataGapWarningEndpoints:
    """Live-endpoint version of TestSectorDataGapWarning: a universe
    with no industry data (like nse_full) must surface the warning in
    the actual /api/screen and /api/backtest responses, not just at
    the helper-function level."""
    client = TestClient(app)

    def _patch_no_industry_demo(self, monkeypatch):
        from screener import demo, webapp
        panels, uni, bench = demo.build_demo()
        uni = uni.copy()
        uni["industry"] = None
        monkeypatch.setattr(demo, "build_demo",
                            lambda: (panels, uni, bench))
        webapp._state.clear()

    def test_screen_warns_on_sector_condition(self, monkeypatch):
        self._patch_no_industry_demo(monkeypatch)
        try:
            r = self.client.post("/api/screen", json={"spec": {
                "conditions": [{"type": "sector_rank", "top": 3}]}})
            assert r.status_code == 200
            assert any("sector" in w.lower() for w in r.json()["warnings"])
        finally:
            from screener import webapp
            webapp._state.clear()

    def test_screen_no_warning_for_non_sector_spec(self, monkeypatch):
        self._patch_no_industry_demo(monkeypatch)
        try:
            r = self.client.post("/api/screen", json={"spec": {
                "conditions": [{"type": "trend", "direction": "up"}]}})
            assert r.status_code == 200
            assert r.json()["warnings"] == []
        finally:
            from screener import webapp
            webapp._state.clear()

    def test_backtest_warns_on_sector_condition(self, monkeypatch):
        self._patch_no_industry_demo(monkeypatch)
        try:
            r = self.client.post("/api/backtest", json={
                "spec": {"conditions": [
                    {"type": "sector_rank", "top": 3}]},
                "horizons": [5], "sensitivity": False})
            assert r.status_code == 200
            assert any("sector" in w.lower() for w in r.json()["warnings"])
        finally:
            from screener import webapp
            webapp._state.clear()


class TestScreenBatch:
    """ROADMAP Item 5: the morning-view multi-screen dashboard."""
    client = TestClient(app)

    def test_batch_runs_multiple_presets(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        r = self.client.post("/api/screen_batch", json={"preset_ids": [
            "support_50ema_uptrend", "golden_cross"]})
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert {row["preset_id"] for row in rows} == \
            {"support_50ema_uptrend", "golden_cross"}
        for row in rows:
            assert "matched" in row and "top3" in row and "error" not in row

    def test_batch_includes_user_preset(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        monkeypatch.setattr(webapp, "USER_PRESETS_FILE",
                            tmp_path / "user_presets.json")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        added = self.client.post("/api/user_presets",
                                 json={"name": "Mine", "spec": spec}).json()
        r = self.client.post("/api/screen_batch",
                             json={"preset_ids": [f"user:{added['id']}"]})
        rows = r.json()["rows"]
        assert rows[0]["name"] == "Mine" and "error" not in rows[0]

    def test_batch_unknown_preset_reports_error_not_crash(self, tmp_path,
                                                          monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        r = self.client.post("/api/screen_batch",
                             json={"preset_ids": ["does_not_exist"]})
        assert r.status_code == 200
        assert "error" in r.json()["rows"][0]


class TestUserPresets:
    """ROADMAP Item 5: saved custom screens — validated identically to
    built-in presets, rejected on save rather than discovered on run."""
    client = TestClient(app)

    def test_add_list_update_remove(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "USER_PRESETS_FILE",
                            tmp_path / "user_presets.json")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        r = self.client.post("/api/user_presets",
                             json={"name": "My uptrend", "notes": "test",
                                   "spec": spec})
        assert r.status_code == 200
        entry = r.json()
        assert entry["name"] == "My uptrend" and entry["english"]

        items = self.client.get("/api/user_presets").json()
        assert len(items) == 1 and items[0]["id"] == entry["id"]

        ru = self.client.put(f"/api/user_presets/{entry['id']}",
                             json={"name": "Renamed"})
        assert ru.json()["name"] == "Renamed"

        rd = self.client.delete(f"/api/user_presets/{entry['id']}")
        assert rd.json()["removed"] is True
        assert self.client.get("/api/user_presets").json() == []

    def test_invalid_spec_rejected_on_save(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "USER_PRESETS_FILE",
                            tmp_path / "user_presets.json")
        r = self.client.post("/api/user_presets", json={
            "name": "Bad", "spec": {"conditions": [
                {"type": "compare", "left": "pe_ratio", "op": ">",
                 "right": 5}]}})
        assert r.status_code == 422
        assert self.client.get("/api/user_presets").json() == []

    def test_update_unknown_id_404s(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "USER_PRESETS_FILE",
                            tmp_path / "user_presets.json")
        r = self.client.put("/api/user_presets/doesnotexist",
                            json={"name": "x"})
        assert r.status_code == 404


class TestWatchlist:
    """ROADMAP Item 5: star a match, track signal decay against
    *today's* data, not a static bookmark."""
    client = TestClient(app)

    def test_add_and_list_signal_still_holding(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "WATCHLIST_FILE",
                            tmp_path / "watchlist.jsonl")
        from screener.webapp import _load_state
        panel = _load_state()["panels"]["STEADY"]
        tag_date = str(panel.index[-40].date())
        spec = {"conditions": [{"type": "trend", "direction": "up"}],
               "as_of": tag_date}
        r = self.client.post("/api/watchlist",
                             json={"symbol": "STEADY", "spec": spec})
        assert r.status_code == 200
        assert r.json()["tagged_date"] == tag_date

        j = self.client.get("/api/watchlist").json()
        row = next(x for x in j if x["symbol"] == "STEADY")
        assert row["still_holds"] is True   # STEADY never stops trending up
        assert row["move_pct"] > 0          # and keeps rising since the tag

    def test_signal_decay_detected(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "WATCHLIST_FILE",
                            tmp_path / "watchlist.jsonl")
        from screener.webapp import _load_state
        panel = _load_state()["panels"]["BRKDWN"]
        # tag well before the engineered breakdown (last 8 bars), when
        # the uptrend condition genuinely held
        tag_date = str(panel.index[-20].date())
        spec = {"conditions": [{"type": "trend", "direction": "up"}],
               "as_of": tag_date}
        self.client.post("/api/watchlist",
                         json={"symbol": "BRKDWN", "spec": spec})
        j = self.client.get("/api/watchlist").json()
        row = next(x for x in j if x["symbol"] == "BRKDWN")
        assert row["still_holds"] is False  # the signal has decayed

    def test_remove(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "WATCHLIST_FILE",
                            tmp_path / "watchlist.jsonl")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        r = self.client.post("/api/watchlist",
                             json={"symbol": "STEADY", "spec": spec})
        item_id = r.json()["id"]
        assert len(self.client.get("/api/watchlist").json()) == 1
        rd = self.client.delete(f"/api/watchlist/{item_id}")
        assert rd.json()["removed"] is True
        assert len(self.client.get("/api/watchlist").json()) == 0

    def test_unknown_symbol_404s(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "WATCHLIST_FILE",
                            tmp_path / "watchlist.jsonl")
        r = self.client.post("/api/watchlist", json={"symbol": "NOPE",
            "spec": {"conditions": [{"type": "trend", "direction": "up"}]}})
        assert r.status_code == 404


class TestScreenDiff:
    """ROADMAP Item 5: "what changed since last run"."""
    client = TestClient(app)

    def test_first_run_has_no_diff(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        j = self.client.post("/api/screen", json={"spec": spec}).json()
        assert j["diff"] is None

    def test_second_run_reports_diff_present(self, tmp_path, monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        self.client.post("/api/screen", json={"spec": spec})
        j2 = self.client.post("/api/screen", json={"spec": spec}).json()
        # nothing changed in the demo data between the two calls
        assert j2["diff"] is not None
        assert j2["diff"]["new"] == []
        assert j2["diff"]["dropped"] == []

    def test_recognised_as_same_screen_regardless_of_as_of(self, tmp_path,
                                                           monkeypatch):
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        spec1 = {"conditions": [{"type": "trend", "direction": "up"}]}
        spec2 = {"conditions": [{"type": "trend", "direction": "up"}],
                 "as_of": "latest"}
        self.client.post("/api/screen", json={"spec": spec1})
        j2 = self.client.post("/api/screen", json={"spec": spec2}).json()
        assert j2["diff"] is not None

    def test_dropped_symbol_gets_failing_reason(self, tmp_path, monkeypatch):
        import json
        from screener import webapp
        monkeypatch.setattr(webapp, "LOG_FILE", tmp_path / "screen_log.jsonl")
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        spec_h = dsl.spec_hash(spec)
        # BRKDWN is engineered to break its uptrend at the latest bar —
        # a fabricated prior run claiming it matched exercises the
        # "now fails" path without depending on real data changing.
        webapp.LOG_FILE.write_text(json.dumps({
            "ts": "2020-01-01T00:00:00", "as_of": "latest", "spec": spec,
            "stats": {}, "spec_hash": spec_h, "matched": ["BRKDWN"],
        }) + "\n")
        j = self.client.post("/api/screen", json={"spec": spec}).json()
        dropped = {d["symbol"]: d["reason"] for d in j["diff"]["dropped"]}
        assert "BRKDWN" in dropped
        assert "now fails" in dropped["BRKDWN"]


# ---------------------------------------------------------------- sector /
# cross-sectional relative strength (ROADMAP Item 1)
from screener import cross_section  # noqa: E402


def _sector_universe():
    """3 sectors x 3 symbols with engineered momentum dispersion, plus one
    thin-history symbol. Sector A: strong recent momentum (best). Sector B:
    flat, middling. Sector C: sharp decline (worst)."""
    n = 300
    panels = {
        "A1": _mk_ohlcv(100 * np.cumprod(1 + np.full(n, 0.0040))),
        "A2": _mk_ohlcv(100 * np.cumprod(1 + np.full(n, 0.0035))),
        "A3": _mk_ohlcv(100 * np.cumprod(1 + np.full(n, 0.0038))),
        "B1": _mk_ohlcv(100 * np.cumprod(1 + np.full(n, 0.0005))),
        "B2": _mk_ohlcv(100 * np.cumprod(1 + np.full(n, 0.0004))),
        "THIN": _mk_ohlcv(100 * np.cumprod(1 + np.full(30, 0.01))),
        "C1": _mk_ohlcv(100 * np.cumprod(1 - np.full(n, 0.0030))),
        "C2": _mk_ohlcv(100 * np.cumprod(1 - np.full(n, 0.0028))),
        "C3": _mk_ohlcv(100 * np.cumprod(1 - np.full(n, 0.0032))),
    }
    uni = pd.DataFrame({
        "symbol": list(panels), "name": list(panels),
        "industry": ["Sector A"] * 3 + ["Sector B"] * 2 + ["Sector B"]
                   + ["Sector C"] * 3,
    })
    return panels, uni


class TestSectorDataGapWarning:
    """ROADMAP Item 15 sequencing follow-up: nse_full has no sector/
    industry data, so sector conditions must warn loudly rather than
    silently return zero matches."""

    def _uni(self, industries):
        return pd.DataFrame({"symbol": [f"S{i}" for i in range(len(industries))],
                            "name": [f"S{i}" for i in range(len(industries))],
                            "industry": industries})

    def test_none_when_spec_has_no_sector_conditions(self):
        spec = {"conditions": [{"type": "trend", "direction": "up"}]}
        uni = self._uni([None, None])
        assert sector_data_gap_warning(spec, uni) is None

    def test_none_when_universe_has_some_industry_data(self):
        spec = {"conditions": [{"type": "sector_rank", "top": 3}]}
        uni = self._uni(["IT", "Metals"])
        assert sector_data_gap_warning(spec, uni) is None

    def test_warns_when_sector_rank_used_and_no_industry_data(self):
        spec = {"conditions": [{"type": "sector_rank", "top": 3}]}
        uni = self._uni([None, None, float("nan")])
        w = sector_data_gap_warning(spec, uni)
        assert w is not None and "sector" in w.lower()

    def test_warns_when_plain_sector_condition_used(self):
        spec = {"conditions": [{"type": "sector", "in": ["IT"]}]}
        uni = self._uni([None, None])
        assert sector_data_gap_warning(spec, uni) is not None

    def test_none_when_universe_is_none(self):
        spec = {"conditions": [{"type": "sector_rank", "top": 3}]}
        assert sector_data_gap_warning(spec, None) is None

    def test_none_when_universe_missing_industry_column(self):
        spec = {"conditions": [{"type": "sector_rank", "top": 3}]}
        uni = pd.DataFrame({"symbol": ["S0"], "name": ["S0"]})
        assert sector_data_gap_warning(spec, uni) is None


class TestCrossSection:
    def test_deterministic_pure_function(self):
        panels, uni = _sector_universe()
        df1 = cross_section.build_cross_section(dict(panels), uni,
                                                 "latest", 63)
        df2 = cross_section.build_cross_section(dict(panels), uni,
                                                 "latest", 63)
        pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())

    def test_thin_history_excluded_not_defaulted(self):
        panels, uni = _sector_universe()
        df = cross_section.build_cross_section(panels, uni, "latest", 63)
        assert pd.isna(df.loc["THIN", "ret_pct"])

    def test_cache_key_not_fooled_by_id_reuse(self):
        """The cache is keyed by id(panels), which CPython can and does
        reuse once an earlier `panels` dict is garbage-collected — a
        real bug this session's Item 14 backtest tests exposed by
        churning through many short-lived synthetic panels dicts and
        intermittently corrupting unrelated preset tests elsewhere in
        the same pytest run. Simulate the exact collision: plant a
        stale entry under this dict's own id() but a different symbol
        set, and confirm the real computation still wins."""
        panels, uni = _sector_universe()
        stale_key = (id(panels), frozenset({"NOT", "THE", "SAME", "SYMBOLS"}),
                    "latest", 63)
        bogus = pd.DataFrame({"ret_pct": [999.0]}, index=["A1"])
        cross_section._CACHE[stale_key] = bogus
        try:
            df = cross_section.build_cross_section(panels, uni, "latest", 63)
            assert set(df.index) == set(panels)
            assert df.loc["A1", "ret_pct"] != 999.0
        finally:
            cross_section._CACHE.pop(stale_key, None)
        assert pd.isna(df.loc["THIN", "rs_percentile"])

    def test_sector_ranking_direction(self):
        panels, uni = _sector_universe()
        df = cross_section.build_cross_section(panels, uni, "latest", 63)
        assert df.loc["A1", "sector_rank"] == 1
        assert df.loc["B1", "sector_rank"] == 2
        assert df.loc["C1", "sector_rank"] == 3

    def test_rs_percentile_ordering(self):
        panels, uni = _sector_universe()
        df = cross_section.build_cross_section(panels, uni, "latest", 63)
        assert df.loc["A1", "rs_percentile"] > df.loc["C1", "rs_percentile"]

    def test_no_lookahead(self):
        """Ranks at an early as_of must reflect only data up to that row —
        same spirit as the pivot look-ahead test. Sector A crashes then
        rallies; Sector C does the mirror, so which sector 'wins' flips
        between the early date and latest."""
        n, phase1 = 300, 200
        a = np.concatenate([
            100 * np.cumprod(1 - np.full(phase1, 0.002))])
        a = np.concatenate([a, a[-1] * np.cumprod(
            1 + np.full(n - phase1, 0.006))])
        c = np.concatenate([
            100 * np.cumprod(1 + np.full(phase1, 0.002))])
        c = np.concatenate([c, c[-1] * np.cumprod(
            1 - np.full(n - phase1, 0.006))])
        panels = {"A1": _mk_ohlcv(a), "C1": _mk_ohlcv(c)}
        uni = pd.DataFrame({"symbol": ["A1", "C1"], "name": ["a", "c"],
                           "industry": ["Sector A", "Sector C"]})
        early = str(panels["A1"].index[phase1].date())
        df_early = cross_section.build_cross_section(panels, uni, early, 63)
        df_latest = cross_section.build_cross_section(panels, uni,
                                                       "latest", 63)
        assert (df_early.loc["C1", "rs_percentile"]
               > df_early.loc["A1", "rs_percentile"])
        assert (df_latest.loc["A1", "rs_percentile"]
               > df_latest.loc["C1", "rs_percentile"])


class TestSectorConditions:
    def test_sector_condition_matches(self):
        panels, uni = _sector_universe()
        sbs = uni.set_index("symbol")["industry"]
        spec = {"conditions": [{"type": "sector", "in": ["Sector A"]}]}
        assert evaluate_symbol(panels["A1"], spec, symbol="A1",
                               sector_by_symbol=sbs)
        assert not evaluate_symbol(panels["C1"], spec, symbol="C1",
                                   sector_by_symbol=sbs)

    def test_sector_condition_requires_context(self):
        panels, _uni = _sector_universe()
        spec = {"conditions": [{"type": "sector", "in": ["Sector A"]}]}
        with pytest.raises(RuntimeError):
            evaluate_symbol(panels["A1"], spec)

    def test_rs_percentile_threshold(self):
        panels, uni = _sector_universe()
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        spec = {"conditions": [
            {"type": "rs_percentile", "window": 63, "op": ">=",
             "value": 80}]}
        assert evaluate_symbol(panels["A1"], spec, symbol="A1",
                               cross_section=cs)
        assert not evaluate_symbol(panels["C1"], spec, symbol="C1",
                                   cross_section=cs)

    def test_sector_rank_top_and_bottom(self):
        panels, uni = _sector_universe()
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        top = {"conditions": [
            {"type": "sector_rank", "window": 63, "top": 1}]}
        bottom = {"conditions": [
            {"type": "sector_rank", "window": 63, "bottom": 1}]}
        assert evaluate_symbol(panels["A1"], top, symbol="A1",
                               cross_section=cs)
        assert not evaluate_symbol(panels["C1"], top, symbol="C1",
                                   cross_section=cs)
        assert evaluate_symbol(panels["C1"], bottom, symbol="C1",
                               cross_section=cs)
        assert not evaluate_symbol(panels["A1"], bottom, symbol="A1",
                                   cross_section=cs)

    def test_via_run_screen_end_to_end(self):
        panels, uni = _sector_universe()
        spec = dsl.validate({"conditions": [
            {"type": "sector_rank", "window": 63, "top": 1}]})
        res = run_screen(panels, spec, universe=uni)
        assert set(res["symbol"]) == {"A1", "A2", "A3"}

    def test_explainers(self):
        # raw dict, not dsl.validate()-ed: the synthetic universe uses
        # fictional sector labels ("Sector A"), which real validation
        # correctly rejects (KNOWN_SECTORS is the real Nifty 500 list).
        from screener import explain
        panels, uni = _sector_universe()
        sbs = uni.set_index("symbol")["industry"]
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        spec = {"conditions": [
            {"type": "sector", "in": ["Sector A"]},
            {"type": "rs_percentile", "window": 63, "op": ">=",
             "value": 50},
            {"type": "sector_rank", "window": 63, "top": 1}]}
        ev = explain.explain_symbol(panels["A1"], spec, symbol="A1",
                                    sector_by_symbol=sbs, cross_section=cs)
        assert all(e["passed"] for e in ev)
        assert "Sector A" in ev[0]["evidence"]
        assert "percentile" in ev[1]["evidence"]
        assert "ranked" in ev[2]["evidence"]


class TestCrossSectionCache:
    def test_cache_bounded(self):
        from screener import cross_section as cs
        from screener.webapp import _load_state
        st = _load_state()
        cs._CACHE.clear()
        panel = next(iter(st["panels"].values()))
        dates = [str(d.date()) for d in panel.index[-(cs._CACHE_MAX + 10):]]
        for d in dates:
            cs.build_cross_section(st["panels"], st["universe"], d, 63)
        assert len(cs._CACHE) <= cs._CACHE_MAX
        # most recent as_of must still be cached (FIFO evicts oldest);
        # key is (id(panels), frozenset(panels), as_of, window)
        assert any(k[2] == dates[-1] for k in cs._CACHE)


# ============================================================ market breadth
# Regime context computed from the universe itself (post-v0.15 Session 2
# prep) — LITERATURE.md §9, ROADMAP §C.
def _breadth_universe():
    """6 symbols, deliberately split so pct_above_200dma and
    pct_at_20d_high are both hand-computable exactly: 4 monotonically
    RISING for all 300 bars (each closes far above its own SMA200, and
    every day's high exceeds the prior 20 days' — since the whole
    series only ever increases, every bar is trivially a new 20-day
    high); 2 monotonically FALLING (below SMA200, and never make a new
    20-day high past the initial ramp). -> 4/6 = 66.67% on both
    metrics, by construction."""
    n = 300
    up = np.full(n, 0.003)
    down = np.full(n, -0.003)
    panels = {}
    for k in range(4):
        panels[f"UP{k}"] = _mk_ohlcv(100 * np.cumprod(1 + up))
    for k in range(2):
        panels[f"DOWN{k}"] = _mk_ohlcv(100 * np.cumprod(1 + down))
    uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                        "industry": ["Sector A"] * 6})
    return panels, uni


class TestMarketBreadth:
    def test_compute_breadth_hand_computed(self):
        from screener import cross_section as cs
        panels, _uni = _breadth_universe()
        b = cs.compute_breadth(panels, "latest")
        assert b["n_symbols"] == 6
        assert b["pct_above_200dma"] == pytest.approx(400 / 6, abs=0.01)
        assert b["pct_at_20d_high"] == pytest.approx(400 / 6, abs=0.01)

    def test_thin_history_symbol_excluded_from_denominator(self):
        from screener import cross_section as cs
        panels, _uni = _breadth_universe()
        panels["NEWLIST"] = _mk_ohlcv(
            100 * np.cumprod(1 + np.full(50, 0.001)))  # <200 bars
        b = cs.compute_breadth(panels, "latest")
        assert b["n_symbols"] == 6  # NEWLIST excluded, not counted "below"

    def test_dsl_validate_requires_direction(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "breadth"}]})
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "breadth", "direction": "sideways"}]})
        spec = dsl.validate({"conditions": [
            {"type": "breadth", "direction": "positive"}]})
        assert "market breadth positive" in dsl.describe(spec)

    def test_cond_breadth_positive_and_negative(self):
        from screener import cross_section as cs
        from screener.evaluator import cond_breadth
        panels, _uni = _breadth_universe()
        b = cs.compute_breadth(panels, "latest")  # 66.67% above -> positive
        any_panel = panels["UP0"]
        i = len(any_panel) - 1
        assert cond_breadth(any_panel, {"direction": "positive"}, i,
                            breadth=b) is True
        assert cond_breadth(any_panel, {"direction": "negative"}, i,
                            breadth=b) is False

    def test_cond_breadth_missing_context_fails_closed(self):
        from screener.evaluator import cond_breadth
        panels, _uni = _breadth_universe()
        panel = panels["UP0"]
        i = len(panel) - 1
        assert cond_breadth(panel, {"direction": "positive"}, i,
                            breadth=None) is False

    def test_run_screen_gates_every_symbol_identically(self):
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "breadth", "direction": "positive"}]})
        result = run_screen(panels, spec, universe=uni)
        assert len(result) == 6  # breadth positive -> every symbol passes
        spec_neg = dsl.validate({"conditions": [
            {"type": "breadth", "direction": "negative"}]})
        assert run_screen(panels, spec_neg, universe=uni).empty

    def test_combined_with_trend_narrows_to_rising_symbols_only(self):
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "breadth", "direction": "positive"}]})
        result = run_screen(panels, spec, universe=uni)
        assert set(result["symbol"]) == {"UP0", "UP1", "UP2", "UP3"}

    def test_explainer(self):
        from screener import cross_section as cs, explain
        panels, _uni = _breadth_universe()
        b = cs.compute_breadth(panels, "latest")
        panel = panels["UP0"]
        i = len(panel) - 1
        spec = {"conditions": [{"type": "breadth", "direction": "positive"}]}
        ev = explain.explain_symbol(panel, spec, symbol="UP0", breadth=b)
        assert ev[0]["passed"] is True
        assert "above its 200-day SMA" in ev[0]["evidence"]
        assert ev[0]["values"]["pct_above_200dma"] is not None

    def test_backtest_vectorized_matches_evaluator(self):
        """The CRITICAL acceptance test (ROADMAP Item 14's own bar):
        the backtester's vectorized breadth signal must never disagree
        with the row-by-row evaluator."""
        from screener import backtest as bt
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "breadth", "direction": "positive"}]})
        report = bt.verify_vectorizer_consistency(
            panels, uni, spec, n_samples=50)
        assert report["checked"] > 0
        assert report["mismatches"] == []

    def test_backtest_spec_runs_with_breadth_condition(self):
        from screener import backtest as bt
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "trend", "direction": "up"},
            {"type": "breadth", "direction": "positive"}]})
        result = bt.backtest_spec(panels, uni, spec, horizons=(5,),
                                  cooldown=5, min_events=1,
                                  sensitivity=False)
        assert result["n_symbols"] > 0

    def test_compute_breadth_series_matches_scalar_at_same_date(self):
        """backtest.compute_breadth_series (vectorized, whole calendar)
        and cross_section.compute_breadth (single as-of scalar) must
        agree at the same date — two independent computations of the
        same definition, not one calling the other."""
        from screener import backtest as bt, cross_section as cs
        panels, _uni = _breadth_universe()
        symbols = list(panels.keys())
        series_df = bt.compute_breadth_series(panels, symbols)
        as_of = str(panels["UP0"].index[-1].date())
        scalar = cs.compute_breadth(panels, as_of)
        row = series_df.loc[panels["UP0"].index[-1]]
        assert row["pct_above_200dma"] == pytest.approx(
            scalar["pct_above_200dma"], abs=0.01)
        assert row["pct_at_20d_high"] == pytest.approx(
            scalar["pct_at_20d_high"], abs=0.01)


# ============================================================ ROADMAP Item 9
# Evidence-based strategy presets: new indicators (mom_12_1, roc_126/252,
# sma_150(+slope)) and the atr_pct_percentile / rs_percentile-basis
# cross-sectional conditions they feed.
class TestMomentumIndicators:
    def test_mom_12_1_skips_most_recent_month(self):
        """mom_12_1 must equal the return from t-252 to t-21 — a sharp
        move in the excluded last-21-bar window must NOT show up in it,
        while it does show up in roc_21."""
        n = 300
        closes = 100 * np.cumprod(1 + np.full(n, 0.001))  # steady drift
        closes = closes.astype(float)
        closes[-21:] *= 1.5  # engineered spike inside the skipped month
        panel = _mk_ohlcv(closes)
        i = len(panel) - 1
        expected = 100 * (panel["close"].iloc[i - 21]
                          / panel["close"].iloc[i - 252] - 1)
        assert panel["mom_12_1"].iloc[i] == pytest.approx(expected, rel=1e-9)
        # roc_21 (window return including the spike) must be much larger
        assert panel["roc_21"].iloc[i] > panel["mom_12_1"].iloc[i] + 10

    def test_mom_12_1_nan_with_insufficient_history(self):
        panel = _mk_ohlcv(100 * np.cumprod(1 + np.full(200, 0.001)))
        assert pd.isna(panel["mom_12_1"].iloc[-1])  # needs 252+ bars

    def test_roc_126_252_present_and_correct(self):
        n = 300
        closes = 100 * np.cumprod(1 + np.full(n, 0.001))
        panel = _mk_ohlcv(closes)
        i = len(panel) - 1
        for w in (126, 252):
            expected = 100 * (panel["close"].iloc[i]
                              / panel["close"].iloc[i - w] - 1)
            assert panel[f"roc_{w}"].iloc[i] == pytest.approx(expected)

    def test_sma_150_and_slope_present(self):
        panel = _mk_ohlcv(100 * np.cumprod(1 + np.full(300, 0.001)))
        assert "sma_150" in panel.columns
        assert "sma_150_slope" in panel.columns
        assert "sma_200_slope" in panel.columns
        # steady uptrend -> both long MAs must be rising
        assert panel["sma_150_slope"].iloc[-1] > 0
        assert panel["sma_200_slope"].iloc[-1] > 0


def _vol_dispersion_universe():
    """4 symbols, same drift, deliberately different intraday range
    (the `band` parameter) so ATR% cleanly separates them into clean
    quartiles (25/50/75/100th percentile) — unlike _sector_universe,
    whose fixed 0.4% band makes every symbol's ATR% nearly identical."""
    n = 300
    drift = np.full(n, 0.0005)
    panels = {
        "CALM": _mk_ohlcv(100 * np.cumprod(1 + drift), band=0.002),
        "MID": _mk_ohlcv(100 * np.cumprod(1 + drift), band=0.010),
        "WILD": _mk_ohlcv(100 * np.cumprod(1 + drift), band=0.030),
        "EXTREME": _mk_ohlcv(100 * np.cumprod(1 + drift), band=0.050),
    }
    uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                        "industry": ["Sector A"] * 4})
    return panels, uni


class TestAtrPctPercentile:
    def test_cross_sectional_ordering(self):
        panels, uni = _vol_dispersion_universe()
        df = cross_section.build_cross_section(panels, uni, "latest", 63)
        assert (df.loc["CALM", "atr_percentile"]
               < df.loc["MID", "atr_percentile"]
               < df.loc["WILD", "atr_percentile"]
               < df.loc["EXTREME", "atr_percentile"])

    def test_condition_low_and_high_vol(self):
        panels, uni = _vol_dispersion_universe()
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        low = {"conditions": [
            {"type": "atr_pct_percentile", "op": "<=", "value": 40}]}
        high = {"conditions": [
            {"type": "atr_pct_percentile", "op": ">=", "value": 60}]}
        assert evaluate_symbol(panels["CALM"], low, symbol="CALM",
                               cross_section=cs)
        assert not evaluate_symbol(panels["WILD"], low, symbol="WILD",
                                   cross_section=cs)
        assert evaluate_symbol(panels["WILD"], high, symbol="WILD",
                               cross_section=cs)
        assert not evaluate_symbol(panels["CALM"], high, symbol="CALM",
                                   cross_section=cs)

    def test_requires_cross_section_context(self):
        panels, _uni = _vol_dispersion_universe()
        spec = {"conditions": [
            {"type": "atr_pct_percentile", "op": ">=", "value": 50}]}
        with pytest.raises(RuntimeError):
            evaluate_symbol(panels["WILD"], spec)

    def test_explainer(self):
        from screener import explain
        panels, uni = _vol_dispersion_universe()
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        spec = {"conditions": [
            {"type": "atr_pct_percentile", "op": ">=", "value": 50}]}
        ev = explain.explain_symbol(panels["WILD"], spec, symbol="WILD",
                                    cross_section=cs)
        assert ev[0]["passed"]
        assert "percentile" in ev[0]["evidence"]


class TestRSPercentileBasis:
    def test_basis_defaults_to_return(self):
        c = dsl.validate({"conditions": [
            {"type": "rs_percentile", "op": ">=", "value": 50}]})
        # basis is optional on input; canonicalization fills the default.
        assert dsl.canonicalize_conditions(c["conditions"])[0]["basis"] \
            == "return"

    def test_rejects_unknown_basis(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "rs_percentile", "basis": "nonsense",
                 "op": ">=", "value": 50}]})

    def test_mom_12_1_basis_ranks_differently_from_return(self):
        """A symbol with a huge rally confined to the most recent month
        (excluded from mom_12_1) must rank high on basis='return' but
        NOT on basis='mom_12_1' — the whole point of the skip-month
        construction (LITERATURE.md §1)."""
        n = 300
        flat = np.full(n, 100.0)
        spike = flat.copy()
        spike[-10:] *= 1.6  # confined to the last ~2 weeks
        steady = 100 * np.cumprod(1 + np.full(n, 0.001))  # real 12-1 mover
        panels = {"SPIKER": _mk_ohlcv(spike), "STEADY": _mk_ohlcv(steady),
                  "FLAT": _mk_ohlcv(flat)}
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 3})
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        ret_high = {"conditions": [
            {"type": "rs_percentile", "basis": "return", "window": 63,
             "op": ">=", "value": 90}]}
        mom_high = {"conditions": [
            {"type": "rs_percentile", "basis": "mom_12_1", "op": ">=",
             "value": 90}]}
        assert evaluate_symbol(panels["SPIKER"], ret_high, symbol="SPIKER",
                               cross_section=cs)
        assert not evaluate_symbol(panels["SPIKER"], mom_high,
                                   symbol="SPIKER", cross_section=cs)
        assert evaluate_symbol(panels["STEADY"], mom_high, symbol="STEADY",
                               cross_section=cs)

    def test_explainer_mom_12_1(self):
        from screener import explain
        n = 300
        steady = 100 * np.cumprod(1 + np.full(n, 0.001))
        panels = {"A": _mk_ohlcv(steady), "B": _mk_ohlcv(np.full(n, 100.0))}
        uni = pd.DataFrame({"symbol": ["A", "B"], "name": ["A", "B"],
                           "industry": ["Sector A", "Sector A"]})
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        spec = {"conditions": [
            {"type": "rs_percentile", "basis": "mom_12_1", "op": ">=",
             "value": 50}]}
        ev = explain.explain_symbol(panels["A"], spec, symbol="A",
                                    cross_section=cs)
        assert "12-1 momentum" in ev[0]["evidence"]


class TestNewStrategyPresets:
    """Each new preset (ROADMAP Item 9) against an engineered universe
    where its target profile clearly exists, plus a rejection case."""

    def _leaders_universe(self):
        n = 300
        # LEADER: strong steady 12-1 momentum, liquid.
        leader = 100 * np.cumprod(1 + np.full(n, 0.0035))
        # LAGGARD: flat, illiquid.
        laggard = np.full(n, 100.0)
        panels = {"LEADER": _mk_ohlcv(leader),
                  "LAGGARD": _mk_ohlcv(laggard)}
        panels["LAGGARD"]["volume"] = 1_000.0  # tiny turnover
        panels["LAGGARD"]["turnover_cr"] = (
            panels["LAGGARD"]["close"] * panels["LAGGARD"]["volume"] / 1e7)
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 2})
        return panels, uni

    def test_momentum_12_1_leaders(self):
        from screener import presets
        panels, uni = self._leaders_universe()
        spec = presets.get("momentum_12_1_leaders")["spec"]
        res = run_screen(panels, spec, universe=uni)
        assert list(res["symbol"]) == ["LEADER"]

    def test_near_52w_high_ghw(self):
        from screener import presets
        n = 300
        near_high = 100 * np.cumprod(1 + np.full(n, 0.003))  # grinds to new highs
        far_from_high = np.concatenate([
            100 * np.cumprod(1 + np.full(200, 0.003)),
            np.full(n - 200, 60.0)])  # crashed and stayed down
        panels = {"NEARHIGH": _mk_ohlcv(near_high),
                  "FALLEN": _mk_ohlcv(far_from_high)}
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 2})
        spec = presets.get("near_52w_high_ghw")["spec"]
        res = run_screen(panels, spec, universe=uni)
        assert "NEARHIGH" in set(res["symbol"])
        assert "FALLEN" not in set(res["symbol"])

    def test_tsmom_regime(self):
        from screener import presets
        panel_up = uptrend_pullback_panel()  # close far above sma_200, positive 12m return
        panel_down = breakdown_panel()
        spec = dsl.validate(presets.get("tsmom_regime")["spec"])
        assert evaluate_symbol(panel_up, spec)
        # breakdown panel still spent most of the year rising then broke
        # down sharply in the last 8 bars -- roc_252 may still be positive,
        # so assert on the sideways (flat, no regime) panel instead.
        assert not evaluate_symbol(sideways_panel(), spec)

    def test_ma_timing_highvol(self):
        from screener import presets
        panels, uni = _vol_dispersion_universe()  # CALM/MID/WILD, all uptrends
        spec = dsl.validate(presets.get("ma_timing_highvol")["spec"])
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        assert evaluate_symbol(panels["WILD"], spec, symbol="WILD",
                               cross_section=cs)
        assert not evaluate_symbol(panels["CALM"], spec, symbol="CALM",
                                   cross_section=cs)

    def test_volume_momentum(self):
        from screener import presets
        n = 300
        strong = 100 * np.cumprod(1 + np.full(n, 0.003))
        panels = {"STRONG": _mk_ohlcv(strong, vol_last_ratio=2.0),
                  "WEAK": _mk_ohlcv(np.full(n, 100.0))}
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 2})
        spec = presets.get("volume_momentum")["spec"]
        res = run_screen(panels, spec, universe=uni)
        assert list(res["symbol"]) == ["STRONG"]

    def test_lowvol_defensive(self):
        from screener import presets
        panels, uni = _vol_dispersion_universe()  # CALM/MID/WILD, all uptrends
        spec = dsl.validate(presets.get("lowvol_defensive")["spec"])
        cs = {63: cross_section.build_cross_section(panels, uni,
                                                     "latest", 63)}
        assert evaluate_symbol(panels["CALM"], spec, symbol="CALM",
                               cross_section=cs)
        assert not evaluate_symbol(panels["WILD"], spec, symbol="WILD",
                                   cross_section=cs)

    def test_minervini_stage2(self):
        from screener import presets
        n = 300
        # textbook stage-2: steady long grind up, comfortably off the low,
        # close to the high, all MAs stacked and rising.
        qualifies = 100 * np.cumprod(1 + np.full(n, 0.0025))
        panels = {"STAGE2": _mk_ohlcv(qualifies),
                  "SIDEWAYS": sideways_panel()}
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 2})
        spec = presets.get("minervini_stage2")["spec"]
        res = run_screen(panels, spec, universe=uni)
        assert "STAGE2" in set(res["symbol"])
        assert "SIDEWAYS" not in set(res["symbol"])


class TestResetButton:
    client = TestClient(app)

    def test_reset_button_and_function_served(self):
        html = self.client.get("/").text
        assert 'id="btnReset"' in html and "resetAll()" in html
        js = self.client.get("/app.js").text
        assert "function resetAll()" in js
        # the reset must restore every toggle label it clears
        for label in ("recent screens", "manage my screens",
                      "📊 dashboard", "☆ watchlist"):
            assert label in js
        # and must not touch persisted stores
        assert "watchlist.jsonl" not in js.split("function resetAll()")[1] \
            .split("function toast")[0]


# ============================================================ ROADMAP Item 19
# Link (2003) practitioner screens: stochastics/adx_slope fields, three new
# condition types (threshold_cross, persistence, divergence), five presets.
def _mini_panel(**cols) -> pd.DataFrame:
    """A DataFrame with only the columns a condition actually needs —
    threshold_cross/persistence only ever read one named field. Values
    are coerced to plain arrays first: handing pandas a dict of Series
    that carry their own (default RangeIndex) index alongside an
    explicit `index=` reindexes each column by label instead of by
    position, silently turning every value into NaN."""
    cols = {k: np.asarray(v, dtype=float) for k, v in cols.items()}
    n = len(next(iter(cols.values())))
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame(cols, index=idx)


class TestLinkIndicators:
    def test_stochastic_hand_computed(self):
        """n=5, smooth_k=3, smooth_d=3 — every raw %K, slow %K, and %D
        value below computed independently by hand, not re-derived from
        the implementation."""
        high = [10, 11, 12, 11, 10, 13, 14, 12, 11, 10]
        low = [8, 9, 10, 9, 8, 11, 12, 10, 9, 8]
        close = [9, 10, 11, 10, 9, 12, 13, 11, 10, 9]
        df = _mini_panel(high=pd.Series(high, dtype=float),
                         low=pd.Series(low, dtype=float),
                         close=pd.Series(close, dtype=float))
        out = indicators.stochastic(df, n=5, smooth_k=3, smooth_d=3)
        # raw %K[4]=25, [5]=80, [6]=83.3333, [7]=50, [8]=33.3333, [9]=16.6667
        # slow %K[8] = mean(raw[6],raw[7],raw[8]) = mean(83.3333,50,33.3333)
        assert out["stoch_k"].iloc[8] == pytest.approx(55.5556, abs=0.01)
        assert out["stoch_k"].iloc[9] == pytest.approx(33.3333, abs=0.01)
        # %D[8] = mean(slow_k[6],slow_k[7],slow_k[8])
        assert out["stoch_d"].iloc[8] == pytest.approx(63.1481, abs=0.01)
        assert out["stoch_d"].iloc[9] == pytest.approx(53.3333, abs=0.01)

    def test_stochastic_zero_range_is_nan_not_inf(self):
        n = 20
        flat = pd.Series(100.0, index=range(n))
        df = _mini_panel(high=flat, low=flat, close=flat)
        out = indicators.stochastic(df, n=14, smooth_k=3, smooth_d=3)
        assert pd.isna(out["stoch_k"].iloc[-1])
        assert pd.isna(out["stoch_d"].iloc[-1])
        assert not np.isinf(out["stoch_k"].fillna(0)).any()

    def test_stochastic_saturates_at_100_for_monotonic_uptrend(self):
        """A strictly rising series is always at the top of its own
        n-bar range once the window is full — a real, hand-derivable
        invariant, not a re-implementation check."""
        n = 60
        ramp = pd.Series(np.arange(1, n + 1), dtype=float)
        df = _mini_panel(high=ramp, low=ramp, close=ramp)
        out = indicators.stochastic(df, n=14, smooth_k=3, smooth_d=3)
        assert out["stoch_k"].iloc[-1] == pytest.approx(100.0)
        assert out["stoch_d"].iloc[-1] == pytest.approx(100.0)

    def test_adx_slope_is_5bar_diff_of_adx(self):
        panel = uptrend_pullback_panel()
        pd.testing.assert_series_equal(
            panel["adx_slope"], panel["adx"].diff(5), check_names=False)

    def test_stoch_and_adx_slope_in_known_fields(self):
        assert {"stoch_k", "stoch_d", "adx_slope"} <= dsl.KNOWN_FIELDS


class TestThresholdCross:
    def test_dsl_validation(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "threshold_cross",
                                          "field": "rsi", "level": 40}]})
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "threshold_cross", "field": "rsi", "level": 40,
                 "direction": "sideways"}]})
        spec = dsl.validate({"conditions": [
            {"type": "threshold_cross", "field": "rsi", "level": 40,
             "direction": "above"}]})
        assert "crossed above" in dsl.describe(spec)

    def test_crosses_within_lookback(self):
        from screener.evaluator import cond_threshold_cross
        panel = _mini_panel(rsi=[30, 32, 35, 38, 42, 44])
        c = {"field": "rsi", "level": 40, "direction": "above",
            "lookback": 3}
        assert cond_threshold_cross(panel, c, 5) is True

    def test_crossing_bar_outside_lookback_does_not_match(self):
        from screener.evaluator import cond_threshold_cross
        panel = _mini_panel(rsi=[30, 32, 35, 38, 42, 44])
        c = {"field": "rsi", "level": 40, "direction": "above",
            "lookback": 1}
        # the cross happened at bar 4->5's predecessor pair, already
        # past by the time lookback=1 only looks at bar 5 itself
        assert cond_threshold_cross(panel, c, 5) is False

    def test_nan_adjacent_bar_does_not_falsely_match_or_crash(self):
        from screener.evaluator import cond_threshold_cross
        # the real cross (38->42) is still inside the window and must
        # still be found even with an unrelated NaN earlier in it
        panel = _mini_panel(rsi=[30, 32, np.nan, 38, 42, 44])
        c = {"field": "rsi", "level": 40, "direction": "above",
            "lookback": 3}
        assert cond_threshold_cross(panel, c, 5) is True
        # but a NaN sitting exactly between the two candidate bars means
        # neither adjacent pair can confirm a cross there — no crash,
        # and no false match
        panel2 = _mini_panel(rsi=[30, 32, 35, 38, np.nan, 44])
        assert cond_threshold_cross(panel2, c, 5) is False

    def test_mirror_below_direction(self):
        from screener.evaluator import cond_threshold_cross
        panel = _mini_panel(rsi=[70, 68, 65, 62, 58, 55])
        c = {"field": "rsi", "level": 60, "direction": "below",
            "lookback": 3}
        assert cond_threshold_cross(panel, c, 5) is True


class TestPersistence:
    def test_dsl_validation(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "persistence",
                                          "field": "rsi", "op": ">=",
                                          "value": 60}]})  # missing bars
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "persistence", "field": "rsi", "op": ">=",
                 "value": 60, "bars": 0}]})
        spec = dsl.validate({"conditions": [
            {"type": "persistence", "field": "rsi", "op": ">=",
             "value": 60, "bars": 15}]})
        assert "for all of the last 15 bars" in dsl.describe(spec)

    def test_all_bars_satisfy_matches(self):
        from screener.evaluator import cond_persistence
        panel = _mini_panel(rsi=[65, 66, 64, 70, 68])
        c = {"field": "rsi", "op": ">=", "value": 60, "bars": 5}
        assert cond_persistence(panel, c, 4) is True

    def test_one_bar_below_fails(self):
        from screener.evaluator import cond_persistence
        panel = _mini_panel(rsi=[65, 66, 55, 70, 68])
        c = {"field": "rsi", "op": ">=", "value": 60, "bars": 5}
        assert cond_persistence(panel, c, 4) is False

    def test_insufficient_history_returns_false(self):
        from screener.evaluator import cond_persistence
        panel = _mini_panel(rsi=[65, 66, 64, 70])
        c = {"field": "rsi", "op": ">=", "value": 60, "bars": 5}
        assert cond_persistence(panel, c, 3) is False

    def test_nan_in_window_returns_false(self):
        from screener.evaluator import cond_persistence
        panel = _mini_panel(rsi=[65, 66, np.nan, 70, 68])
        c = {"field": "rsi", "op": ">=", "value": 60, "bars": 5}
        assert cond_persistence(panel, c, 4) is False


def _divergence_panel(pivot1_price, pivot2_price, pivot1_osc, pivot2_osc,
                      *, kind: str) -> pd.DataFrame:
    """30 flat/monotonic bars with two clean, unambiguous fractal pivots
    (k=5) forced at index 10 and 20, >=10 bars apart. `kind='bullish'`
    shapes two pivot LOWS (dips below a strictly rising baseline, so no
    ties can occur elsewhere); `kind='bearish'` shapes two pivot HIGHS
    (spikes above a strictly falling baseline)."""
    n = 30
    if kind == "bullish":
        low = 100 + 0.001 * np.arange(n)
        low[10], low[20] = pivot1_price, pivot2_price
        high = low + 1.0
    else:
        high = 100 - 0.001 * np.arange(n)
        high[10], high[20] = pivot1_price, pivot2_price
        low = high - 1.0
    rsi = np.full(n, 50.0)
    rsi[10], rsi[20] = pivot1_osc, pivot2_osc
    return _mini_panel(high=high, low=low, rsi=rsi)


class TestDivergence:
    def test_dsl_validation(self):
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [{"type": "divergence",
                                          "oscillator": "rsi"}]})
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "divergence", "kind": "sideways",
                 "oscillator": "rsi"}]})
        with pytest.raises(dsl.DSLValidationError):
            dsl.validate({"conditions": [
                {"type": "divergence", "kind": "bullish",
                 "oscillator": "macd"}]})
        spec = dsl.validate({"conditions": [
            {"type": "divergence", "kind": "bullish",
             "oscillator": "rsi"}]})
        assert "bullish divergence" in dsl.describe(spec)

    def test_bullish_divergence_detected(self):
        from screener.evaluator import cond_divergence
        # price: lower low (90 -> 85); oscillator: higher low (30 -> 45)
        panel = _divergence_panel(90, 85, 30, 45, kind="bullish")
        c = {"kind": "bullish", "oscillator": "rsi", "lookback": 25}
        assert cond_divergence(panel, c, len(panel) - 1) is True

    def test_bearish_divergence_detected(self):
        from screener.evaluator import cond_divergence
        # price: higher high (110 -> 115); oscillator: lower high (70 -> 55)
        panel = _divergence_panel(110, 115, 70, 55, kind="bearish")
        c = {"kind": "bearish", "oscillator": "rsi", "lookback": 25}
        assert cond_divergence(panel, c, len(panel) - 1) is True

    def test_control_confirmed_move_is_not_divergence(self):
        """Price makes a lower low AND the oscillator confirms it with
        its own lower low (no fading momentum) — this must NOT read as
        bullish divergence."""
        from screener.evaluator import cond_divergence
        panel = _divergence_panel(90, 85, 45, 30, kind="bullish")
        c = {"kind": "bullish", "oscillator": "rsi", "lookback": 25}
        assert cond_divergence(panel, c, len(panel) - 1) is False

    def test_fewer_than_two_pivots_returns_false(self):
        from screener.evaluator import cond_divergence
        n = 30
        low = 100 + 0.001 * np.arange(n)
        low[10] = 90.0  # only one dip
        high = low + 1.0
        rsi = np.full(n, 50.0)
        panel = _mini_panel(high=high, low=low, rsi=rsi)
        c = {"kind": "bullish", "oscillator": "rsi", "lookback": 25}
        assert cond_divergence(panel, c, len(panel) - 1) is False

    def test_explainer_shows_both_pivot_dates_prices_and_osc_values(self):
        from screener import explain
        panel = _divergence_panel(90, 85, 30, 45, kind="bullish")
        spec = {"conditions": [{"type": "divergence", "kind": "bullish",
                                "oscillator": "rsi", "lookback": 25}]}
        ev = explain.explain_symbol(panel, spec)
        assert ev[0]["passed"] is True
        vals = ev[0]["values"]
        assert vals["pivot1_price"] == pytest.approx(90, abs=0.5)
        assert vals["pivot2_price"] == pytest.approx(85, abs=0.5)
        assert vals["pivot1_osc"] == pytest.approx(30)
        assert vals["pivot2_osc"] == pytest.approx(45)
        assert vals["pivot1_date"] and vals["pivot2_date"]


class TestLinkBacktestVectorizerConsistency:
    """The CRITICAL acceptance test (ROADMAP Item 14's own bar) applied
    to the three new condition types: the backtester's signal path must
    never disagree with the row-by-row evaluator."""

    def test_threshold_cross(self):
        from screener import backtest as bt
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "threshold_cross", "field": "rsi", "level": 50,
             "direction": "above", "lookback": 5}]})
        report = bt.verify_vectorizer_consistency(panels, uni, spec,
                                                   n_samples=80)
        assert report["checked"] > 0
        assert report["mismatches"] == []

    def test_persistence(self):
        from screener import backtest as bt
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "persistence", "field": "rsi", "op": ">=",
             "value": 50, "bars": 10}]})
        report = bt.verify_vectorizer_consistency(panels, uni, spec,
                                                   n_samples=80)
        assert report["checked"] > 0
        assert report["mismatches"] == []

    def test_divergence(self):
        from screener import backtest as bt
        panels, uni = _breadth_universe()
        spec = dsl.validate({"conditions": [
            {"type": "divergence", "kind": "bullish", "oscillator": "rsi",
             "lookback": 40}]})
        report = bt.verify_vectorizer_consistency(panels, uni, spec,
                                                   n_samples=50)
        assert report["checked"] > 0
        assert report["mismatches"] == []


class TestLinkPresets:
    LINK_PRESET_IDS = (
        "link_high_probability_pullback", "link_oscillator_timed_entry",
        "link_trend_breakout", "link_persistent_strength",
        "link_bullish_divergence",
    )

    def test_all_five_registered_with_practitioner_evidence(self):
        from screener import presets
        for pid in self.LINK_PRESET_IDS:
            p = presets.get(pid)
            ev = p["evidence"]
            assert ev["basis"] == "practitioner"
            assert any("Link" in s for s in ev["sources"])
            assert ev["finding"] and ev["caveat"]

    def test_link_persistent_strength_matches_and_rejects(self):
        from screener import presets
        n = 300
        # a PURELY monotonic series has zero down days ever, which makes
        # RSI's loss term 0 -> NaN (divide-by-zero guard), not a high
        # finite reading — noise large enough to occasionally flip a
        # day negative keeps it strongly trending overall (drift
        # dominates the random walk over 300 bars) while giving RSI
        # real (high) values to be persistent about.
        noise = np.random.default_rng(7).normal(0, 0.01, n)
        strong_up = 100 * np.cumprod(1 + np.full(n, 0.006) + noise)
        panels = {"STRONG": _mk_ohlcv(strong_up),
                  "FLAT": sideways_panel()}
        uni = pd.DataFrame({"symbol": list(panels), "name": list(panels),
                           "industry": ["Sector A"] * 2})
        spec = presets.get("link_persistent_strength")["spec"]
        res = run_screen(panels, spec, universe=uni)
        assert "STRONG" in set(res["symbol"])
        assert "FLAT" not in set(res["symbol"])

    def test_link_oscillator_timed_entry_validates_and_describes(self):
        from screener import presets
        spec = dsl.validate(
            presets.get("link_oscillator_timed_entry")["spec"])
        english = dsl.describe(spec)
        assert "crossed above" in english and "uptrend" in english

    def test_link_trend_breakout_validates_and_describes(self):
        from screener import presets
        spec = dsl.validate(presets.get("link_trend_breakout")["spec"])
        english = dsl.describe(spec)
        assert "broke above" in english and "[weekly]" in english

    def test_link_bullish_divergence_validates_and_describes(self):
        from screener import presets
        spec = dsl.validate(presets.get("link_bullish_divergence")["spec"])
        english = dsl.describe(spec)
        assert "bullish divergence" in english
