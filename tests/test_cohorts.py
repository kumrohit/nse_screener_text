"""Tests for the cohort tracker (ROADMAP Item 16 — screener/cohorts.py).

Covers the locked methodology: dates-frozen adjustment invariance,
pending->active->completed lifecycle, milestone freezing (idempotent),
baseline parity with the backtester (shared helper, not reimplemented),
stale-symbol carry-forward, weighted aggregation, and scorecard
aggregation with small-N suppression.
"""
from __future__ import annotations

import json
import unittest.mock as mock

import numpy as np
import pandas as pd
import pytest

from tests.conftest import last_bday

from screener import backtest, cohorts, config, indicators


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


def _universe(n_symbols=12, n_bars=300, seed=0):
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


class TestCohortCreation:
    def test_create_cohort_starts_pending(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["A", "B"],
                                  weights=cohorts.weights_from_symbols(
                                      ["A", "B"]))
        assert c["status"] == cohorts.STATUS_PENDING
        assert c["entry_date"] is None
        assert all(v is None for v in c["milestones"].values())
        assert c["spec_hash"]

    def test_create_cohort_requires_symbols(self, tmp_data_dir):
        with pytest.raises(ValueError):
            cohorts.create_cohort(universe_id="u", spec=SPEC, symbols=[],
                                  weights={"method": "equal", "by_symbol": {}})

    def test_create_cohort_persists_and_is_listed(self, tmp_data_dir):
        cohorts.create_cohort(universe_id="u", spec=SPEC, symbols=["A"],
                              weights=cohorts.weights_from_symbols(["A"]))
        panels = _universe(n_symbols=2)
        lst = cohorts.list_cohorts("u", {"A": panels["SYM0"],
                                         "B": panels["SYM1"]}, 0)
        assert len(lst) == 1


class TestDeleteCohort:
    def test_delete_removes_it_and_returns_true(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["A"],
                                  weights=cohorts.weights_from_symbols(["A"]))
        assert cohorts.delete_cohort("u", c["cohort_id"])["removed"] is True
        assert cohorts._load_all("u") == []

    def test_delete_unknown_id_returns_false_and_changes_nothing(
            self, tmp_data_dir):
        cohorts.create_cohort(universe_id="u", spec=SPEC, symbols=["A"],
                              weights=cohorts.weights_from_symbols(["A"]))
        assert cohorts.delete_cohort("u", "doesnotexist")["removed"] is False
        assert len(cohorts._load_all("u")) == 1

    def test_delete_only_removes_the_targeted_cohort(self, tmp_data_dir):
        c1 = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                   symbols=["A"],
                                   weights=cohorts.weights_from_symbols(["A"]))
        c2 = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                   symbols=["B"],
                                   weights=cohorts.weights_from_symbols(["B"]))
        cohorts.delete_cohort("u", c1["cohort_id"])
        remaining = cohorts._load_all("u")
        assert len(remaining) == 1
        assert remaining[0]["cohort_id"] == c2["cohort_id"]

    def test_delete_is_scoped_to_its_own_universe(self, tmp_data_dir):
        c = cohorts.create_cohort(universe_id="u1", spec=SPEC,
                                  symbols=["A"],
                                  weights=cohorts.weights_from_symbols(["A"]))
        cohorts.create_cohort(universe_id="u2", spec=SPEC, symbols=["A"],
                              weights=cohorts.weights_from_symbols(["A"]))
        assert cohorts.delete_cohort("u1", c["cohort_id"])["removed"] is True
        assert cohorts._load_all("u1") == []
        assert len(cohorts._load_all("u2")) == 1


class TestWeights:
    def test_equal_weights_sum_to_one(self):
        w = cohorts.weights_from_symbols(["A", "B", "C"])
        assert w["method"] == "equal"
        assert sum(w["by_symbol"].values()) == pytest.approx(1.0, abs=1e-5)
        assert all(v == pytest.approx(1 / 3) for v in w["by_symbol"].values())

    def test_position_weights_sum_to_one_and_reflect_sizing(self):
        positions = [{"symbol": "A", "value": 30_000},
                    {"symbol": "B", "value": 10_000}]
        w = cohorts.weights_from_positions(positions, "risk")
        assert w["method"] == "risk"
        assert pytest.approx(sum(w["by_symbol"].values())) == 1.0
        assert w["by_symbol"]["A"] == pytest.approx(0.75)
        assert w["by_symbol"]["B"] == pytest.approx(0.25)

    def test_zero_total_value_raises(self):
        with pytest.raises(ValueError):
            cohorts.weights_from_positions(
                [{"symbol": "A", "value": 0}], "risk")


class TestPendingLifecycle:
    def test_pending_shows_no_returns_until_entry_bar_exists(self, tmp_data_dir):
        panels = _universe(n_symbols=3)
        # created "today" — the panel's own last bar — so there is no
        # bar strictly after it yet; must stay pending with no returns.
        today = str(panels["SYM0"].index[-1].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=today)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_PENDING
        assert c["entry_date"] is None
        assert all(v is None for v in c["milestones"].values())

    def test_entry_appears_once_a_later_bar_exists(self, tmp_data_dir):
        panels = _universe(n_symbols=3)
        created = str(panels["SYM0"].index[-10].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_ACTIVE
        assert c["entry_date"] is not None
        assert pd.Timestamp(c["entry_date"]) > pd.Timestamp(created)


class TestMilestoneFreeze:
    def test_milestone_freezes_and_is_idempotent_on_recompute(self, tmp_data_dir):
        panels = _universe(n_symbols=5)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1", "SYM2"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1", "SYM2"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["milestones"]["5"] is not None
        snap_5_first = json.dumps(c["milestones"]["5"], sort_keys=True)

        # recompute again — the frozen snapshot must not change, even
        # though live prices at the same underlying bars are unchanged
        # here (a real "did we recompute" check, not just an assert
        # that the module doesn't crash on a second call).
        cohorts.refresh_cohort(c, panels, 0)
        assert json.dumps(c["milestones"]["5"], sort_keys=True) == \
            snap_5_first

    def test_completes_at_60_bars_and_stays_completed(self, tmp_data_dir):
        panels = _universe(n_symbols=3)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_COMPLETED
        assert c["milestones"]["60"] is not None
        # completed cohorts remain readable/listed, not archived away —
        # re-fetch from disk (list_cohorts re-reads the persisted file,
        # independent of the in-memory `c` mutated above) to prove it.
        lst = cohorts.list_cohorts("u", panels, 0)
        assert lst and lst[0]["status"] == cohorts.STATUS_COMPLETED

    def test_milestone_not_reached_yet_stays_none(self, tmp_data_dir):
        panels = _universe(n_symbols=3)
        created = str(panels["SYM0"].index[-3].date())  # only ~2 bars old
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_ACTIVE
        assert c["milestones"]["5"] is None
        assert c["milestones"]["20"] is None


class TestAdjustmentInvariance:
    def test_returns_invariant_under_retroactive_halving(self, tmp_data_dir):
        """Simulates a retroactive split/bonus adjustment (halve the
        whole series) and asserts every frozen return is unchanged —
        percentage returns from open/close ratios are scale-invariant
        by construction, and the cohort record stores only dates, never
        prices, so a later adjustment must not perturb anything."""
        panels = _universe(n_symbols=5, seed=3)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1", "SYM2"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1", "SYM2"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        original = json.loads(json.dumps(c["milestones"]))

        halved_panels = {}
        for sym, panel in panels.items():
            raw = panel[["open", "high", "low", "close", "volume"]].copy()
            raw[["open", "high", "low", "close"]] *= 0.5
            halved_panels[sym] = indicators.compute_panel(raw)

        c2 = cohorts.create_cohort(universe_id="u2", spec=SPEC,
                                   symbols=["SYM0", "SYM1", "SYM2"],
                                   weights=cohorts.weights_from_symbols(
                                       ["SYM0", "SYM1", "SYM2"]),
                                   created_ts=created)
        cohorts.refresh_cohort(c2, halved_panels, 0)

        for h in ("5", "20", "60"):
            assert c2["milestones"][h]["gross"] == \
                pytest.approx(original[h]["gross"], abs=1e-6)
            assert c2["milestones"][h]["excess_net"] == \
                pytest.approx(original[h]["excess_net"], abs=1e-6)


class TestBaselineParity:
    def test_cohort_baseline_matches_backtester_baseline(self, tmp_data_dir):
        """Same engineered universe/date: the cohort's baseline for a
        horizon must equal backtest.compute_baseline()'s value at the
        same signal date — they call the identical function, but this
        proves the cohort wires it correctly (right date, right
        horizon), not just that the function itself is correct."""
        panels = _universe(n_symbols=6, seed=5)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)

        expected_baseline = backtest.compute_baseline(
            panels, list(panels.keys()), (5,), 0)
        sig_date = cohorts._signal_date(panels, c["entry_date"])
        expected = round(float(expected_baseline[5].loc[sig_date]), 4)
        assert c["milestones"]["5"]["baseline"] == pytest.approx(expected)


class TestStaleSymbol:
    def test_stale_symbol_flagged_and_last_close_carried(self, tmp_data_dir):
        panels = _universe(n_symbols=3, seed=7)
        created = str(panels["SYM0"].index[-70].date())
        # truncate SYM1 so it stops trading 10 bars after entry —
        # well before the 20/60-bar milestones, but SYM0/the calendar
        # keep going, so the cohort as a whole is well past those.
        entry_pos = panels["SYM0"].index.get_loc(
            pd.Timestamp(created)) + 1
        truncated = panels["SYM1"].iloc[:entry_pos + 10].copy()
        panels_with_stale = dict(panels)
        panels_with_stale["SYM1"] = truncated

        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels_with_stale, 0)

        m20 = c["milestones"]["20"]
        assert m20["per_symbol"]["SYM1"]["stale"] is True
        assert m20["per_symbol"]["SYM1"]["return"] is not None
        assert m20["n_stale"] == 1
        # never dropped from the aggregate
        assert m20["n_symbols"] == 2
        assert m20["gross"] is not None


class TestWeightedAggregation:
    def test_allocation_weighted_return_differs_from_equal_weight(
            self, tmp_data_dir):
        """Engineered dispersion: SYM0 rallies hard, SYM1 is flat.
        Overweighting SYM0 must produce a materially different (higher)
        aggregate return than equal-weighting the same two symbols."""
        n = 300
        base_end = last_bday()
        rally = _mk_panel(100 * np.cumprod(1 + np.full(n, 0.006)), end=base_end)
        flat = _mk_panel(100 * np.cumprod(1 + np.full(n, 0.0001)), end=base_end)
        panels = {"RALLY": rally, "FLAT": flat}
        created = str(rally.index[-70].date())

        equal = cohorts.create_cohort(
            universe_id="u", spec=SPEC, symbols=["RALLY", "FLAT"],
            weights=cohorts.weights_from_symbols(["RALLY", "FLAT"]),
            created_ts=created)
        cohorts.refresh_cohort(equal, panels, 0)

        overweighted = cohorts.create_cohort(
            universe_id="u", spec=SPEC, symbols=["RALLY", "FLAT"],
            weights=cohorts.weights_from_positions(
                [{"symbol": "RALLY", "value": 90_000},
                 {"symbol": "FLAT", "value": 10_000}], "risk"),
            created_ts=created)
        cohorts.refresh_cohort(overweighted, panels, 0)

        assert overweighted["milestones"]["20"]["gross"] > \
            equal["milestones"]["20"]["gross"]


class TestScorecard:
    def test_two_cohorts_aggregate_correctly_with_enough_names(
            self, tmp_data_dir):
        panels = _universe(n_symbols=8, seed=11)
        created = str(panels["SYM0"].index[-70].date())
        symbols_a = [f"SYM{i}" for i in range(10)]  # 10 names, some missing panels
        symbols_b = [f"SYM{i}" for i in range(10, 20)]
        wide_panels = dict(panels)
        for i in range(20):
            wide_panels.setdefault(f"SYM{i}", panels[f"SYM{i % 8}"])

        c1 = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                   symbols=symbols_a,
                                   weights=cohorts.weights_from_symbols(
                                       symbols_a),
                                   created_ts=created)
        c2 = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                   symbols=symbols_b,
                                   weights=cohorts.weights_from_symbols(
                                       symbols_b),
                                   created_ts=created)
        cohorts.refresh_cohort(c1, wide_panels, 0)
        cohorts.refresh_cohort(c2, wide_panels, 0)
        cohorts._save_all("u", [c1, c2])

        sc = cohorts.scorecard("u", c1["spec_hash"], wide_panels, 0)
        assert sc["n_cohorts_total"] == 2
        h20 = sc["horizons"]["20"]
        assert h20["n_names"] == 20
        assert h20["insufficient"] is False
        assert h20["mean_excess_net"] is not None

    def test_suppresses_mean_below_20_names(self, tmp_data_dir):
        panels = _universe(n_symbols=3, seed=13)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        cohorts._save_all("u", [c])

        sc = cohorts.scorecard("u", c["spec_hash"], panels, 0)
        for h in ("5", "20", "60"):
            assert sc["horizons"][h]["insufficient"] is True
            assert sc["horizons"][h]["mean_excess_net"] is None

    def test_in_sample_lookup_joins_on_spec_hash(self, tmp_data_dir):
        panels = _universe(n_symbols=3, seed=17)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        cohorts._save_all("u", [c])

        log = [{"spec_hash": "other", "universe": "u", "horizons": {"5": "wrong"}},
              {"spec_hash": c["spec_hash"], "universe": "u",
               "horizons": {"5": "right"}},
              {"spec_hash": c["spec_hash"], "universe": "other_universe",
               "horizons": {"5": "wrong universe"}}]
        sc = cohorts.scorecard("u", c["spec_hash"], panels, 0,
                               backtest_log_entries=log)
        assert sc["in_sample"] == {"5": "right"}

    def test_completed_cohorts_still_appear_in_scorecard(self, tmp_data_dir):
        panels = _universe(n_symbols=3, seed=19)
        created = str(panels["SYM0"].index[-70].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_COMPLETED
        cohorts._save_all("u", [c])
        sc = cohorts.scorecard("u", c["spec_hash"], panels, 0)
        assert sc["n_cohorts_total"] == 1


class TestCurrentReturn:
    def test_current_return_is_live_and_never_frozen(self, tmp_data_dir):
        panels = _universe(n_symbols=3, seed=23)
        created = str(panels["SYM0"].index[-3].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        assert c["status"] == cohorts.STATUS_ACTIVE
        cur = cohorts.current_return(panels["SYM0"], c["entry_date"])
        assert cur is not None  # live mark-to-market, no fixed horizon needed

    def test_current_snapshot_none_while_pending(self, tmp_data_dir):
        panels = _universe(n_symbols=2, seed=29)
        today = str(panels["SYM0"].index[-1].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0"]),
                                  created_ts=today)
        cohorts.refresh_cohort(c, panels, 0)
        assert cohorts.current_snapshot(c, panels) is None

    def test_current_snapshot_aggregates_once_active(self, tmp_data_dir):
        panels = _universe(n_symbols=2, seed=31)
        created = str(panels["SYM0"].index[-3].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["SYM0", "SYM1"],
                                  weights=cohorts.weights_from_symbols(
                                      ["SYM0", "SYM1"]),
                                  created_ts=created)
        cohorts.refresh_cohort(c, panels, 0)
        snap = cohorts.current_snapshot(c, panels)
        assert snap is not None
        assert snap["gross"] is not None
        assert set(snap["per_symbol"]) == {"SYM0", "SYM1"}


class TestTwoTierDeletion:
    """Survivorship guard: forward cohorts past entry tombstone with a
    reason; replay/pending hard-delete; scorecard counts tombstones."""

    def _activated_forward(self, tmp_data_dir):
        panels = _universe(n_symbols=2)
        pmap = {"A": panels["SYM0"], "B": panels["SYM1"]}
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["A"],
                                  weights=cohorts.weights_from_symbols(["A"]))
        # a cohort created *now* has no entry bar yet (correct pending
        # behaviour) — backdate created_ts to mimic a cohort created
        # weeks ago, exactly how activated forward cohorts arise in
        # reality; mode stays "forward" (set at creation, not derived).
        raw = cohorts._load_all("u")
        raw[0]["created_ts"] = str(pmap["A"].index[-30].date())
        cohorts._save_all("u", raw)
        lst = cohorts.list_cohorts("u", pmap, 0)
        assert lst and lst[0]["status"] in ("active", "completed")
        return c, pmap

    def test_forward_past_entry_requires_reason(self, tmp_data_dir):
        c, _ = self._activated_forward(tmp_data_dir)
        res = cohorts.delete_cohort("u", c["cohort_id"])
        assert res["removed"] is False and "reason" in res["error"]
        # still visible — nothing changed
        assert len(cohorts._load_all("u")) == 1

    def test_forward_tombstones_with_reason_and_scorecard_counts(
            self, tmp_data_dir):
        c, pmap = self._activated_forward(tmp_data_dir)
        res = cohorts.delete_cohort("u", c["cohort_id"],
                                    reason="mis-tracked screen")
        assert res == {"removed": True, "tombstoned": True, "error": None}
        # hidden from views, record retained
        assert cohorts.list_cohorts("u", pmap, 0) == []
        raw = cohorts._load_all("u")
        assert len(raw) == 1 and raw[0]["status"] == "deleted"
        assert raw[0]["delete_reason"] == "mis-tracked screen"
        # counted on the scorecard
        sc = cohorts.scorecard("u", c["spec_hash"], pmap, 0)
        assert sc["deleted_forward"]["count"] == 1
        assert sc["deleted_forward"]["reasons"] == ["mis-tracked screen"]
        # double delete refused
        res2 = cohorts.delete_cohort("u", c["cohort_id"], reason="again")
        assert res2["error"] == "already deleted"

    def test_replay_hard_deletes_without_reason(self, tmp_data_dir):
        panels = _universe(n_symbols=2)
        pmap = {"A": panels["SYM0"], "B": panels["SYM1"]}
        as_of = str(pmap["A"].index[-40].date())
        c = cohorts.create_cohort(universe_id="u", spec=SPEC,
                                  symbols=["A"],
                                  weights=cohorts.weights_from_symbols(["A"]),
                                  as_of=as_of, panels=pmap)
        cohorts.list_cohorts("u", pmap, 0)   # activate via refresh
        res = cohorts.delete_cohort("u", c["cohort_id"])
        assert res == {"removed": True, "tombstoned": False, "error": None}
        assert cohorts._load_all("u") == []

    def test_tombstone_is_inert_on_refresh(self, tmp_data_dir):
        c, pmap = self._activated_forward(tmp_data_dir)
        cohorts.delete_cohort("u", c["cohort_id"], reason="cleanup")
        before = cohorts._load_all("u")[0].copy()
        cohorts.list_cohorts("u", pmap, 0)   # refresh pass
        after = cohorts._load_all("u")[0]
        assert after == before               # tombstones never advance
