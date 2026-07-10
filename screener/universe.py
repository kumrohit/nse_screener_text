"""Nifty 500 universe management.

NSE publishes the official constituent list as a CSV. NSE blocks naive
scrapers, so we fetch with browser-like headers and cache to disk. If the
fetch fails (IP block / offline), we fall back to the cached copy and warn.
"""
from __future__ import annotations

import io
import sys

import pandas as pd
import requests

from . import config
from .universes import DEFAULT_UNIVERSE


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


def fetch_universe(force_refresh: bool = False,
                   universe_id: str = DEFAULT_UNIVERSE) -> pd.DataFrame:
    """Return DataFrame with columns: symbol, name, industry, yf_ticker.

    `universe_id` only has one valid value today (`nifty500` — the
    fetch source/format below is Nifty-500-specific); it's threaded
    through now so a second universe's own fetcher can plug in later
    without touching every caller again (ROADMAP Item 15 Phase A)."""
    ufile = config.universe_file(universe_id)
    if ufile.exists() and not force_refresh:
        return pd.read_csv(ufile)

    try:
        resp = requests.get(config.NIFTY500_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        raw = pd.read_csv(io.StringIO(resp.text))
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
