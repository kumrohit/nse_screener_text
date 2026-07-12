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

    # Shared history with the web UI's /api/screen (same LOG_FILE, same
    # entry shape) — this is what makes `cohort create --from-last-screen`
    # work regardless of which surface ran the screen.
    from .webapp import _log_run
    matches = [{"symbol": s} for s in result["symbol"]] if not result.empty else []
    _log_run(spec, str(shown_date),
             {"matched": len(result), "evaluated": len(panels)}, matches,
             dsl.spec_hash(spec), universe_id)


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


def cmd_cohort_create(args) -> None:
    """Freeze a cohort for walk-forward out-of-sample tracking
    (ROADMAP Item 16) — either the matches from the most recent logged
    screen for this universe, or an explicit spec + symbol list. `--as-of`
    (ROADMAP Item 17) freezes a REPLAY cohort instead — as of any
    historical date already in the store — which is in-sample by
    construction and excluded from the OOS scorecard."""
    from . import cohorts as cohorts_mod
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)

    if args.symbols:
        if args.preset:
            from . import presets
            spec = dsl.validate(presets.get(args.preset)["spec"])
        elif args.json:
            spec = dsl.validate(json.loads(args.query))
        else:
            sys.exit("--symbols needs --preset or --json to supply the spec")
        symbols = args.symbols
    else:
        from .webapp import LOG_FILE
        if not LOG_FILE.exists():
            sys.exit("No screens logged yet. Run `screen` first, or pass "
                     "--symbols with --preset/--json.")
        entries = [json.loads(l) for l in
                  LOG_FILE.read_text().strip().splitlines() if l]
        matches = [e for e in entries
                  if e.get("universe", universes.DEFAULT_UNIVERSE)
                  == universe_id]
        if not matches:
            sys.exit(f"No logged screens for universe {universe_id!r}. Run "
                     f"`screen --universe {universe_id}` first.")
        entry = matches[-1]
        spec, symbols = entry["spec"], entry["matched"]
        if not symbols:
            sys.exit("The last screen for this universe matched 0 symbols "
                     "— nothing to track.")

    weights = cohorts_mod.weights_from_symbols(symbols)
    panels = None
    if getattr(args, "as_of", None):
        prices = _load_prices(universe_id)
        panels = indicators.build_panels(prices)
    try:
        cohort = cohorts_mod.create_cohort(
            universe_id=universe_id, spec=spec, symbols=symbols,
            weights=weights, notes=args.notes or "",
            as_of=getattr(args, "as_of", None), panels=panels)
    except ValueError as exc:
        sys.exit(str(exc))
    print(dsl.describe(spec))
    mode_note = f", mode={cohort['mode']}" if cohort["mode"] == "replay" else ""
    print(f"Created cohort {cohort['cohort_id']} — {len(symbols)} symbols, "
         f"universe={universe_id}, status={cohort['status']}{mode_note}")


def cmd_cohort_list(args) -> None:
    from . import cohorts as cohorts_mod
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    prices = _load_prices(universe_id)
    panels = indicators.build_panels(prices)
    lst = cohorts_mod.list_cohorts(universe_id, panels,
                                   config.liquidity_gate_cr(universe_id))
    if not lst:
        print("No cohorts yet.")
        return
    for c in lst:
        print(f"{c['cohort_id']}  {c['status']:<10} "
             f"entry={c['entry_date'] or '—':<12} {len(c['symbols']):>3} "
             f"symbols  {dsl.describe(c['spec'])}")


def cmd_cohort_show(args) -> None:
    from . import cohorts as cohorts_mod
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    prices = _load_prices(universe_id)
    panels = indicators.build_panels(prices)
    c = cohorts_mod.get_cohort(universe_id, args.cohort_id, panels,
                               config.liquidity_gate_cr(universe_id))
    if c is None:
        sys.exit(f"No cohort {args.cohort_id!r} in universe {universe_id!r}")

    mode_line = f"  [REPLAY — as of {c['as_of']}]" if c["mode"] == "replay" else ""
    print(f"Cohort {c['cohort_id']}  status={c['status']}  "
         f"entry={c['entry_date'] or 'pending'}{mode_line}")
    if c["mode"] == "replay":
        print(cohorts_mod.REPLAY_SURVIVORSHIP_NOTE)
    print(dsl.describe(c["spec"]))
    print(f"Symbols ({c['weights']['method']} weighted): "
         f"{', '.join(c['symbols'])}")
    for h in cohorts_mod.HORIZONS:
        m = c["milestones"][str(h)]
        if m is None:
            print(f"  {h}-bar: not reached yet")
            continue
        print(f"  {h}-bar: gross {m['gross']*100:+.2f}%  net "
             f"{m['net']*100:+.2f}%  vs. baseline {m['baseline']*100:+.2f}% "
             f"-> excess net {m['excess_net']*100:+.2f}%  "
             f"({m['n_stale']} of {m['n_symbols']} stale)")
    current = cohorts_mod.current_snapshot(c, panels)
    if current and current["gross"] is not None:
        print(f"  current (live, unfrozen): gross {current['gross']*100:+.2f}%"
             f"  net {current['net']*100:+.2f}%")


def cmd_cohort_delete(args) -> None:
    """Permanently remove one cohort. No undo — matches this project's
    existing watchlist/user-preset delete commands, which are also
    unconfirmed at the CLI layer (a confirmation prompt belongs to an
    interactive UI, not a scriptable command)."""
    from . import cohorts as cohorts_mod
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    removed = cohorts_mod.delete_cohort(universe_id, args.cohort_id)
    if not removed:
        sys.exit(f"No cohort {args.cohort_id!r} in universe {universe_id!r}")
    print(f"Deleted cohort {args.cohort_id}")


def cmd_cohort_perf(args) -> None:
    """The ROADMAP Item 17 performance panel for one cohort's window
    (entry_date -> --end, default latest bar) — same engine for forward
    and replay cohorts."""
    from . import cohort_perf, cohorts as cohorts_mod, data_ingest
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    prices = _load_prices(universe_id)
    panels = indicators.build_panels(prices)
    c = cohorts_mod.get_cohort(universe_id, args.cohort_id, panels,
                               config.liquidity_gate_cr(universe_id))
    if c is None:
        sys.exit(f"No cohort {args.cohort_id!r} in universe {universe_id!r}")

    perf = cohort_perf.evaluate_performance(
        c, panels, list(panels.keys()),
        config.liquidity_gate_cr(universe_id), end_date=args.end,
        benchmark=data_ingest.load_benchmark(universe_id))
    if perf is None:
        sys.exit("No evaluable window yet — cohort is pending, or --end "
                 "resolves before entry+1.")

    print(f"Cohort {c['cohort_id']}  window {perf['entry_date']} -> "
         f"{perf['end_date']}  ({perf['n_bars']} bars)\n")
    print(f"  cumulative: gross {perf['gross']*100:+.2f}%  net "
         f"{perf['net']*100:+.2f}%")
    print(f"  excess vs. baseline: gross {perf['excess_gross_baseline']*100:+.2f}%"
         f"  net {perf['excess_net_baseline']*100:+.2f}%")
    if perf["excess_net_nifty"] is not None:
        print(f"  excess vs. Nifty:    gross {perf['excess_gross_nifty']*100:+.2f}%"
             f"  net {perf['excess_net_nifty']*100:+.2f}%")
    print(f"  annualised vol: {perf['annualized_vol']*100:.2f}%" if
         perf["annualized_vol"] is not None else "  annualised vol: —")
    dd = perf["max_drawdown"]
    print(f"  max drawdown: {dd['pct']*100:.2f}%  "
         f"(peak {dd['peak_date']} -> trough {dd['trough_date']})")
    if perf["sharpe"] is not None:
        print(f"  sharpe: {perf['sharpe']:.2f}")
    else:
        print(f"  sharpe: — ({perf['sharpe_note']})")
    print(f"  hit rate positive: {perf['hit_rate_positive']*100:.0f}%  "
         f"vs. baseline: {perf['hit_rate_vs_baseline']*100:.0f}%")
    print("\n  contributors (weighted, best to worst):")
    for ctr in perf["contributors"]:
        print(f"    {ctr['symbol']:<12} weight {ctr['weight']*100:5.1f}%  "
             f"return {ctr['return_gross']*100:+7.2f}%  "
             f"contribution {ctr['contribution_gross']*100:+6.2f}%  "
             f"own max DD {ctr['max_drawdown_pct']*100:+.2f}%")


def cmd_scorecard(args) -> None:
    """Per-spec IS-vs-OOS scorecard (ROADMAP Item 16) — accepts either
    a raw spec_hash or a preset id (resolved to its spec_hash)."""
    from . import cohorts as cohorts_mod
    universe_id = getattr(args, "universe", universes.DEFAULT_UNIVERSE)
    spec_hash = args.spec_hash_or_preset
    try:
        from . import presets
        spec_hash = dsl.spec_hash(dsl.validate(presets.get(spec_hash)["spec"]))
    except KeyError:
        pass  # not a known preset id — treat the argument as a raw spec_hash

    prices = _load_prices(universe_id)
    panels = indicators.build_panels(prices)
    from .webapp import BACKTEST_LOG_FILE
    log_entries = []
    if BACKTEST_LOG_FILE.exists():
        log_entries = [json.loads(l) for l in
                       BACKTEST_LOG_FILE.read_text().strip().splitlines()
                       if l]
    sc = cohorts_mod.scorecard(
        universe_id, spec_hash, panels, config.liquidity_gate_cr(universe_id),
        backtest_log_entries=log_entries)

    print(f"Scorecard — spec_hash={spec_hash}  universe={universe_id}  "
         f"({sc['n_cohorts_total']} cohorts)\n")
    for h in ("5", "20", "60"):
        hs = sc["horizons"][h]
        if hs["insufficient"]:
            print(f"  {h}-bar OOS: insufficient sample "
                 f"({hs['n_names']} names, {hs['n_cohorts']} cohorts)")
        else:
            print(f"  {h}-bar OOS: mean excess net {hs['mean_excess_net']*100:+.2f}%"
                 f"  hit-rate {hs['hit_rate']*100:.0f}%  "
                 f"({hs['n_cohorts']} cohorts, {hs['n_names']} names)")
        is_h = (sc["in_sample"] or {}).get(h)
        if is_h and not is_h.get("insufficient", True):
            print(f"  {h}-bar IS:  mean excess net "
                 f"{is_h['excess_net']['mean']*100:+.2f}%")
    if sc["replay"]["n_cohorts"]:
        print(f"\n{sc['replay']['label']} ({sc['replay']['n_cohorts']} cohorts):")
        for h in ("5", "20", "60"):
            rh = sc["replay"]["horizons"][h]
            if rh["insufficient"]:
                print(f"  {h}-bar: insufficient sample "
                     f"({rh['n_names']} names, {rh['n_cohorts']} cohorts)")
            else:
                print(f"  {h}-bar: mean excess net {rh['mean_excess_net']*100:+.2f}%"
                     f"  hit-rate {rh['hit_rate']*100:.0f}%  "
                     f"({rh['n_cohorts']} cohorts, {rh['n_names']} names)")
    print(f"\n{sc['footnote']}")
    print(sc["survivorship_free_note"])


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

    ch = sub.add_parser("cohort", help="walk-forward out-of-sample "
                        "tracking for a screen's matches (ROADMAP Item 16)")
    ch_sub = ch.add_subparsers(dest="cohort_cmd", required=True)

    chc = ch_sub.add_parser("create",
                            help="freeze a cohort from the last logged "
                                 "screen, or an explicit spec + symbols")
    chc.add_argument("--from-last-screen", action="store_true",
                     help="use the most recent logged screen's spec + "
                          "matches for this universe (default if "
                          "--symbols is omitted)")
    chc.add_argument("--symbols", nargs="+",
                     help="explicit symbol list (needs --preset or --json "
                          "to supply the spec)")
    chc.add_argument("--preset", help="preset id supplying the spec")
    chc.add_argument("--json", action="store_true",
                     help="query is a raw DSL JSON spec")
    chc.add_argument("query", nargs="?", default="",
                     help="JSON spec (with --json)")
    chc.add_argument("--notes", default="")
    chc.add_argument("--as-of", dest="as_of", default=None,
                     help="freeze a REPLAY cohort as of this historical "
                          "date (ROADMAP Item 17) instead of a forward "
                          "one — in-sample by construction, excluded "
                          "from the OOS scorecard")
    _add_universe_arg(chc)
    chc.set_defaults(func=cmd_cohort_create)

    chl = ch_sub.add_parser("list", help="list this universe's cohorts")
    _add_universe_arg(chl)
    chl.set_defaults(func=cmd_cohort_list)

    chs = ch_sub.add_parser("show", help="one cohort's full detail")
    chs.add_argument("cohort_id")
    _add_universe_arg(chs)
    chs.set_defaults(func=cmd_cohort_show)

    chd = ch_sub.add_parser("delete", help="permanently remove one cohort")
    chd.add_argument("cohort_id")
    _add_universe_arg(chd)
    chd.set_defaults(func=cmd_cohort_delete)

    chp = ch_sub.add_parser("perf", help="performance panel for one "
                            "cohort's window (ROADMAP Item 17)")
    chp.add_argument("cohort_id")
    chp.add_argument("--end", default=None,
                     help="evaluate to this date instead of the latest "
                          "bar (default: latest bar)")
    _add_universe_arg(chp)
    chp.set_defaults(func=cmd_cohort_perf)

    scc = sub.add_parser("scorecard",
                         help="per-spec IS-vs-OOS scorecard (ROADMAP "
                              "Item 16) — backtest vs. tracked cohorts")
    scc.add_argument("spec_hash_or_preset",
                     help="a raw spec_hash, or a preset id (resolved "
                          "automatically)")
    _add_universe_arg(scc)
    scc.set_defaults(func=cmd_scorecard)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
