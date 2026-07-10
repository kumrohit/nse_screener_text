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

from . import config, data_ingest, dsl, evaluator, indicators, universe, universes


def _load_prices(universe_id: str = universes.DEFAULT_UNIVERSE
                 ) -> pd.DataFrame:
    store = config.price_store(universe_id)
    if not store.exists():
        sys.exit(f"No price store found for universe {universe_id!r}. "
                 "Run `python -m screener.cli backfill` first.")
    return pd.read_parquet(store)


def cmd_backfill(args) -> None:
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    uni = universe.fetch_universe(force_refresh=True, universe_id=universe_id)
    prices = data_ingest.full_backfill(uni, universe_id=universe_id)
    data_ingest.fetch_benchmark(universe_id=universe_id)
    print(f"Backfilled {prices['symbol'].nunique()} symbols, "
          f"{len(prices):,} rows, "
          f"{prices['date'].min().date()} → {prices['date'].max().date()}")


def cmd_update(args) -> None:
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    uni = universe.fetch_universe(universe_id=universe_id)
    prices = data_ingest.incremental_update(uni, universe_id=universe_id)
    data_ingest.fetch_benchmark(universe_id=universe_id)
    print(f"Store now ends {prices['date'].max().date()}")


def cmd_verify(args) -> None:
    import sys as _sys
    from . import verify
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    uni = universe.fetch_universe(universe_id=universe_id)
    prices = _load_prices(universe_id)
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
        prices, uni, data_ingest.load_benchmark(universe_id), panels,
        bhav_prices, log_lines, rotated_lines)
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

    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    uni = universe.fetch_universe(universe_id=universe_id)
    warning = evaluator.sector_data_gap_warning(spec, uni)
    if warning:
        print(f"WARNING: {warning}")
    prices = _load_prices(universe_id)
    latest = data_ingest.assert_fresh(prices)
    panels = indicators.build_panels(prices)
    result = evaluator.run_screen(
        panels, spec, universe=uni,
        min_turnover_cr=config.liquidity_gate_cr(universe_id),
        benchmark=data_ingest.load_benchmark(universe_id))

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


def cmd_backtest(args) -> None:
    """Event-study backtest for a DSL spec (ROADMAP Item 14) — readable
    text report, same engine as the API/UI (`screener/backtest.py`)."""
    from . import backtest as bt

    if getattr(args, "preset", None):
        from . import presets
        spec = dsl.validate(presets.get(args.preset)["spec"])
    elif args.json:
        spec = dsl.validate(json.loads(args.query))
    else:
        from . import parser
        spec = parser.parse(args.query)

    print(dsl.describe(spec))
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    uni = universe.fetch_universe(universe_id=universe_id)
    warning = evaluator.sector_data_gap_warning(spec, uni)
    if warning:
        print(f"WARNING: {warning}")
    prices = _load_prices(universe_id)
    data_ingest.assert_fresh(prices)
    panels = indicators.build_panels(prices)
    result = bt.backtest_spec(
        panels, uni, spec, horizons=tuple(args.horizons),
        cooldown=args.cooldown, cost_pct=args.cost_pct,
        min_turnover_cr=config.liquidity_gate_cr(universe_id), stride=args.stride,
        min_events=args.min_events, hypothesis=args.hypothesis,
        benchmark=data_ingest.load_benchmark(universe_id),
        sensitivity=not args.no_sensitivity,
        survivorship_note=universes.get(universe_id).survivorship_note)

    print(f"\n{result['n_symbols']} symbols, {result['n_events_total']} "
         f"events total ({result['elapsed_sec']}s)")
    if args.hypothesis:
        print(f"Hypothesis: {args.hypothesis}")
    for h in args.horizons:
        stats = result["horizons"][h]
        print(f"\n--- {h}-bar horizon ---")
        if stats["insufficient"]:
            print(f"  insufficient events ({stats['count']} < min "
                 f"{args.min_events}) — no stats shown")
            continue
        raw, eg, en = stats["raw"], stats["excess_gross"], stats["excess_net"]
        ci = stats["bootstrap_ci_excess_net_mean"]
        print(f"  events: {stats['count']} across {stats['event_dates']} "
             f"dates")
        print(f"  raw:    event {raw['event_gross_mean']*100:+.2f}% gross / "
             f"{raw['event_net_mean']*100:+.2f}% net  vs. baseline "
             f"{raw['baseline_mean']*100:+.2f}%")
        print(f"  excess (gross): mean {eg['mean']*100:+.2f}%  "
             f"median {eg['median']*100:+.2f}%  hit-rate "
             f"{eg['hit_rate']*100:.0f}%  p5/p95 "
             f"{eg['p5']*100:+.2f}%/{eg['p95']*100:+.2f}%")
        print(f"  excess (net):   mean {en['mean']*100:+.2f}%  "
             f"median {en['median']*100:+.2f}%  hit-rate "
             f"{en['hit_rate']*100:.0f}%  p5/p95 "
             f"{en['p5']*100:+.2f}%/{en['p95']*100:+.2f}%")
        print(f"  bootstrap 90% CI on mean excess (net): "
             f"[{ci['lo5']*100:+.2f}%, {ci['hi95']*100:+.2f}%]")

    if result.get("sensitivity"):
        print("\n--- sensitivity (one-at-a-time, 20-bar excess net) ---")
        for row in result["sensitivity"]:
            print(f"  condition[{row['condition_index']}].{row['param']} "
                 f"(base {row['base_value']}): {row['verdict']}")

    print(f"\n{result['survivorship_note']}")


def _add_universe_arg(p) -> None:
    """`--universe` (ROADMAP Item 15 Phase A) — only `nifty500` is
    registered today, but every command that reads/writes a price store
    already threads the id through, so a second universe (`nse_full`,
    `nse_etf`) is a pure `universes.py` addition, not a plumbing change."""
    p.add_argument("--universe", default=universes.DEFAULT_UNIVERSE,
                   choices=sorted(universes.UNIVERSES),
                   help=f"screen universe (default {universes.DEFAULT_UNIVERSE})")


def main() -> None:
    ap = argparse.ArgumentParser(prog="screener")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bf = sub.add_parser("backfill")
    _add_universe_arg(bf)
    bf.set_defaults(func=cmd_backfill)
    up = sub.add_parser("update")
    _add_universe_arg(up)
    up.set_defaults(func=cmd_update)
    sub.add_parser("bhavcopy-update",
                   help="fetch NSE bhavcopy days (data layer v2, "
                        "side-by-side with yfinance — ROADMAP Item 3)"
                   ).set_defaults(func=cmd_bhavcopy_update)
    vf = sub.add_parser("verify",
                        help="post-backfill data health report")
    vf.add_argument("--jumps", action="store_true",
                    help="list the exact bars behind the adjustment "
                         "smell test, with split-ratio hints")
    _add_universe_arg(vf)
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
    _add_universe_arg(sc)
    sc.set_defaults(func=cmd_screen)

    bt = sub.add_parser("backtest",
                        help="event-study backtest for a screen (ROADMAP "
                             "Item 14) — historical signal dates vs. "
                             "same-date universe baseline")
    bt.add_argument("query", nargs="?", default="",
                    help="natural-language filter or JSON spec")
    bt.add_argument("--preset", help="run a pre-configured screen by id")
    bt.add_argument("--json", action="store_true",
                    help="query is a raw DSL JSON spec (skips the LLM)")
    bt.add_argument("--horizons", type=int, nargs="+", default=[5, 20, 60])
    bt.add_argument("--cooldown", type=int, default=20,
                    help="bars between de-duplicated events (default 20)")
    bt.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.30,
                    help="round-trip cost %% applied to net figures "
                         "(default 0.30)")
    bt.add_argument("--stride", type=int, default=20,
                    help="date-grid stride for the expensive condition "
                         "types (near_support/breakout_resistance/"
                         "bb_squeeze/rs_percentile/sector_rank/"
                         "atr_pct_percentile), default 20 — measured "
                         "against the real 500-symbol store to keep "
                         "these under the perf gate; a smaller stride "
                         "is more precise but much slower")
    bt.add_argument("--min-events", dest="min_events", type=int, default=30)
    bt.add_argument("--hypothesis",
                    help="free-text pre-registered expectation, logged "
                         "with the result")
    bt.add_argument("--no-sensitivity", action="store_true",
                    help="skip the one-at-a-time sensitivity grid "
                         "(faster)")
    _add_universe_arg(bt)
    bt.set_defaults(func=cmd_backtest)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
