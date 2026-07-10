"""Universe registry (ROADMAP Item 15 Phase A — registry-lite).

Every screenable universe is a config entry, not an if-branch scattered
through the codebase. Today this registry holds exactly one universe,
`nifty500` — the point of building it now, before `nse_full`/`nse_etf`
are onboarded, is to prove the abstraction (per-universe storage,
`--universe` threading through the CLI, a state cache keyed by
universe) with **zero behaviour change**: the full test suite green,
spec hashes unchanged, screen log backward-readable. A second universe
should be addable as a new `Universe` entry here plus its own
symbol-list/data-ingestion work, without touching CLI/webapp plumbing
again.

Deliberately minimal for now: no `liquidity_gate`/`sector_enabled`
fields yet, because with a single universe there is nothing for them to
differ against — `config.MIN_MEDIAN_TURNOVER_CR` and the sector/RS
condition types stay global exactly as before. Add those fields to
`Universe` when `nse_full` (a different natural liquidity floor) or
`nse_etf` (sector/RS disabled) actually need them, not speculatively.

All equity universes share the NSE calendar, INR, and the full
indicator field set — the field-mask/calendar/bars_per_year abstraction
from the original multi-asset plan was descoped 2026-07-07 (ROADMAP.md
Item 15) along with the FX and crypto phases.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Universe:
    id: str
    name: str
    benchmark_ticker: str | None    # yfinance ticker for rel_strength, or None
    survivorship_note: str


DEFAULT_UNIVERSE = "nifty500"

UNIVERSES: dict[str, Universe] = {
    "nifty500": Universe(
        id="nifty500",
        name="Nifty 500",
        benchmark_ticker="^NSEI",
        survivorship_note=(
            "Nifty 500 constituents as of the current index list, "
            "projected backward — symbols delisted, merged, or dropped "
            "since aren't in this universe. Flatters strategies "
            "(especially dip-buying) since the names that didn't "
            "survive aren't here to drag the average down."
        ),
    ),
}


def get(universe_id: str) -> Universe:
    try:
        return UNIVERSES[universe_id]
    except KeyError:
        raise ValueError(
            f"unknown universe {universe_id!r}; must be one of "
            f"{sorted(UNIVERSES)}") from None
