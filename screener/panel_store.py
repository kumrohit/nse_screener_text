"""Indicator-panel cache (ROADMAP Item 20 P1).

Measured: `indicators.build_panels()` costs ~17.7s on 500 symbols (~71s
on nse_full) and used to be recomputed from raw prices on every single
interactive CLI invocation and webapp cold start — the `INDICATOR_STORE`
cache declared in `config.py` since v0.1 was never implemented. This
module is that implementation.

Design: one per-universe long-format parquet snapshot of every symbol's
full indicator panel (`config.indicator_store(universe_id)`), plus a tiny
JSON sidecar recording `SCHEMA_VERSION` and the price store's own mtime
at save time — the invalidation key. Checking the cache is therefore one
`os.stat()` call, never a parquet read of either file, and a cache HIT
never touches the (much larger) raw prices store at all. `load_or_build`
is the one entry point every interactive call site uses; a MISS builds
from `prices` (loading it first if the caller didn't already) and writes
the cache for next time, so the cost is paid once, not on every command.
"""
from __future__ import annotations

import json

import pandas as pd

from . import config, indicators

SCHEMA_VERSION = 1


def _meta_path(universe_id: str):
    return config.indicator_store(universe_id).with_suffix(".meta.json")


def _price_mtime(universe_id: str) -> float | None:
    store = config.price_store(universe_id)
    return store.stat().st_mtime if store.exists() else None


def save(universe_id: str, panels: dict[str, pd.DataFrame]) -> None:
    """Persist `panels` to the per-universe cache plus a sidecar
    recording the schema version and the price store's mtime at save
    time — the exact invalidation key `_load_cached` checks against."""
    path = config.indicator_store(universe_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for sym, panel in panels.items():
        df = panel.reset_index(names="date")
        df.insert(0, "symbol", sym)
        frames.append(df)
    long_df = (pd.concat(frames, ignore_index=True) if frames
              else pd.DataFrame(columns=["symbol", "date"]))
    long_df.to_parquet(path, index=False)
    _meta_path(universe_id).write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "price_mtime": _price_mtime(universe_id),
    }))


def _load_cached(universe_id: str) -> dict[str, pd.DataFrame] | None:
    """The cache, reshaped back into build_panels()'s exact {symbol:
    date-indexed DataFrame} shape — or None if missing, schema-stale,
    or the price store has moved on since the cache was written."""
    meta_path = _meta_path(universe_id)
    path = config.indicator_store(universe_id)
    if not meta_path.exists() or not path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if meta.get("schema_version") != SCHEMA_VERSION:
        return None
    if meta.get("price_mtime") != _price_mtime(universe_id):
        return None
    long_df = pd.read_parquet(path)
    panels: dict[str, pd.DataFrame] = {}
    for sym, g in long_df.groupby("symbol", sort=False):
        panels[str(sym)] = g.drop(columns="symbol").set_index(
            "date").sort_index()
    return panels


def load_or_build(universe_id: str, prices: pd.DataFrame | None = None
                  ) -> dict[str, pd.DataFrame]:
    """Cache HIT: returns cached panels, never touching `prices` at all
    (the caller's `prices` argument, if given, is simply ignored — nothing
    to build). Cache MISS: builds from `prices` (reading the price store
    first if the caller didn't already pass one — raising the same clear
    error `cli._load_prices` always has if it's missing) and writes the
    cache before returning, so the next call is a hit."""
    cached = _load_cached(universe_id)
    if cached is not None:
        return cached
    if prices is None:
        store = config.price_store(universe_id)
        if not store.exists():
            raise FileNotFoundError(
                f"No price store found for universe {universe_id!r}. "
                "Run `python -m screener.cli backfill` first.")
        prices = pd.read_parquet(store)
    panels = indicators.build_panels(prices)
    save(universe_id, panels)
    return panels
