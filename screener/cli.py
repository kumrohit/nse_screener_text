"""CLI.

Usage (run locally, not in a sandbox — needs NSE/Yahoo network access):

    python -m screener.cli backfill          # one-time 5y history download
    python -m screener.cli update            # nightly incremental refresh
    python -m screener.cli screen "stocks taking support at 50 EMA and in uptrend"
    python -m screener.cli screen --json '{"logic":"AND","conditions":[...]}'
    python -m screener.cli screen --dry-run "..."   # show interpretation only
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # must run before any third-party import
    sys.exit(
        f"This project needs Python 3.10+ (you are on "
        f"{sys.version_info.major}.{sys.version_info.minor} at "
        f"{sys.executable}).\n"
        "On macOS this usually means the Command Line Tools Python was "
        "picked up.\nFix:\n"
        "    brew install python@3.12\n"
        "    python3.12 -m venv .venv && source .venv/bin/activate\n"
        "    pip install -r requirements.txt"
    )

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


def cmd_verify(args) -> None:
    import sys as _sys
    from . import verify
    uni = universe.fetch_universe()
    prices = _load_prices()
    if getattr(args, "jumps", False):
        j = verify.list_jumps(prices)
        if j.empty:
            print("No single-day |moves| >40% in the store.")
        else:
            with pd.option_context("display.width", 160):
                print(j.to_string(index=False))
            print("\nFor UNADJUSTED rows: delete that symbol from the store"
                  "\nand re-run update — a fresh yfinance fetch usually"
                  "\ncomes back adjusted:"
                  "\n  python -m screener.cli refetch SYMBOL")
        return
    panels = indicators.build_panels(prices)
    bhav_prices = (pd.read_parquet(config.BHAVCOPY_STORE)
                  if config.BHAVCOPY_STORE.exists() else None)
    from .webapp import LOG_FILE, ROTATED_LOG_FILE
    log_lines = (LOG_FILE.read_text().strip().splitlines()
                if LOG_FILE.exists() else None)
    rotated_lines = (ROTATED_LOG_FILE.read_text().strip().splitlines()
                     if ROTATED_LOG_FILE.exists() else None)
    results = verify.verify_store(
        prices, uni, data_ingest.load_benchmark(), panels, bhav_prices,
        log_lines, rotated_lines)
    _sys.exit(verify.print_report(results))


def cmd_bhavcopy_update(_args) -> None:
    """Fetch/append NSE bhavcopy days (data layer v2, side-by-side with
    yfinance — see ROADMAP Item 3). Does not touch the live price store
    that screens read from."""
    from . import bhavcopy
    store = bhavcopy.update_bhavcopy_store()
    if store.empty:
        print("No bhavcopy data yet.")
        return
    print(f"Bhavcopy store now covers {store['symbol'].nunique()} symbols, "
          f"{len(store):,} rows, "
          f"{store['date'].min().date()} → {store['date'].max().date()}")


def cmd_refetch(args) -> None:
    """Drop and freshly re-download one symbol (unadjusted-data remedy)."""
    uni = universe.fetch_universe()
    sym = args.symbol.upper()
    if sym not in set(uni["symbol"]):
        _sys_exit = __import__("sys").exit
        _sys_exit(f"{sym} not in the Nifty 500 universe file")
    prices = _load_prices()
    before = (prices["symbol"] == sym).sum()
    prices = prices[prices["symbol"] != sym]
    prices.to_parquet(config.PRICE_STORE, index=False)
    row = uni[uni["symbol"] == sym]
    fresh = data_ingest.full_backfill_symbols(row)
    print(f"{sym}: {before} rows dropped, {len(fresh)} re-fetched")


def cmd_presets(_args) -> None:
    from . import dsl as _dsl, presets
    for p in presets.PRESETS:
        print(f"{p['id']:<28} [{p['group']}] {p['name']}")
        print(f"{'':28} {_dsl.describe(p['spec'])}\n")


def cmd_log(args) -> None:
    import json as _json
    from .webapp import LOG_FILE
    if not LOG_FILE.exists():
        print("No screens logged yet.")
        return
    lines = LOG_FILE.read_text().strip().splitlines()[-args.tail:]
    for ln in reversed(lines):
        e = _json.loads(ln)
        print(f"{e['ts']}  as_of={e['as_of']}  "
              f"matched {e['stats']['matched']}/{e['stats']['evaluated']}: "
              f"{', '.join(e['matched'][:10])}"
              + (" …" if len(e['matched']) > 10 else ""))
        print(f"    {dsl.describe(e['spec'])}")


def cmd_screen(args) -> None:
    if getattr(args, "preset", None):
        from . import presets
        spec = presets.get(args.preset)["spec"]
        spec = dsl.validate(spec)
    elif args.json:
        spec = dsl.validate(json.loads(args.query))
    else:
        from . import parser
        spec = parser.parse(args.query)

    if getattr(args, "as_of", None):
        spec["as_of"] = args.as_of

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

    as_of = spec.get("as_of", "latest")
    shown_date = as_of if as_of != "latest" else latest.date()
    print(f"As of {shown_date} — {len(result)} matches\n")
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
    sub.add_parser("bhavcopy-update",
                   help="fetch NSE bhavcopy days (data layer v2, "
                        "side-by-side with yfinance — ROADMAP Item 3)"
                   ).set_defaults(func=cmd_bhavcopy_update)
    vf = sub.add_parser("verify",
                        help="post-backfill data health report")
    vf.add_argument("--jumps", action="store_true",
                    help="list the exact bars behind the adjustment "
                         "smell test, with split-ratio hints")
    vf.set_defaults(func=cmd_verify)

    lg = sub.add_parser("log", help="recent screen runs (replay trail)")
    lg.add_argument("--tail", type=int, default=10)
    lg.set_defaults(func=cmd_log)

    rf = sub.add_parser("refetch",
                        help="drop and re-download one symbol")
    rf.add_argument("symbol")
    rf.set_defaults(func=cmd_refetch)

    sub.add_parser("presets",
                   help="list pre-configured screens"
                   ).set_defaults(func=cmd_presets)

    sc = sub.add_parser("screen")
    sc.add_argument("query", nargs="?", default="",
                    help="natural-language filter or JSON spec")
    sc.add_argument("--preset", help="run a pre-configured screen by id "
                                     "(see `presets` command)")
    sc.add_argument("--json", action="store_true",
                    help="query is a raw DSL JSON spec (skips the LLM)")
    sc.add_argument("--dry-run", action="store_true",
                    help="show compiled interpretation without screening")
    sc.add_argument("--as-of", dest="as_of",
                    help="screen as of this date (YYYY-MM-DD) instead of "
                         "latest — parity with the web UI's date picker")
    sc.add_argument("--out", help="save results CSV to this path")
    sc.set_defaults(func=cmd_screen)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
