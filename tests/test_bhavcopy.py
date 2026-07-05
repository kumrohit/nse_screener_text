"""Tests for the NSE bhavcopy data layer (ROADMAP Item 3, pre-cutover).

No live network calls here — `fetch_day` is exercised against a
monkeypatched `requests.get` returning fixture CSV text shaped exactly
like the real `sec_bhavdata_full` file (confirmed against a live fetch
on 2026-07-03: SYMBOL, SERIES, DATE1, ..., CLOSE_PRICE, ..., DELIV_PER).
`parse_adjustment_factor`'s regexes were built from real NSE
corporate-action subject lines (fetched 2026-07-05); the values below
reproduce those exact strings.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from screener import bhavcopy


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


_BHAV_CSV = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
    "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
    "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
    "1018GS2026, GS, 03-Jul-2026, 103.85, 103.85, 103.85, 103.85, 103.85, "
    "103.85, 103.85, 1, 0.00, 1, 1, 100.00\n"
    "TESTCO, EQ, 03-Jul-2026, 100.00, 101.00, 105.00, 99.00, 104.00, "
    "104.00, 102.50, 10000, 10.25, 50, 4000, 40.00\n"
    "BADCO, EQ, 03-Jul-2026, 50.00, 51.00, 49.00, 52.00, 50.50, "
    "50.50, 50.50, 500, 0.25, 5, 100, 20.00\n"
)


class TestFetchDay:
    def test_filters_to_eq_and_parses_delivery(self, monkeypatch):
        monkeypatch.setattr(
            bhavcopy.requests, "get",
            lambda url, headers=None, timeout=None: _FakeResp(_BHAV_CSV))
        df = bhavcopy.fetch_day(dt.date(2026, 7, 3))
        # GS (government security) row filtered out by SERIES=='EQ';
        # BADCO dropped for high < low (impossible bar)
        assert list(df["symbol"]) == ["TESTCO"]
        row = df.iloc[0]
        assert row["close"] == 104.0
        assert row["delivery_pct"] == 40.0
        assert row["date"] == pd.Timestamp("2026-07-03")

    def test_returns_none_on_404(self, monkeypatch):
        monkeypatch.setattr(
            bhavcopy.requests, "get",
            lambda url, headers=None, timeout=None: _FakeResp(
                "", status_code=404))
        assert bhavcopy.fetch_day(dt.date(2026, 6, 28)) is None


class TestParseAdjustmentFactor:
    @pytest.mark.parametrize("subject,expected", [
        ("Bonus 3:1", 0.25),
        ("Bonus 1:1", 0.5),
        ("Bonus 1:2", 2 / 3),
        ("Bonus 5:1", 1 / 6),
        ("Face Value Split (Sub-Division) - From Rs10/- Per Share "
         "To Rs 5/- Per Share", 0.5),
        ("Face Value Split (Sub-Division) - From Rs10/- Per Share "
         "To Re 1/- Per Share", 0.1),
        ("Face Value Split (Sub-Division) - From Rs 2/- Per Share "
         "To Re 1/- Per Share", 0.5),
    ])
    def test_recognised_actions(self, subject, expected):
        got = bhavcopy.parse_adjustment_factor(subject)
        assert got == pytest.approx(expected, abs=1e-4)

    @pytest.mark.parametrize("subject", [
        "Dividend - Rs 5 Per Share",
        "Interim Dividend - Rs 24 Per Share",
        "Annual General Meeting",
        "Amalgamation / Merger",
    ])
    def test_non_split_bonus_returns_none(self, subject):
        assert bhavcopy.parse_adjustment_factor(subject) is None


class TestAdjustmentCompounding:
    def _actions(self):
        return pd.DataFrame({
            "symbol": ["ABC", "ABC"],
            "ex_date": [pd.Timestamp("2024-03-01"),
                       pd.Timestamp("2024-06-01")],
            "subject": ["Bonus 1:1", "Bonus 3:1"],
            "series": ["EQ", "EQ"],
            "factor": [0.5, 0.25],
        })

    def test_cumulative_factor_compounds_backward(self):
        adj = bhavcopy.build_adjustment_factors(self._actions())
        row_t1 = adj[adj["ex_date"] == "2024-03-01"].iloc[0]
        row_t2 = adj[adj["ex_date"] == "2024-06-01"].iloc[0]
        assert row_t2["cum_factor"] == pytest.approx(0.25)
        assert row_t1["cum_factor"] == pytest.approx(0.5 * 0.25)

    def test_apply_adjustments_ordering(self):
        """Bar before both actions gets both factors; bar between the two
        actions gets only the later factor; bar after both is untouched.
        This is exactly the bug an oldest-first application order would
        get wrong (see the comment in bhavcopy.apply_adjustments)."""
        adj = bhavcopy.build_adjustment_factors(self._actions())
        prices = pd.DataFrame({
            "symbol": ["ABC", "ABC", "ABC"],
            "date": [pd.Timestamp("2024-01-01"),
                    pd.Timestamp("2024-04-01"),
                    pd.Timestamp("2024-09-01")],
            "open": [100.0, 100.0, 100.0],
            "high": [100.0, 100.0, 100.0],
            "low": [100.0, 100.0, 100.0],
            "close": [100.0, 100.0, 100.0],
        })
        out = bhavcopy.apply_adjustments(prices, adj)
        assert out["close"].iloc[0] == pytest.approx(100 * 0.5 * 0.25)
        assert out["close"].iloc[1] == pytest.approx(100 * 0.25)
        assert out["close"].iloc[2] == pytest.approx(100.0)

    def test_unparsed_actions_excluded_not_defaulted(self):
        acts = pd.DataFrame({
            "symbol": ["XYZ"], "ex_date": [pd.Timestamp("2024-01-01")],
            "subject": ["Dividend - Rs 5 Per Share"], "series": ["EQ"],
            "factor": [float("nan")],
        })
        adj = bhavcopy.build_adjustment_factors(acts)
        assert adj.empty


class TestCrossSourceVerify:
    def _stores(self):
        from screener import verify
        yf = pd.DataFrame({
            "symbol": ["AAA", "AAA", "BBB"],
            "date": [pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-02"),
                    pd.Timestamp("2026-07-01")],
            "close": [100.0, 101.0, 200.0],
        })
        return verify, yf

    def test_agreement_within_tolerance_passes(self):
        verify, yf = self._stores()
        bhav = yf.copy()
        bhav["close"] = bhav["close"] * 1.0001  # 0.01% noise
        row = verify.check_cross_source(yf, bhav)
        assert row[1] == verify.PASS

    def test_systematic_divergence_warns(self):
        verify, yf = self._stores()
        bhav = yf.copy()
        bhav["close"] = bhav["close"] * 1.02  # 2% gap
        row = verify.check_cross_source(yf, bhav)
        assert row[1] == verify.WARN
        assert "investigate" in row[2]

    def test_missing_bhav_data_warns_not_crashes(self):
        verify, yf = self._stores()
        row = verify.check_cross_source(yf, None)
        assert row[1] == verify.WARN
        row2 = verify.check_cross_source(yf, pd.DataFrame())
        assert row2[1] == verify.WARN
