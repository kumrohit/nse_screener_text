"""Universe registry (ROADMAP Item 15 Phase A — registry-lite).

Every screenable universe is a config entry, not an if-branch scattered
through the codebase. Started with exactly one universe, `nifty500`,
to prove the abstraction (per-universe storage, `--universe` threading
through the CLI, a state cache keyed by universe) with **zero behaviour
change** before onboarding more. `nse_full`: all NSE EQ-series symbols
(~2,000+, vs. Nifty 500's 500), sourced from NSE's own listing archive
rather than the index-membership CSV nifty500 uses (see `universe.py`'s
per-universe fetch dispatch) and backfilled via the same yfinance
pipeline as nifty500 — no new adjustment-correctness code, just a
bigger symbol list. `nse_etf`: a curated list of ~36 broad domestic
equity-index ETFs (NIFTYBEES, BANKBEES, etc.) — curated rather than
auto-fetched-and-classified because NSE's ETF listing's `Underlying`
column mixes fund names into what should be index names for a large
share of rows, too inconsistent to classify reliably by keyword.

`liquidity_gate_cr` is the first field that earns its place on
`Universe`: nse_full's much longer tail of thin, sometimes-barely-traded
names needs a stricter floor (₹2cr vs nifty500's ₹0.5cr) to keep data
glitches (near-zero volume days) from masquerading as real liquidity.
`sector_enabled` is still not a field — NSE's raw equity listing carries
no sector/industry classification (that's an index-methodology concept,
not a raw-listing one), so `sector`/`sector_rank` conditions simply find
no matches for nse_full today; that's a real limitation, not a bug, and
doesn't need a registry flag to be handled correctly (the existing
NaN-industry code paths already degrade gracefully).

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
    liquidity_gate_cr: float        # min 20-day median turnover, crores INR
    survivorship_note: str


DEFAULT_UNIVERSE = "nifty500"

UNIVERSES: dict[str, Universe] = {
    "nifty500": Universe(
        id="nifty500",
        name="Nifty 500",
        benchmark_ticker="^NSEI",
        liquidity_gate_cr=0.5,
        survivorship_note=(
            "Survivorship caveat: Nifty 500 constituents as of the "
            "current index list, projected backward — symbols "
            "delisted, merged, or dropped since aren't in this "
            "universe. Flatters strategies (especially dip-buying) "
            "since the names that didn't survive aren't here to drag "
            "the average down."
        ),
    ),
    "nse_full": Universe(
        id="nse_full",
        name="NSE Full (all EQ series)",
        benchmark_ticker="^NSEI",
        liquidity_gate_cr=2.0,
        survivorship_note=(
            "Survivorship caveat: all NSE EQ-series symbols as of the "
            "current exchange listing, projected backward — an even "
            "more aggressive survivorship bias than Nifty 500. Names "
            "outside any index delist, get suspended, or merge far "
            "more often, and none of that churn is represented here. "
            "No sector/industry classification is available for this "
            "universe (NSE's raw listing doesn't carry one), so "
            "sector-based screens will find nothing to match. Treat "
            "results as more exploratory and noisier than Nifty 500's."
        ),
    ),
    "nse_etf": Universe(
        id="nse_etf",
        name="NSE Equity ETFs",
        benchmark_ticker="^NSEI",
        liquidity_gate_cr=0.1,
        survivorship_note=(
            "Survivorship note: this is a curated, hand-verified list "
            "of broad domestic equity-index ETFs (NSE's own ETF "
            "listing carries no reliable machine-classifiable index "
            "field — see universe.py — so this is not an automatic "
            "fetch), deliberately excluding gold/silver/commodity/"
            "debt/international-index/money-market ETFs. Sector/"
            "industry classification is not available (same reason as "
            "nse_full). ETF closures are rare relative to stock "
            "delistings, so survivorship bias here is smaller than "
            "for an equity universe, but not zero."
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
