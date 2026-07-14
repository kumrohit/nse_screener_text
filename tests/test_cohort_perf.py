"""Tests for the cohort performance engine (ROADMAP Item 17, v0.15 —
screener/cohort_perf.py) and the replay-mode extension to
screener/cohorts.py it depends on: mode auto-set, the OOS-scorecard
integrity wall, window/end_date resolution, the locked metric set
(hand-computed against an engineered price path, not just asserted),
Sharpe's 60-bar gate, and adjustment invariance for a replay window.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tests.conftest import last_bday

from screener import backtest, cohort_perf, cohorts, config, indicators


def _mk_panel(closes: np.ndarray, band: float = 0.004,
             end: pd.Timestamp | None = None, vol: float = 1_000_000.0
             ) -> pd.DataFrame:
    n = len(closes)
    end = last_bday(end)  # weekend-safe: see conftest.last_bday
    dates = pd.bdate_range(end=end, periods=n)
    close = pd.Series(closes, index=dates)
    high, low = close * (1 + band), close * (1 - band)
    openp = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(vol, index=dates)
    df = pd.DataFrame({"open": openp, "high": high, "low": low,
                       "close": close, "volume": volume})
    return indicators.compute_panel(df)


def _universe(n_symbols=8, n_bars=300, seed=0):
    rng = np.random.default_rng(seed)
    panels = {}
    for k in range(n_symbols):
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, n_bars))
        panels[f"SYM{k}"] = _mk_panel(closes)
    return panels


SPEC = {"conditions": [{"type": "trend", "direction": "up"}]}


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------- mode
class TestReplayMode:
    def test_as_of_creates_replay_mode(self, tmp_data_dir):
        panels = _universe(n_symbols=4, n_bars=200)
        as_of = str(panels["SYM0"].index[-100].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  as_of=as_of, panels=panels)
        assert c["mode"] == cohorts.MODE_REPLAY
        assert c["as_of"] is not None

    def test_no_as_of_stays_forward_mode(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]))
        assert c["mode"] == cohorts.MODE_FORWARD
        assert c["as_of"] is None

    def test_replay_entry_anchored_to_as_of_not_now(self, tmp_data_dir):
        panels = _universe(n_symbols=4, n_bars=200)
        as_of = str(panels["SYM0"].index[-100].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  as_of=as_of, panels=panels)
        cohorts.refresh_cohort(c, panels, 0)
        # replay resolves to active on the very next refresh — no
        # waiting for real calendar time, the whole point of replay.
        assert c["status"] != cohorts.STATUS_PENDING
        assert pd.Timestamp(c["entry_date"]) > pd.Timestamp(as_of)

    def test_as_of_with_no_later_bar_raises(self, tmp_data_dir):
        panels = _universe(n_symbols=4, n_bars=200)
        latest = str(panels["SYM0"].index[-1].date())
        with pytest.raises(ValueError):
            cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  as_of=latest, panels=panels)

    def test_as_of_resolves_leniently_to_prior_trading_day(
            self, tmp_data_dir):
        panels = _universe(n_symbols=4, n_bars=200)
        # a Saturday two weeks before the end of the panel — not itself
        # a trading day, must roll back rather than raise.
        anchor = panels["SYM0"].index[-30]
        weekend = anchor + pd.offsets.Week(weekday=5)  # the Saturday on/after
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  as_of=str(weekend.date()), panels=panels)
        assert pd.Timestamp(c["as_of"]).dayofweek < 5

    def test_old_records_without_mode_default_to_forward(
            self, tmp_data_dir):
        # simulate a pre-Item-17 record with no mode/as_of fields at all
        f = config.cohorts_file("u")
        f.parent.mkdir(parents=True, exist_ok=True)
        legacy = {
            "cohort_id": "legacy1", "created_ts": "2026-01-01T00:00:00",
            "universe": "u", "spec": SPEC,
            "spec_hash": "abc", "symbols": ["SYM0"],
            "weights": {"method": "equal", "by_symbol": {"SYM0": 1.0}},
            "entry_date": None, "status": "pending", "notes": "",
            "milestones": {"5": None, "20": None, "60": None},
        }
        f.write_text(json.dumps(legacy) + "\n")
        loaded = cohorts._load_all("u")
        assert loaded[0]["mode"] == cohorts.MODE_FORWARD
        assert loaded[0]["as_of"] is None
        # migration also stamps the record current, in the persisted
        # file itself — not just in the in-memory return value — so
        # the next load doesn't redo the same migration work
        assert loaded[0]["schema_version"] == cohorts.SCHEMA_VERSION
        on_disk = json.loads(f.read_text().strip())
        assert on_disk["schema_version"] == cohorts.SCHEMA_VERSION
        assert on_disk["mode"] == cohorts.MODE_FORWARD

    def test_new_cohorts_are_stamped_current_at_creation(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]))
        assert c["schema_version"] == cohorts.SCHEMA_VERSION

    def test_migrate_record_is_idempotent(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]))
        once = cohorts.migrate_record(dict(c))
        twice = cohorts.migrate_record(dict(once))
        assert once == twice


class TestScorecardWall:
    def test_replay_excluded_from_oos_aggregate(self, tmp_data_dir):
        panels = _universe(n_symbols=25, n_bars=300, seed=5)
        created = str(panels["SYM0"].index[-70].date())
        symbols_fwd = [f"SYM{i}" for i in range(12)]
        symbols_replay = [f"SYM{i}" for i in range(12, 24)]

        fwd = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                    symbols=symbols_fwd,
                                    weights=cohorts.weights_from_symbols(
                                        symbols_fwd),
                                    created_ts=created)
        replay = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                       symbols=symbols_replay,
                                       weights=cohorts.weights_from_symbols(
                                           symbols_replay),
                                       as_of=created, panels=panels)
        cohorts.refresh_cohort(fwd, panels, 0)
        cohorts.refresh_cohort(replay, panels, 0)
        cohorts._save_all("u", [fwd, replay])

        sc = cohorts.scorecard("u", fwd["spec_hash"], panels, 0)
        assert sc["n_cohorts_total"] == 1  # forward only
        assert sc["horizons"]["20"]["n_names"] == 12  # only the forward 12
        assert sc["replay"]["n_cohorts"] == 1
        assert sc["replay"]["horizons"]["20"]["n_names"] == 12

    def test_mode_not_overridable_via_create_cohort_kwargs(
            self, tmp_data_dir):
        # create_cohort has no `mode` parameter at all — mode can only
        # ever be derived from as_of, never set directly, which is what
        # actually enforces "not user-overridable" (there's no argument
        # to override it with, by construction).
        import inspect
        sig = inspect.signature(cohorts.create_cohort)
        assert "mode" not in sig.parameters


# ---------------------------------------------------------------- perf engine
def _entry_perf(panels, cohort_symbols, weights, created_ts,
                universe_symbols=None, min_turnover_cr=0, end_date=None,
                benchmark=None):
    c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                              symbols=cohort_symbols, weights=weights,
                              created_ts=created_ts)
    cohorts.refresh_cohort(c, panels, min_turnover_cr)
    return c, cohort_perf.evaluate_performance(
        c, panels, universe_symbols or list(panels.keys()),
        min_turnover_cr, end_date=end_date, benchmark=benchmark)


class TestWindowResolution:
    def test_pending_cohort_returns_none(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=200)
        today = str(panels["SYM0"].index[-1].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=today)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_PENDING
        assert cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0) is None

    def test_end_before_entry_plus_one_is_pending(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=200)
        created = str(panels["SYM0"].index[-70].date())
        c, _ = None, None
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0, end_date=c["entry_date"])
        assert perf is None

    def test_end_date_clamps_to_latest_bar(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=200)
        created = str(panels["SYM0"].index[-70].date())
        c, perf = _entry_perf(panels, ["SYM0"],
                              cohorts.weights_from_symbols(["SYM0"]),
                              created, end_date="2099-01-01")
        assert perf["end_date"] == str(panels["SYM0"].index[-1].date())

    def test_end_on_non_trading_day_resolves_to_prior_bar(
            self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=200)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        entry_pos = panels["SYM0"].index.get_loc(
            pd.Timestamp(c["entry_date"]))
        # find an actual Friday in the window (a bdate_range index is
        # Mon-Fri only) — the Saturday right after it is the non-trading
        # day, and that Friday is the correct "prior bar" it resolves to.
        friday = next(d for d in panels["SYM0"].index[entry_pos + 5:]
                     if d.dayofweek == 4)
        saturday = friday + pd.Timedelta(days=1)
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0,
            end_date=str(saturday.date()))
        assert perf["end_date"] == str(friday.date())


class TestHandComputedMetrics:
    """An engineered, fully deterministic price path — expected values
    are computed independently in-test (not hardcoded literals), so
    this exercises the actual formulas rather than a frozen snapshot."""

    def _build(self):
        lead = [100.0] * 70
        tail = [110.0, 90.0, 121.0, 100.0, 105.0]
        closes = np.array(lead + tail)
        panel = _mk_panel(closes)
        decoy = _mk_panel(np.array([100.0] * len(closes)))  # flat, for baseline
        panels = {"SYM0": panel, "DECOY": decoy}
        created = str(panel.index[len(lead) - 1].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        return panels, c

    def test_cumulative_return_and_equity_curve_index_to_100(
            self, tmp_data_dir):
        panels, c = self._build()
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)
        entry_price = panels["SYM0"]["open"].loc[
            pd.Timestamp(c["entry_date"])]
        end_price = panels["SYM0"]["close"].iloc[-1]
        expected_gross = float(end_price / entry_price - 1)
        assert perf["gross"] == pytest.approx(expected_gross, abs=1e-6)
        assert perf["equity_curve"]["cohort"][0] == pytest.approx(
            100 * float(panels["SYM0"]["close"].loc[
                pd.Timestamp(c["entry_date"])]) / float(entry_price),
            abs=1e-2)

    def test_max_drawdown_value_and_dates_hand_computed(
            self, tmp_data_dir):
        panels, c = self._build()
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)
        dates = pd.to_datetime(perf["equity_curve"]["dates"])
        curve = pd.Series(perf["equity_curve"]["cohort"], index=dates)
        cummax = curve.cummax()
        dd = curve / cummax - 1
        expected_trough = dd.idxmin()
        expected_peak = curve.loc[:expected_trough].idxmax()
        assert perf["max_drawdown"]["pct"] == pytest.approx(
            float(dd.min()), abs=1e-4)
        assert perf["max_drawdown"]["trough_date"] == str(
            expected_trough.date())
        assert perf["max_drawdown"]["peak_date"] == str(
            expected_peak.date())

    def test_excess_vs_baseline_matches_independent_computation(
            self, tmp_data_dir):
        panels, c = self._build()
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)
        baseline_ret = backtest.daily_baseline_returns(
            panels, list(panels.keys()), 0)
        window_dates = pd.to_datetime(perf["equity_curve"]["dates"])
        base_window = baseline_ret.reindex(window_dates).fillna(0.0)
        base_window.iloc[0] = 0.0
        baseline_curve = 100 * (1 + base_window).cumprod()
        expected_baseline_gross = float(baseline_curve.iloc[-1] / 100 - 1)
        # perf["gross"] is already rounded to 4dp, so a tolerance tighter
        # than that would be testing floating-point noise, not the formula
        assert perf["gross"] - expected_baseline_gross == pytest.approx(
            perf["excess_gross_baseline"], abs=1e-4)


class TestSharpeGate:
    def test_sharpe_suppressed_under_60_bars(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=200, seed=2)
        created = str(panels["SYM0"].index[-30].date())
        c, perf = _entry_perf(panels, ["SYM0"],
                              cohorts.weights_from_symbols(["SYM0"]),
                              created)
        assert perf["n_bars"] < 60
        assert perf["sharpe"] is None
        assert perf["sharpe_note"] is not None

    def test_sharpe_present_over_60_bars(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=300, seed=2)
        created = str(panels["SYM0"].index[-100].date())
        c, perf = _entry_perf(panels, ["SYM0"],
                              cohorts.weights_from_symbols(["SYM0"]),
                              created)
        assert perf["n_bars"] >= 60
        assert perf["sharpe"] is not None
        assert perf["sharpe_note"] is None
        daily_rets = pd.Series(
            perf["equity_curve"]["cohort"]).pct_change().dropna()
        expected = float(daily_rets.mean() / daily_rets.std()
                         * np.sqrt(252))
        assert perf["sharpe"] == pytest.approx(expected, abs=1e-2)


class TestContribution:
    def test_weighted_vs_equal_contribution_arithmetic(self, tmp_data_dir):
        n = 200
        base_end = last_bday()
        rally = _mk_panel(100 * np.cumprod(1 + np.full(n, 0.01)),
                          end=base_end)
        flat = _mk_panel(100 * np.cumprod(1 + np.full(n, 0.0001)),
                         end=base_end)
        panels = {"RALLY": rally, "FLAT": flat}
        created = str(rally.index[-70].date())

        c = cohorts.create_cohort(
            universe_id="u", spec=SPEC, symbols=["RALLY", "FLAT"],
            weights=cohorts.weights_from_positions(
                [{"symbol": "RALLY", "value": 90_000},
                 {"symbol": "FLAT", "value": 10_000}], "risk"),
            created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)

        by_sym = {ctr["symbol"]: ctr for ctr in perf["contributors"]}
        assert by_sym["RALLY"]["contribution_gross"] == pytest.approx(
            0.9 * perf["per_symbol"]["RALLY"]["return_gross"], abs=1e-4)
        assert by_sym["FLAT"]["contribution_gross"] == pytest.approx(
            0.1 * perf["per_symbol"]["FLAT"]["return_gross"], abs=1e-4)
        # RALLY dominates the weighted contribution — a real, not
        # coincidental, dispersion check.
        assert (by_sym["RALLY"]["contribution_gross"] >
               by_sym["FLAT"]["contribution_gross"])


class TestPerSymbolRowShape:
    def test_per_symbol_row_carries_own_max_dd_and_contribution(
            self, tmp_data_dir):
        panels, c = TestHandComputedMetrics()._build()
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)
        row = perf["per_symbol"]["SYM0"]
        for key in ("entry_px", "end_px", "return_gross", "return_net",
                   "excess_gross", "excess_net", "weight", "stale",
                   "max_drawdown", "contribution_gross"):
            assert key in row
        assert row["max_drawdown"]["pct"] <= 0
        assert "peak_date" in row["max_drawdown"]
        assert "trough_date" in row["max_drawdown"]
        # single-symbol cohort: its own contribution equals the
        # cohort's overall gross return exactly (weight renormalizes to 1)
        assert row["contribution_gross"] == pytest.approx(
            perf["gross"], abs=1e-4)


class TestStaleSymbolInWindow:
    def test_stale_symbol_flagged_and_carried_in_window(self, tmp_data_dir):
        panels = _universe(n_symbols=3, n_bars=300, seed=7)
        created = str(panels["SYM0"].index[-100].date())
        entry_pos = panels["SYM0"].index.get_loc(
            pd.Timestamp(created)) + 1
        truncated = panels["SYM1"].iloc[:entry_pos + 15].copy()
        panels_with_stale = dict(panels)
        panels_with_stale["SYM1"] = truncated

        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels_with_stale, 0)
        perf = cohort_perf.evaluate_performance(
            c, panels_with_stale, list(panels_with_stale.keys()), 0)
        assert perf["per_symbol"]["SYM1"]["stale"] is True
        assert perf["per_symbol"]["SYM0"]["stale"] is False
        # never dropped — still contributes to the weighted aggregate
        assert "SYM1" in {ctr["symbol"] for ctr in perf["contributors"]}


class TestAdjustmentInvarianceReplay:
    def test_replay_window_invariant_under_retroactive_halving(
            self, tmp_data_dir):
        panels = _universe(n_symbols=4, n_bars=300, seed=13)
        as_of = str(panels["SYM0"].index[-100].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  as_of=as_of, panels=panels)
        cohorts.refresh_cohort(c, panels, 0)
        perf = cohort_perf.evaluate_performance(
            c, panels, list(panels.keys()), 0)

        halved = {}
        for sym, panel in panels.items():
            raw = panel[["open", "high", "low", "close", "volume"]].copy()
            raw[["open", "high", "low", "close"]] *= 0.5
            halved[sym] = indicators.compute_panel(raw)

        c2 = cohorts.create_cohort(universe_id="u2", spec=SPEC,
                                   symbols=["SYM0", "SYM1"],
                                   weights=cohorts.weights_from_symbols(
                                       ["SYM0", "SYM1"]),
                                   as_of=as_of, panels=halved)
        cohorts.refresh_cohort(c2, halved, 0)
        perf2 = cohort_perf.evaluate_performance(
            c2, halved, list(halved.keys()), 0)

        assert perf2["gross"] == pytest.approx(perf["gross"], abs=1e-6)
        assert perf2["excess_gross_baseline"] == pytest.approx(
            perf["excess_gross_baseline"], abs=1e-6)
        assert perf2["max_drawdown"]["pct"] == pytest.approx(
            perf["max_drawdown"]["pct"], abs=1e-6)
