"""CLI.

Usage (run locally, not in a sandbox — needs NSE/Yahoo network access):

    python -m screener.cli backfill          # one-time 5y history download
    python -m screener.cli update            # nightly incremental refresh
    python -m screener.cli screen "stocks taking support at 50 EMA and in uptrend"
    python -m screener.cli screen --json '{"logic":"AND","conditions":[...]}'
    python -m screener.cli screen --dry-run "..."   # show interpretation only
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from . import config, data_ingest, dsl, evaluator, indicators, universe


def _load_prices() -> pd.DataFrame:
    if not config.PRICE_STORE.exists():
        sys.exit("No price store found. Run `python -m screener.cli "
                 "backfill` first.")
    return pd.read_parquet(config.PRICE_STORE)


def cmd_backfill(_args) -> None:
    uni = universe.fetch_universe(force_refresh=True)
    prices = data_ingest.full_backfill(uni)
    data_ingest.fetch_benchmark()
    print(f"Backfilled {prices['symbol'].nunique()} symbols, "
          f"{len(prices):,} rows, "
          f"{prices['date'].min().date()} → {prices['date'].max().date()}")


def cmd_update(_args) -> None:
    uni = universe.fetch_universe()
    prices = data_ingest.incremental_update(uni)
    data_ingest.fetch_benchmark()
    print(f"Store now ends {prices['date'].max().date()}")


def cmd_screen(args) -> None:
    if args.json:
        spec = dsl.validate(json.loads(args.query))
    else:
        from . import parser
        spec = parser.parse(args.query)

    print(dsl.describe(spec))
    if args.dry_run:
        print(json.dumps(spec, indent=2))
        return

    uni = universe.fetch_universe()
    prices = _load_prices()
    latest = data_ingest.assert_fresh(prices)
    panels = indicators.build_panels(prices)
    result = evaluator.run_screen(
        panels, spec, universe=uni,
        min_turnover_cr=config.MIN_MEDIAN_TURNOVER_CR,
        benchmark=data_ingest.load_benchmark())

    print(f"As of {latest.date()} — {len(result)} matches\n")
    if result.empty:
        print("No stocks matched.")
    else:
        with pd.option_context("display.max_rows", 100,
                               "display.width", 160):
            print(result.to_string(index=False))
        if args.out:
            result.to_csv(args.out, index=False)
            print(f"\nSaved to {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="screener")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backfill").set_defaults(func=cmd_backfill)
    sub.add_parser("update").set_defaults(func=cmd_update)

    sc = sub.add_parser("screen")
    sc.add_argument("query", help="natural-language filter or JSON spec")
    sc.add_argument("--json", action="store_true",
                    help="query is a raw DSL JSON spec (skips the LLM)")
    sc.add_argument("--dry-run", action="store_true",
                    help="show compiled interpretation without screening")
    sc.add_argument("--out", help="save results CSV to this path")
    sc.set_defaults(func=cmd_screen)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
