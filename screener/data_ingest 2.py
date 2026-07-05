"""Price ingestion: 5y daily OHLCV for the Nifty 500 via yfinance.

Design notes
------------
* yfinance `auto_adjust=True` handles splits/bonuses — critical for EMAs.
  Dividends also get folded in, which slightly alters raw price levels;
  acceptable for technical screening, and far better than unadjusted data.
* Batch download in chunks with retries; yfinance rate limits are the main
  operational risk of the free-API route.
* Store long-format parquet: one row per (symbol, date). Incremental
  updates only re-fetch the tail.
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import pandas as pd

from . import config

CHUNK = 50
MAX_RETRIES = 3


def _download_chunk(tickers: list[str], start: str) -> pd.DataFrame:
    import yfinance as yf

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = yf.download(
                tickers=tickers, start=start, interval="1d",
                auto_adjust=True, group_by="ticker", threads=True,
                progress=False,
            )
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == MAX_RETRIES:
                raise
            wait = 10 * attempt
            print(f"[ingest] chunk failed ({exc}); retry in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)

    frames = []
    for t in tickers:
        try:
            sub = raw[t].dropna(how="all")
        except KeyError:
            continue
        if sub.empty:
            continue
        sub = sub.rename(columns=str.lower)[
            ["open", "high", "low", "close", "volume"]
        ].copy()
        sub["symbol"] = t.removesuffix(config.YF_SUFFIX)
        sub.index.name = "date"
        frames.append(sub.reset_index())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def full_backfill(universe: pd.DataFrame) -> pd.DataFrame:
    start = (dt.date.today()
             - dt.timedelta(days=int(365.25 * config.HISTORY_YEARS)))
    tickers = universe["yf_ticker"].tolist()
    parts = []
    for i in range(0, len(tickers), CHUNK):
        batch = tickers[i:i + CHUNK]
        print(f"[ingest] {i + len(batch)}/{len(tickers)}", file=sys.stderr)
        parts.append(_download_chunk(batch, start.isoformat()))
        time.sleep(2)  # be polite; avoids yfinance throttling
    prices = pd.concat(parts, ignore_index=True)
    prices = _clean(prices)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(config.PRICE_STORE, index=False)
    return prices


def incremental_update(universe: pd.DataFrame) -> pd.DataFrame:
    """Re-fetch last 10 calendar days and merge (handles late corrections)."""
    if not config.PRICE_STORE.exists():
        return full_backfill(universe)
    prices = pd.read_parquet(config.PRICE_STORE)
    start = (pd.Timestamp.today() - pd.Timedelta(days=10)).date().isoformat()
    tickers = universe["yf_ticker"].tolist()
    parts = [_download_chunk(tickers[i:i + CHUNK], start)
             for i in range(0, len(tickers), CHUNK)]
    fresh = pd.concat(parts, ignore_index=True)
    if not fresh.empty:
        prices = (
            pd.concat([prices, _clean(fresh)], ignore_index=True)
            .drop_duplicates(subset=["symbol", "date"], keep="last")
            .sort_values(["symbol", "date"])
            .reset_index(drop=True)
        )
        prices.to_parquet(config.PRICE_STORE, index=False)
    return prices


def _clean(prices: pd.DataFrame) -> pd.DataFrame:
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"]).dt.tz_localize(None).dt.normalize()
    # Drop impossible bars (data glitches: zero/negative price, high < low)
    bad = (
        (p[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (p["high"] < p["low"])
    )
    return p.loc[~bad].sort_values(["symbol", "date"]).reset_index(drop=True)


def assert_fresh(prices: pd.DataFrame) -> pd.Timestamp:
    """Fail loud if the store is stale. Returns latest bar date."""
    latest = prices["date"].max()
    age = (pd.Timestamp.today().normalize() - latest).days
    if age > config.MAX_STALENESS_DAYS:
        raise RuntimeError(
            f"Price store stale: latest bar {latest.date()} is {age} days "
            "old. Run `python -m screener.cli update` first."
        )
    return latest


# ---------------------------------------------------------------- benchmark
BENCHMARK_TICKER = "^NSEI"
BENCHMARK_STORE = config.DATA_DIR / "benchmark.parquet"


def fetch_benchmark() -> pd.Series:
    """Nifty 50 close series for relative-strength conditions."""
    import yfinance as yf
    start = (dt.date.today()
             - dt.timedelta(days=int(365.25 * config.HISTORY_YEARS)))
    raw = yf.download(BENCHMARK_TICKER, start=start.isoformat(),
                      interval="1d", auto_adjust=True, progress=False)
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):  # yfinance multi-col quirk
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    close.name = "nifty_close"
    close.to_frame().to_parquet(BENCHMARK_STORE)
    return close


def load_benchmark() -> pd.Series | None:
    if BENCHMARK_STORE.exists():
        return pd.read_parquet(BENCHMARK_STORE)["nifty_close"]
    return None
