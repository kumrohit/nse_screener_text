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
