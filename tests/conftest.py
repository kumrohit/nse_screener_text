"""Shared pytest fixtures.

The web/evidence tests assert against the synthetic 8-stock demo
universe (PULLBK, STEADY, BRKDWN, ...), which `webapp._load_state()`
only builds when `config.PRICE_STORE` doesn't exist. That's true in CI,
but not on a dev machine that has already run `backfill` — there,
`_load_state()` would load the real 500-symbol store instead, and
every demo-mode assertion fails. Force demo mode for the whole session
so the suite is hermetic regardless of local disk state.
"""
from __future__ import annotations

from unittest import mock

import pytest

from screener import config, webapp


@pytest.fixture(autouse=True, scope="session")
def _force_demo_mode():
    with mock.patch.object(config, "PRICE_STORE",
                           config.DATA_DIR / "__no_such_price_store__.parquet"):
        webapp._state.clear()
        yield
    webapp._state.clear()


@pytest.fixture(autouse=True)
def _isolate_webapp_log_files(tmp_path, monkeypatch):
    """webapp's log/store constants (LOG_FILE, BACKTEST_LOG_FILE, ...) are
    bound once at import time from the real config.DATA_DIR, so patching
    config.DATA_DIR in an individual test does NOT redirect them — every
    /api/screen, /api/backtest etc. call in a test run was silently
    appending demo-data entries to the real data/ store. Point every one
    at this test's own tmp_path by default; tests that need a specific
    path can still monkeypatch over this within the test as before."""
    for name in ("LOG_FILE", "ROTATED_LOG_FILE", "ALLOCATION_LOG_FILE",
                "BACKTEST_LOG_FILE", "WATCHLIST_FILE", "USER_PRESETS_FILE"):
        monkeypatch.setattr(webapp, name, tmp_path / name.lower(),
                            raising=False)
