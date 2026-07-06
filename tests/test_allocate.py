"""Tests for the portfolio allocation engine (ROADMAP Item 10).

Synthetic panels with engineered volatility/price dispersion so the
sizing invariants (risk cap, position cap, sector cap, min ticket) can
be checked exactly, not just "it ran without crashing".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener import allocate, indicators


def _panel(closes: np.ndarray, band: float = 0.02) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = pd.Series(closes, index=dates)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * (1 + band), "low": close * (1 - band),
        "close": close, "volume": pd.Series(1_000_000.0, index=dates),
    })
    return indicators.compute_panel(df)


def _universe(symbols, sectors):
    return pd.DataFrame({"symbol": symbols, "name": symbols,
                        "industry": sectors})


def _basic_universe():
    """4 symbols, 2 sectors, deliberately different price levels and
    volatility bands so risk/inverse-vol sizing meaningfully differs."""
    n = 300
    panels = {
        "CALM_A": _panel(100 * np.cumprod(1 + np.full(n, 0.0008)), band=0.01),
        "CALM_B": _panel(200 * np.cumprod(1 + np.full(n, 0.0008)), band=0.01),
        "WILD_A": _panel(150 * np.cumprod(1 + np.full(n, 0.0008)), band=0.05),
        "WILD_B": _panel(50 * np.cumprod(1 + np.full(n, 0.0008)), band=0.05),
    }
    uni = _universe(list(panels),
                    ["Sector A", "Sector A", "Sector B", "Sector B"])
    return panels, uni


class TestRiskMethodInvariants:
    def test_deployed_never_exceeds_capital(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="risk", risk_pct=1.0)
        assert r["summary"]["deployed"] <= 100_000
        assert sum(p["value"] for p in r["positions"]) == pytest.approx(
            r["summary"]["deployed"])

    def test_per_position_risk_within_budget(self):
        """floor() only rounds shares down, so realized risk per position
        must never exceed the risk budget (capital * risk_pct%)."""
        panels, uni = _basic_universe()
        capital, risk_pct = 100_000, 1.0
        r = allocate.allocate(list(panels), panels, uni, capital=capital,
                              method="risk", risk_pct=risk_pct)
        budget = capital * risk_pct / 100
        for p in r["positions"]:
            assert p["risk"] <= budget + 1e-6

    def test_max_position_pct_enforced(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="risk", risk_pct=50.0,  # deliberately huge
                              max_position_pct=15.0, sector_cap_pct=100.0)
        for p in r["positions"]:
            assert p["pct_of_capital"] <= 15.0 + 1e-6

    def test_rationale_mentions_shares_and_value(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="risk")
        assert r["positions"]
        for p in r["positions"]:
            assert "shares" in p["rationale"] and "₹" in p["rationale"]


class TestAggregateCapitalCap:
    def test_many_positions_never_exceed_capital(self):
        """Regression: caught via live 500-symbol testing, not synthetic
        data. Each position individually respects max_position_pct and
        the sector cap, but with enough distinct-sector candidates the
        sum used to exceed capital -- "risk" sizes each position off
        the risk budget independently with no built-in aggregate cap."""
        n = 300
        panels, sectors = {}, []
        for i in range(9):
            sym = f"S{i}"
            panels[sym] = _panel(100 * np.cumprod(1 + np.full(n, 0.0008)),
                                 band=0.01)
            sectors.append(f"Sector {i}")  # distinct sectors: no sector cap bite
        uni = _universe(list(panels), sectors)
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="risk", risk_pct=1.0,
                              max_position_pct=15.0, sector_cap_pct=100.0,
                              max_positions=9)
        assert r["summary"]["deployed"] <= 100_000
        assert r["summary"]["cash"] >= 0


class TestSectorCap:
    def test_sector_cap_enforced(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="equal", sector_cap_pct=30.0,
                              max_position_pct=100.0)
        sector_totals: dict[str, float] = {}
        for p in r["positions"]:
            sector_totals[p["sector"]] = sector_totals.get(p["sector"], 0.0) \
                + p["pct_of_capital"]
        for pct in sector_totals.values():
            assert pct <= 30.0 + 1e-6

    def test_sector_cap_excludes_or_trims_later_candidates(self):
        """With a very tight sector cap, later same-sector candidates
        must be trimmed/excluded rather than silently ignored."""
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="equal", sector_cap_pct=5.0,
                              max_position_pct=100.0)
        # at most one Sector A and one Sector B position can fit under 5%
        a_positions = [p for p in r["positions"] if p["sector"] == "Sector A"]
        assert len(a_positions) <= 1


class TestMinTicketAndMaxPositions:
    def test_min_ticket_excludes_tiny_positions(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=1_000,
                              method="risk", risk_pct=1.0,
                              min_ticket=5000.0)
        for p in r["positions"]:
            assert p["value"] >= 5000.0

    def test_beyond_max_positions_excluded_with_reason(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="equal", max_positions=2)
        assert len(r["positions"]) <= 2
        beyond = [e for e in r["excluded"]
                 if "max_positions" in e["reason"]]
        assert len(beyond) == 2  # the other 2 of 4 symbols


class TestDegenerateCases:
    def test_empty_symbol_list(self):
        panels, uni = _basic_universe()
        r = allocate.allocate([], panels, uni, capital=100_000, method="risk")
        assert r["positions"] == []
        assert r["summary"]["deployed"] == 0
        assert r["summary"]["cash"] == 100_000
        assert r["summary"]["largest_sector"] is None

    def test_no_match_all_missing_panels(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(["NOPE1", "NOPE2"], panels, uni,
                              capital=100_000, method="risk")
        assert r["positions"] == []
        assert len(r["excluded"]) == 2
        assert all(e["reason"] == "no price data" for e in r["excluded"])

    def test_nan_atr_excluded_never_sized_blind(self):
        """A too-short panel (fewer bars than ATR's min_periods) has NaN
        ATR — must be excluded, not silently sized with a fallback."""
        panels, uni = _basic_universe()
        panels = dict(panels)
        panels["THIN"] = _panel(100 * np.cumprod(1 + np.full(10, 0.001)))
        uni = _universe(list(panels), ["Sector A"] * 2 + ["Sector B"] * 2
                        + ["Sector A"])
        r = allocate.allocate(["THIN"], panels, uni, capital=100_000,
                              method="risk")
        assert r["positions"] == []
        assert any("ATR unavailable" in e["reason"] for e in r["excluded"])

    def test_tiny_capital_degenerate(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=10,
                              method="risk", risk_pct=1.0)
        assert r["summary"]["deployed"] <= 10
        for p in r["positions"]:
            assert p["value"] <= 10

    def test_invalid_method_raises(self):
        panels, uni = _basic_universe()
        with pytest.raises(ValueError):
            allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="mvo")

    def test_nonpositive_capital_raises(self):
        panels, uni = _basic_universe()
        with pytest.raises(ValueError):
            allocate.allocate(list(panels), panels, uni, capital=0,
                              method="risk")


class TestEqualWeightBaseline:
    def test_baseline_always_present_for_non_equal_methods(self):
        panels, uni = _basic_universe()
        for method in ("risk", "inverse_vol"):
            r = allocate.allocate(list(panels), panels, uni,
                                  capital=100_000, method=method)
            assert "baseline" in r
            assert r["baseline"]["method"] == "equal"

    def test_no_redundant_baseline_for_equal(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="equal")
        assert "baseline" not in r

    def test_equal_weight_diverges_from_risk_sizing_on_vol_dispersion(self):
        """CALM_A and WILD_A have the same drift/price scale but very
        different bands (hence ATR) -- equal weight must give them the
        same allocation while risk-based sizing must not."""
        panels, uni = _basic_universe()
        symbols = ["CALM_A", "WILD_A"]
        eq = allocate.allocate(symbols, panels, uni, capital=100_000,
                               method="equal", max_position_pct=100.0,
                               sector_cap_pct=100.0, min_ticket=0.0)
        risk = allocate.allocate(symbols, panels, uni, capital=100_000,
                                 method="risk", risk_pct=1.0,
                                 max_position_pct=100.0,
                                 sector_cap_pct=100.0, min_ticket=0.0)
        eq_pcts = {p["symbol"]: p["pct_of_capital"] for p in eq["positions"]}
        risk_pcts = {p["symbol"]: p["pct_of_capital"]
                    for p in risk["positions"]}
        assert eq_pcts["CALM_A"] == pytest.approx(eq_pcts["WILD_A"], abs=1.0)
        assert risk_pcts["CALM_A"] != pytest.approx(risk_pcts["WILD_A"],
                                                     abs=1.0)

    def test_inverse_vol_favors_calmer_names(self):
        panels, uni = _basic_universe()
        symbols = ["CALM_A", "WILD_A"]
        r = allocate.allocate(symbols, panels, uni, capital=100_000,
                              method="inverse_vol", max_position_pct=100.0,
                              sector_cap_pct=100.0)
        pcts = {p["symbol"]: p["pct_of_capital"] for p in r["positions"]}
        assert pcts["CALM_A"] > pcts["WILD_A"]


class TestStopLevelAndRiskConsistency:
    def test_stop_level_below_entry_and_risk_matches_shares_times_distance(self):
        panels, uni = _basic_universe()
        r = allocate.allocate(list(panels), panels, uni, capital=100_000,
                              method="risk", risk_pct=1.0)
        for p in r["positions"]:
            assert p["stop_level"] < p["entry"]
            implied_distance = p["entry"] - p["stop_level"]
            assert p["risk"] == pytest.approx(
                p["shares"] * implied_distance, rel=1e-2)


class TestRationaleHonesty:
    """The rationale must show the arithmetic that produced the final
    share count, naming the binding cap when one reduced the position."""

    def _lowvol_setup(self):
        # tight band -> tiny ATR -> raw risk sizing wants a huge position,
        # so the 15% position cap is guaranteed to bind
        panels = {"CALMCO": _panel(100 + np.zeros(400), band=0.003)}
        return panels, _universe(["CALMCO"], ["IT"])

    def test_capped_rationale_names_the_cap(self):
        import math
        panels, uni = self._lowvol_setup()
        res = allocate.allocate(["CALMCO"], panels, uni,
                                capital=500000, method="risk", risk_pct=1.0)
        p = res["positions"][0]
        raw = math.floor(5000 / (p["entry"] - p["stop_level"]))
        assert raw > p["shares"], "setup must make the cap bind"
        assert "position cap" in p["rationale"]
        assert f"= {raw} shares" in p["rationale"]
        assert f"effective risk ₹{p['risk']:,.0f}" in p["rationale"]

    def test_uncapped_arithmetic_consistent(self):
        panels, uni = self._lowvol_setup()
        # huge capital + tiny risk -> no cap binds
        res = allocate.allocate(["CALMCO"], panels, uni,
                                capital=50_000_000, method="risk",
                                risk_pct=0.01)
        p = res["positions"][0]
        assert "→" not in p["rationale"]
        assert abs(p["risk"] - p["shares"]
                   * (p["entry"] - p["stop_level"])) < 1
