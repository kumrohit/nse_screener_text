"""Per-universe symbol-list management (ROADMAP Item 15 Phase A).

NSE publishes both the Nifty 500 constituent list (index membership,
carries a sector/industry classification) and the full exchange equity
listing (every EQ-series symbol, no sector data) as CSVs. NSE blocks
naive scrapers, so both are fetched with browser-like headers and
cached to disk. If a live fetch fails (IP block / offline), fall back
to the cached copy and warn rather than fail outright.
"""
from __future__ import annotations

import io
import sys

import pandas as pd
import requests

from . import config
from .universes import DEFAULT_UNIVERSE

NSE_FULL_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


def _fetch_csv(url: str, ufile, timeout: int = 30) -> pd.DataFrame:
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text))


def _fetch_nifty500(force_refresh: bool, ufile) -> pd.DataFrame:
    if ufile.exists() and not force_refresh:
        return pd.read_csv(ufile)
    try:
        raw = _fetch_csv(config.NIFTY500_URL, ufile)
    except Exception as exc:  # noqa: BLE001
        if ufile.exists():
            print(f"[universe] refresh failed ({exc}); using cached list",
                  file=sys.stderr)
            return pd.read_csv(ufile)
        raise RuntimeError(
            "Could not fetch Nifty 500 list and no cached copy exists. "
            "Download ind_nifty500list.csv manually from NSE and place it "
            f"at {ufile}."
        ) from exc

    raw.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]
    df = pd.DataFrame({
        "symbol": raw["symbol"].str.strip(),
        "name": raw["company_name"].str.strip(),
        "industry": raw.get("industry", pd.Series(dtype=str)),
    })
    df["yf_ticker"] = df["symbol"] + config.YF_SUFFIX
    ufile.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ufile, index=False)
    return df


def _fetch_nse_full(force_refresh: bool, ufile) -> pd.DataFrame:
    """Every NSE EQ-series symbol (~2,000+), vs. nifty500's ~500. NSE's
    raw listing carries no sector/industry classification (that's an
    index-methodology concept the Nifty 500 CSV has and this one
    doesn't) — `industry` is left empty rather than guessed, same
    NaN-safe handling as any other thin/missing data in this codebase."""
    if ufile.exists() and not force_refresh:
        return pd.read_csv(ufile)
    try:
        raw = _fetch_csv(NSE_FULL_URL, ufile)
    except Exception as exc:  # noqa: BLE001
        if ufile.exists():
            print(f"[universe] refresh failed ({exc}); using cached list",
                  file=sys.stderr)
            return pd.read_csv(ufile)
        raise RuntimeError(
            "Could not fetch the NSE full equity list and no cached copy "
            f"exists. Download EQUITY_L.csv manually from NSE and place "
            f"it at {ufile}."
        ) from exc

    raw.columns = [c.strip() for c in raw.columns]
    raw["SERIES"] = raw["SERIES"].astype(str).str.strip()
    eq = raw[raw["SERIES"] == "EQ"].copy()
    df = pd.DataFrame({
        "symbol": eq["SYMBOL"].astype(str).str.strip(),
        "name": eq["NAME OF COMPANY"].astype(str).str.strip(),
        "industry": pd.Series(dtype=str, index=eq.index),
    })
    df["yf_ticker"] = df["symbol"] + config.YF_SUFFIX
    ufile.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ufile, index=False)
    return df


_FETCHERS = {
    "nifty500": _fetch_nifty500,
    "nse_full": _fetch_nse_full,
}


def fetch_universe(force_refresh: bool = False,
                   universe_id: str = DEFAULT_UNIVERSE) -> pd.DataFrame:
    """Return DataFrame with columns: symbol, name, industry, yf_ticker.
    Dispatches to the right fetch source/format per `universe_id` — a
    new universe plugs in here plus a registry entry, no other caller
    needs to change (ROADMAP Item 15 Phase A)."""
    ufile = config.universe_file(universe_id)
    try:
        fetcher = _FETCHERS[universe_id]
    except KeyError:
        raise ValueError(
            f"no symbol-list fetcher registered for {universe_id!r}"
        ) from None
    return fetcher(force_refresh, ufile)
