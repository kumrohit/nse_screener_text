"""Web backend.

    python -m screener.webapp          # http://127.0.0.1:8501

Endpoints
---------
GET  /             the single-page UI
GET  /api/status   data mode (live/demo), as-of date, universe size
GET  /api/health   cheap cron/uptime probe: store mtime + as-of, panel
                   count, benchmark presence, log writability, git version
POST /api/parse    {"query": str} -> {"spec", "english"} | 422 {"error"}
POST /api/screen   {"spec": dict} -> stats + matches with per-condition
                   evidence (and near-misses: stocks failing exactly one
                   condition, so the user sees the boundary of the filter)

Falls back to a synthetic 11-stock demo universe when no price store exists,
so the UI is explorable immediately after clone.
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

import os as _os
import threading

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import (allocate, backtest, cohort_perf, cohorts as cohorts_mod,
              config, dsl, evaluator, explain, panel_store, universes)

app = FastAPI(title="NSE Text Screener")

# Keyed by universe_id (ROADMAP Item 15 Phase A) — every reader goes
# through `_load_state(universe_id)`, so a second universe's cache
# entry is a pure addition, not a plumbing change.
_state: dict[str, dict] = {}
_lock = threading.Lock()

# Single active universe for the whole server process (ROADMAP Item 15
# Phase A) — this is a local, single-user tool, not a multi-tenant
# service, so "which universe is the UI currently showing" is one
# process-wide selection rather than a per-request field threaded
# through every endpoint's request body. `POST /api/universe` changes
# it; every endpoint below reads the panels/universe/benchmark for
# whichever universe is currently active.
_ACTIVE_UNIVERSE = universes.DEFAULT_UNIVERSE


def _demo_forced() -> bool:
    """SCREENER_FORCE_DEMO=1 boots demo mode regardless of a local price
    store — used by the visual-regression suite (web/visual) so its
    screenshots compare against deterministic synthetic data instead of
    a real store that drifts every trading day."""
    return _os.environ.get("SCREENER_FORCE_DEMO", "") not in ("", "0")


def _store_mtime(universe_id: str) -> float | None:
    """mtime of the file a long-running server must watch for changes.
    None in demo mode (nothing on disk to watch) — kept distinct from
    any real mtime so a demo->live transition is also detected."""
    store = config.price_store(universe_id)
    return (store.stat().st_mtime
           if store.exists() and not _demo_forced() else None)


def _load_state(universe_id: str = universes.DEFAULT_UNIVERSE) -> dict:
    """Cached, but self-invalidating: a long-running server used to load
    panels once at startup and never notice `python -m screener.cli
    update` writing a fresh prices.parquet overnight, silently screening
    yesterday's data forever. Now every call compares the store's mtime
    against what was loaded and rebuilds on any change. One cache entry
    per universe_id (see `_state`'s docstring above)."""
    with _lock:
        mtime = _store_mtime(universe_id)
        cached = _state.get(universe_id)
        if cached and cached.get("_mtime") == mtime:
            return cached
        entry: dict = {}
        store = config.price_store(universe_id)
        if store.exists() and not _demo_forced():
            from . import cross_section, data_ingest, universe as uni_mod
            prices = pd.read_parquet(store)
            latest = data_ingest.assert_fresh(prices)
            entry.update(
                mode="live",
                panels=panel_store.load_or_build(universe_id, prices=prices),
                universe=uni_mod.fetch_universe(universe_id=universe_id),
                benchmark=data_ingest.load_benchmark(universe_id),
                as_of=str(latest.date()),
                _mtime=mtime,
            )
            cross_section._CACHE.clear()  # keyed by id(panels) — a GC-reused
                                          # id could otherwise serve stale
                                          # ranks after a rebuild
        else:
            from . import demo
            panels, uni, bench = demo.build_demo()
            entry.update(mode="demo", panels=panels, universe=uni,
                         benchmark=bench,
                         as_of=str(panels["STEADY"].index[-1].date()),
                         _mtime=None)
        entry["universe_id"] = universe_id
        _state[universe_id] = entry
        return entry


class ParseIn(BaseModel):
    query: str


class ScreenIn(BaseModel):
    spec: dict


class ChartIn(BaseModel):
    symbol: str
    spec: dict


@app.get("/")
def index():
    return FileResponse(config.ROOT / "web" / "index.html")


@app.get("/app.css")
def app_css():
    """Served as a static file (ROADMAP Item 11 monolith split) rather
    than inlined in index.html — no build step, no framework, just
    three files instead of one."""
    return FileResponse(config.ROOT / "web" / "app.css",
                        media_type="text/css")


@app.get("/app.js")
def app_js():
    return FileResponse(config.ROOT / "web" / "app.js",
                        media_type="application/javascript")


@app.get("/api/status")
def status():
    st = _load_state(_ACTIVE_UNIVERSE)
    return {"mode": st["mode"], "as_of": st["as_of"],
            "universe_size": len(st["panels"]),
            "history_years": config.HISTORY_YEARS,
            "universe_id": st["universe_id"],
            "universe_name": universes.get(st["universe_id"]).name}


@app.get("/api/universes")
def universes_list():
    """Registered universes for the webapp's selector (ROADMAP Item 15
    Phase A) — `active` marks the one every other endpoint currently
    reads from."""
    return [{"id": u.id, "name": u.name, "active": u.id == _ACTIVE_UNIVERSE}
           for u in universes.UNIVERSES.values()]


class UniverseIn(BaseModel):
    id: str


@app.post("/api/universe")
def set_active_universe(body: UniverseIn):
    """Switches which universe every subsequent request reads from —
    a process-wide selection, not per-request (see `_ACTIVE_UNIVERSE`'s
    docstring above)."""
    global _ACTIVE_UNIVERSE
    if body.id not in universes.UNIVERSES:
        return JSONResponse(
            {"error": f"unknown universe {body.id!r}; must be one of "
                      f"{sorted(universes.UNIVERSES)}"},
            status_code=422)
    _ACTIVE_UNIVERSE = body.id
    st = _load_state(_ACTIVE_UNIVERSE)
    return {"active": _ACTIVE_UNIVERSE, "mode": st["mode"],
           "as_of": st["as_of"], "universe_size": len(st["panels"])}


def _git_version() -> str:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=config.ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — a health probe must never raise
        return "unknown"


@app.get("/api/health")
def health():
    """Cheap JSON for cron/uptime monitoring — the nightly pipeline
    curls this after `update` to confirm the server actually picked up
    the fresh store (the P0 stale-server fix, verified live)."""
    st = _load_state(_ACTIVE_UNIVERSE)
    mtime = st.get("_mtime")
    return {
        "mode": st["mode"],
        "as_of": st["as_of"],
        "store_mtime": (pd.Timestamp(mtime, unit="s").isoformat()
                        if mtime else None),
        "panel_count": len(st["panels"]),
        "benchmark_present": st["benchmark"] is not None,
        "log_writable": _os.access(config.DATA_DIR, _os.W_OK)
                       if config.DATA_DIR.exists() else False,
        "version": _git_version(),
        "config_hash": config.config_hash(),
    }


@app.get("/api/presets")
def presets_list():
    from . import presets
    return [{"id": p["id"], "name": p["name"], "group": p["group"],
             "description": p["description"], "spec": p["spec"],
             "english": dsl.describe(p["spec"]),
             "evidence": p.get("evidence"),
             "universes": p.get("universes")}
            for p in presets.PRESETS]


@app.post("/api/parse")
def parse(body: ParseIn):
    from . import parser
    try:
        spec, assumptions = parser.parse_with_assumptions(body.query)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except KeyError:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY not set — plain-English parsing "
                      "is unavailable. Use the JSON spec tab instead."},
            status_code=422)
    return {"spec": spec, "english": dsl.describe(spec),
            "assumptions": assumptions}


@app.post("/api/screen")
def screen(body: ScreenIn):
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return _run_screen(spec)


def _run_screen(spec: dict) -> dict:
    """Everything /api/screen does for one already-validated spec —
    factored out so /api/screen_batch (ROADMAP Item 5) can run several
    presets without duplicating the matching/diffing/logging logic."""
    st = _load_state(_ACTIVE_UNIVERSE)
    as_of = spec.get("as_of", "latest")
    logic = spec.get("logic", "AND")
    matches, near_misses = [], []
    evaluated = liquidity_excluded = 0

    spec_h = dsl.spec_hash(spec)
    prior_run = _find_prior_run(spec_h)

    sector_by_symbol, cross_section, breadth = evaluator._cross_sectional_context(
        spec, st["panels"], st["universe"], as_of)

    for sym, panel in st["panels"].items():
        i = evaluator._row_at(panel, as_of)
        if i is None or i < 0:
            continue
        med_turnover = panel["turnover_cr"].iloc[
            max(0, i - 19): i + 1].median()
        if (st["mode"] == "live"
                and pd.notna(med_turnover)
                and med_turnover < config.liquidity_gate_cr(
                    st["universe_id"])):
            liquidity_excluded += 1
            continue
        evaluated += 1
        evidence = explain.explain_symbol(
            panel, spec, as_of, benchmark=st["benchmark"], symbol=sym,
            sector_by_symbol=sector_by_symbol, cross_section=cross_section,
            breadth=breadth)
        n_passed = sum(e["passed"] for e in evidence)
        matched = (n_passed == len(evidence) if logic == "AND"
                   else n_passed > 0)
        if not matched and not (logic == "AND"
                                and n_passed == len(evidence) - 1):
            continue

        last = panel.iloc[i]
        row = {
            "symbol": sym,
            "conditions_passed": n_passed,
            "conditions_total": len(evidence),
            "evidence": evidence,
            "metrics": {
                "close": round(float(last["close"]), 2),
                "pct_vs_ema50": _r(100 * (last["close"] / last["ema_50"] - 1))
                    if pd.notna(last["ema_50"]) else None,
                "rsi": _r(last["rsi"], 1),
                "adx": _r(last["adx"], 1),
                "vol_ratio": _r(last["vol_ratio"]),
                "atr_pct": _r(last["atr_pct"]),
                "ret_1m_pct": _r(last["roc_21"], 1),
                "ret_3m_pct": _r(last["roc_63"], 1),
                "pct_from_52w_high": _r(last["pct_from_52w_high"], 1),
                "turnover_cr": _r(med_turnover, 1),
            },
        }
        uni = st["universe"]
        meta = uni.loc[uni["symbol"] == sym]
        row["name"] = meta["name"].iloc[0] if len(meta) else sym
        row["industry"] = (meta["industry"].iloc[0]
                           if len(meta) and pd.notna(meta["industry"].iloc[0])
                           else "—")
        row["spark"] = _spark(panel, spec, evidence, i)
        row["flags"] = _data_quality_flags(panel, i, st["as_of"])
        (matches if matched else near_misses).append(row)

    matches.sort(key=lambda r: (r["metrics"]["ret_3m_pct"] is None,
                                -(r["metrics"]["ret_3m_pct"] or 0)))
    near_misses.sort(key=lambda r: -r["conditions_passed"])

    diff = _compute_diff(prior_run, matches, st, spec, as_of,
                         sector_by_symbol, cross_section, breadth)

    _log_run(spec, st["as_of"] if as_of == "latest" else as_of,
             {"matched": len(matches), "evaluated": evaluated}, matches,
             spec_h, st["universe_id"])
    sector_warning = evaluator.sector_data_gap_warning(spec, st["universe"])
    return {
        "english": dsl.describe(spec),
        "spec": spec,
        "as_of": st["as_of"] if as_of == "latest" else as_of,
        "mode": st["mode"],
        "warnings": [sector_warning] if sector_warning else [],
        "stats": {"universe": len(st["panels"]),
                  "liquidity_excluded": liquidity_excluded,
                  "evaluated": evaluated,
                  "matched": len(matches),
                  "near_misses": len(near_misses)},
        "matches": matches[:MAX_MATCHES],
        "near_misses": near_misses[:15],
        "diff": diff,
        "methodology": {
            "data": ("Synthetic demo data — run `python -m screener.cli "
                     "backfill` for live Nifty 500 prices"
                     if st["mode"] == "demo" else
                     f"NSE daily bars via Yahoo Finance, split/bonus "
                     f"adjusted, {config.HISTORY_YEARS}y history"),
            "liquidity_gate": f"20-day median turnover ≥ "
                              f"₹{config.liquidity_gate_cr(st['universe_id'])} cr",
            "nan_policy": "any missing input ⇒ condition fails "
                          "(insufficient history never matches)",
            "config_hash": config.config_hash(),
        },
    }


class ScreenBatchIn(BaseModel):
    preset_ids: list[str]


@app.post("/api/screen_batch")
def screen_batch(body: ScreenBatchIn):
    """Run N presets (built-in or "user:<id>" saved screens) in one
    call — the morning-view dashboard (ROADMAP Item 5): preset x
    (match count, top-3 symbols, new-since-last-run), without opening
    each one individually."""
    from . import presets as presets_mod
    user_by_id = {u["id"]: u for u in _load_user_presets()}

    rows = []
    for pid in body.preset_ids:
        try:
            if pid.startswith("user:"):
                u = user_by_id.get(pid[len("user:"):])
                if u is None:
                    raise KeyError(pid)
                name, spec = u["name"], dsl.validate(u["spec"])
            else:
                p = presets_mod.get(pid)
                name, spec = p["name"], dsl.validate(p["spec"])
        except (KeyError, dsl.DSLValidationError) as exc:
            rows.append({"preset_id": pid, "name": pid, "error": str(exc)})
            continue

        result = _run_screen(spec)
        rows.append({
            "preset_id": pid,
            "name": name,
            "as_of": result["as_of"],
            "matched": result["stats"]["matched"],
            "top3": [m["symbol"] for m in result["matches"][:3]],
            "new_since_last_run": (len(result["diff"]["new"])
                                   if result["diff"] else None),
        })
    return {"rows": rows}


class AllocateIn(BaseModel):
    symbols: list[str]
    capital: float
    method: str = "risk"
    risk_pct: float = 1.0
    max_positions: int = allocate.DEFAULT_MAX_POSITIONS
    max_position_pct: float = allocate.DEFAULT_MAX_POSITION_PCT
    sector_cap_pct: float = allocate.DEFAULT_SECTOR_CAP_PCT
    min_ticket: float = allocate.DEFAULT_MIN_TICKET
    as_of: str | None = None
    spec: dict | None = None  # the screen that produced `symbols`, for the log


ALLOCATION_LOG_FILE = config.DATA_DIR / "allocation_log.jsonl"


def _log_allocation(body: "AllocateIn", result: dict) -> None:
    """Sibling to screen_log.jsonl (ROADMAP Item 10) — kept as its own
    file rather than interleaved into screen_log.jsonl because the
    schemas genuinely differ (a position table, not a matched-symbol
    list) and verify.check_screen_log's required-keys check would
    otherwise have to special-case allocation entries. Same
    replay-guarantee spirit: spec hash (if the originating screen was
    passed) + every sizing parameter + the resulting table."""
    import json as _json
    import datetime as _dt
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ALLOCATION_LOG_FILE, "a") as fh:
            fh.write(_json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "spec_hash": dsl.spec_hash(body.spec) if body.spec else None,
                "symbols": body.symbols, "capital": body.capital,
                "method": body.method, "risk_pct": body.risk_pct,
                "max_positions": body.max_positions,
                "max_position_pct": body.max_position_pct,
                "sector_cap_pct": body.sector_cap_pct,
                "min_ticket": body.min_ticket,
                "as_of": result.get("as_of"),
                "config_hash": config.config_hash(),
                "positions": result["positions"],
                "summary": result["summary"],
            }) + "\n")
    except OSError:
        pass  # logging must never break an allocation response


@app.post("/api/allocate")
def allocate_endpoint(body: AllocateIn):
    """Turns a ranked symbol list + capital + risk tolerance into
    integer-share position sizes (ROADMAP Item 10) — a sizing
    calculator with documented methodology, not a recommendation
    engine. See allocate.py's module docstring and
    TECHNICAL_DESIGN.md §12d for the explicit non-goals (no MVO, no
    Kelly, no return forecasts, no auto-execution)."""
    st = _load_state(_ACTIVE_UNIVERSE)
    as_of = body.as_of or st["as_of"]
    try:
        result = allocate.allocate(
            body.symbols, st["panels"], st["universe"], body.capital,
            method=body.method, risk_pct=body.risk_pct,
            max_positions=body.max_positions,
            max_position_pct=body.max_position_pct,
            sector_cap_pct=body.sector_cap_pct,
            min_ticket=body.min_ticket, as_of=as_of)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    result["as_of"] = as_of
    result["disclaimer"] = (
        "This is a position-sizing calculator, not investment advice. "
        "It has no view on which stocks to buy — only on how much of "
        "each, given the capital, risk tolerance, and constraints you "
        "specified.")
    _log_allocation(body, result)
    return result


class BacktestIn(BaseModel):
    spec: dict
    horizons: list[int] = list(backtest.DEFAULT_HORIZONS)
    cooldown: int = backtest.DEFAULT_COOLDOWN
    cost_pct: float = backtest.DEFAULT_COST_PCT
    min_turnover_cr: float = config.MIN_MEDIAN_TURNOVER_CR
    stride: int = backtest.DEFAULT_STRIDE
    min_events: int = backtest.MIN_EVENTS
    hypothesis: str | None = None
    sensitivity: bool = True


BACKTEST_LOG_FILE = config.DATA_DIR / "backtest_log.jsonl"

# Schema versioning (ROADMAP Item 18 v1.0 hardening). 1: pre-Item-16
# entries (no `universe` field — unlike screen_log's equivalent gap,
# nothing ever defaulted this one on read; cohorts.scorecard()'s IS
# lookup just compared `e.get("universe")` directly, so a pre-Item-16
# entry silently matched nothing rather than being treated as
# nifty500. Same fix as screen_log's migration, applied here too.
BACKTEST_LOG_SCHEMA_VERSION = 2


def migrate_backtest_log_entry(e: dict) -> dict:
    version = e.get("schema_version", 1)
    if version < 2:
        e.setdefault("universe", universes.DEFAULT_UNIVERSE)
    e["schema_version"] = BACKTEST_LOG_SCHEMA_VERSION
    return e


def _log_backtest(body: "BacktestIn", result: dict,
                  universe_id: str = universes.DEFAULT_UNIVERSE) -> None:
    """Same replay-guarantee spirit as screen_log.jsonl/allocation_log.jsonl
    (ROADMAP Items 5/10): spec hash + every run parameter + hypothesis +
    the per-horizon summary (not the full per-event table — that alone
    can be thousands of rows; the summary is enough to see what a run
    concluded, and screen_log already has the spec's match history).
    `universe_id` (ROADMAP Item 16) matters here specifically: the
    cohort scorecard looks up the most recent backtest for a spec_hash
    as its in-sample comparison, and the same spec_hash can legitimately
    run on more than one universe — without this field that lookup
    could silently pair an nse_full cohort with a nifty500 backtest."""
    import json as _json
    import datetime as _dt
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(BACKTEST_LOG_FILE, "a") as fh:
            fh.write(_json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "spec_hash": result["spec_hash"], "english": result["english"],
                "universe": universe_id,
                "hypothesis": body.hypothesis, "cooldown": body.cooldown,
                "cost_pct": body.cost_pct,
                "min_turnover_cr": body.min_turnover_cr,
                "stride": body.stride,
                "n_symbols": result["n_symbols"],
                "n_events_total": result["n_events_total"],
                "horizons": result["horizons"],
                "config_hash": config.config_hash(),
                "schema_version": BACKTEST_LOG_SCHEMA_VERSION,
            }) + "\n")
    except OSError:
        pass  # logging must never break a backtest response


@app.post("/api/backtest")
def backtest_endpoint(body: BacktestIn):
    """Event-study backtest for a DSL spec (ROADMAP Item 14) — an edge
    detector for filters, not a portfolio simulator. See
    screener/backtest.py's module docstring and TECHNICAL_DESIGN.md
    for the locked methodology (cooldown dedup, entry at open[t+1],
    same-date universe baseline, gross/net costs, block-bootstrap
    stats, <30-event suppression, one-at-a-time sensitivity grid,
    survivorship caveat on every response)."""
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if not body.horizons:
        return JSONResponse({"error": "horizons must be non-empty"},
                           status_code=422)

    st = _load_state(_ACTIVE_UNIVERSE)
    result = backtest.backtest_spec(
        st["panels"], st["universe"], spec,
        horizons=tuple(body.horizons), cooldown=body.cooldown,
        cost_pct=body.cost_pct, min_turnover_cr=body.min_turnover_cr,
        stride=body.stride, min_events=body.min_events,
        hypothesis=body.hypothesis, benchmark=st["benchmark"],
        sensitivity=body.sensitivity,
        survivorship_note=universes.get(st["universe_id"]).survivorship_note)
    result["horizons"] = {str(h): v for h, v in result["horizons"].items()}
    result["mode"] = st["mode"]
    sector_warning = evaluator.sector_data_gap_warning(spec, st["universe"])
    result["warnings"] = [sector_warning] if sector_warning else []
    _log_backtest(body, result, st["universe_id"])
    return result


class CohortCreateIn(BaseModel):
    spec: dict
    symbols: list[str] | None = None    # "track these matches" (equal weight)
    positions: list[dict] | None = None  # "track this portfolio" (allocate()
                                         # positions — each needs symbol+value)
    method: str = "equal"                # display label; ignored if symbols given
    notes: str = ""
    as_of: str | None = None            # ROADMAP Item 17: replay cohort if
                                         # given; mode is derived server-side
                                         # from this alone — deliberately no
                                         # `mode` field on this model, so a
                                         # caller cannot pass one to override it


def _attach_current(cohort: dict, panels: dict) -> dict:
    """A response-shaping helper, not a stored field: `current` is
    recomputed fresh on every read (never frozen, never persisted) —
    see cohorts.current_snapshot's docstring. `survivorship_note`
    (ROADMAP Item 17) is attached the same way for replay cohorts —
    static given `mode`, so no reason to store it on every record."""
    out = dict(cohort)
    out["current"] = cohorts_mod.current_snapshot(cohort, panels)
    if cohort["mode"] == cohorts_mod.MODE_REPLAY:
        out["survivorship_note"] = cohorts_mod.REPLAY_SURVIVORSHIP_NOTE
    return out


@app.post("/api/cohorts")
def create_cohort_endpoint(body: CohortCreateIn):
    """Freeze a cohort of matches (or a sized allocation) for
    walk-forward out-of-sample tracking (ROADMAP Item 16) — the
    complement to the backtester's in-sample event study. See
    screener/cohorts.py's module docstring for the full methodology
    (entry convention, milestone freezing, survivorship-free-by-
    construction, IS-vs-OOS scorecard)."""
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if bool(body.symbols) == bool(body.positions):
        return JSONResponse(
            {"error": "provide exactly one of symbols or positions"},
            status_code=422)
    try:
        if body.positions:
            weights = cohorts_mod.weights_from_positions(
                body.positions, body.method)
            symbols = [p["symbol"] for p in body.positions]
        else:
            weights = cohorts_mod.weights_from_symbols(body.symbols)
            symbols = body.symbols
        st = _load_state(_ACTIVE_UNIVERSE)
        cohort = cohorts_mod.create_cohort(
            universe_id=_ACTIVE_UNIVERSE, spec=spec, symbols=symbols,
            weights=weights, notes=body.notes, as_of=body.as_of,
            panels=st["panels"])
    except (ValueError, dsl.DSLValidationError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return cohort


@app.get("/api/cohorts")
def list_cohorts_endpoint(spec_hash: str | None = None):
    st = _load_state(_ACTIVE_UNIVERSE)
    cohorts_list = cohorts_mod.list_cohorts(
        _ACTIVE_UNIVERSE, st["panels"],
        config.liquidity_gate_cr(_ACTIVE_UNIVERSE), spec_hash=spec_hash)
    return [_attach_current(c, st["panels"]) for c in cohorts_list]


@app.get("/api/cohorts/{cohort_id}")
def get_cohort_endpoint(cohort_id: str):
    st = _load_state(_ACTIVE_UNIVERSE)
    cohort = cohorts_mod.get_cohort(
        _ACTIVE_UNIVERSE, cohort_id, st["panels"],
        config.liquidity_gate_cr(_ACTIVE_UNIVERSE))
    if cohort is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _attach_current(cohort, st["panels"])


@app.delete("/api/cohorts/{cohort_id}")
def delete_cohort_endpoint(cohort_id: str, reason: str | None = None):
    """Two-tier removal (see cohorts.delete_cohort): replay/pending
    cohorts hard-delete; forward cohorts past entry require `reason`
    and are tombstoned — hidden but counted by the scorecard, so the
    OOS track record can't be quietly curated. Confirmation UX lives
    in the UI, which asks (and collects the reason) before calling."""
    result = cohorts_mod.delete_cohort(_ACTIVE_UNIVERSE, cohort_id,
                                       reason=reason)
    if result["error"] and not result["removed"]:
        return JSONResponse(result, status_code=400)
    return result


@app.get("/api/scorecard/{spec_hash}")
def scorecard_endpoint(spec_hash: str):
    st = _load_state(_ACTIVE_UNIVERSE)
    log_entries = []
    if BACKTEST_LOG_FILE.exists():
        import json as _json
        log_entries = [migrate_backtest_log_entry(_json.loads(l)) for l in
                       BACKTEST_LOG_FILE.read_text().strip().splitlines()
                       if l]
    return cohorts_mod.scorecard(
        _ACTIVE_UNIVERSE, spec_hash, st["panels"],
        config.liquidity_gate_cr(_ACTIVE_UNIVERSE),
        backtest_log_entries=log_entries)


@app.get("/api/cohorts/{cohort_id}/performance")
def cohort_performance_endpoint(cohort_id: str, end: str | None = None):
    """The ROADMAP Item 17 performance panel — cumulative return, excess
    vs. baseline/Nifty, vol, max drawdown, hit rates, contributors, and
    an equity curve for one cohort's window (entry_date -> `end`,
    default latest bar). Same engine for forward and replay cohorts;
    `end` lets a forward cohort be evaluated to an earlier point too,
    not just replay ones."""
    st = _load_state(_ACTIVE_UNIVERSE)
    cohort = cohorts_mod.get_cohort(
        _ACTIVE_UNIVERSE, cohort_id, st["panels"],
        config.liquidity_gate_cr(_ACTIVE_UNIVERSE))
    if cohort is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    perf = cohort_perf.evaluate_performance(
        cohort, st["panels"], list(st["panels"].keys()),
        config.liquidity_gate_cr(_ACTIVE_UNIVERSE), end_date=end,
        benchmark=st.get("benchmark"))
    if perf is None:
        return JSONResponse(
            {"error": "no evaluable window yet (cohort pending, or end "
                      "resolves before entry+1)"}, status_code=422)
    return perf


@app.post("/api/chart")
def chart(body: ChartIn):
    """Full modal chart data for one symbol (ROADMAP Item 5) — lazily
    fetched on click rather than embedded in every match, since 250
    bars of OHLCV per row would bloat the main /api/screen payload."""
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    st = _load_state(_ACTIVE_UNIVERSE)
    panel = st["panels"].get(body.symbol)
    if panel is None:
        return JSONResponse(
            {"error": f"unknown symbol {body.symbol!r}"}, status_code=404)

    as_of = spec.get("as_of", "latest")
    i = evaluator._row_at(panel, as_of)
    if i is None:
        return JSONResponse(
            {"error": "no data as of this date"}, status_code=422)

    sector_by_symbol, cross_section, breadth = evaluator._cross_sectional_context(
        spec, st["panels"], st["universe"], as_of)
    evidence = explain.explain_symbol(
        panel, spec, as_of, benchmark=st["benchmark"], symbol=body.symbol,
        sector_by_symbol=sector_by_symbol, cross_section=cross_section,
        breadth=breadth)
    return _chart_payload(panel, spec, evidence, i)


def _r(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


_PLOTTABLE = {"ema_10", "ema_20", "ema_50", "ema_100", "ema_200",
              "sma_20", "sma_50", "sma_150", "sma_200", "bb_upper",
              "bb_lower", "high_52w", "low_52w"}
_LEVEL_KEYS = ("support", "resistance", "level")
# Overridable via data/config_local.toml (ROADMAP Item 6) — aliased here
# since they're referenced throughout this module.
SPARK_BARS = config.SPARK_BARS
# Cap how many match cards the payload carries — stats.matched still
# reports the true total, so a loose filter's size is never hidden,
# just not rendered as hundreds of DOM cards.
MAX_MATCHES = config.MAX_MATCHES


def _referenced_fields(spec: dict) -> set[str]:
    """Every plottable field a spec's conditions reference — shared by
    the inline spark and the full chart modal, so both overlay exactly
    what the conditions looked at."""
    fields: set[str] = set()
    for c in spec["conditions"]:
        if c.get("timeframe") == "weekly":
            continue  # daily chart; weekly overlays would mislead
        for k in ("left", "right", "ref", "ma", "fast", "slow", "field"):
            v = c.get(k)
            if isinstance(v, str) and v in _PLOTTABLE:
                fields.add(v)
    return fields


def _evidence_levels(evidence: list[dict]) -> dict:
    levels = {}
    for e in evidence:
        for k in _LEVEL_KEYS:
            if e["values"].get(k) is not None:
                levels[k] = e["values"][k]
    return levels


def _spark(panel: pd.DataFrame, spec: dict, evidence: list[dict],
           i: int) -> dict:
    """Last SPARK_BARS bars up to the as-of row, plus every series the
    spec references and every horizontal level the evidence produced —
    so the mini-chart shows exactly what the conditions looked at."""
    lo = max(0, i - SPARK_BARS + 1)
    win = panel.iloc[lo: i + 1]
    fields = _referenced_fields(spec)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in win.index],
        "close": [_r(v) for v in win["close"]],
        "low": [_r(v) for v in win["low"]],
        "high": [_r(v) for v in win["high"]],
        "series": {f: [_r(v) for v in win[f]] for f in sorted(fields)},
        "levels": _evidence_levels(evidence),
    }


CHART_BARS = 250


def _chart_payload(panel: pd.DataFrame, spec: dict, evidence: list[dict],
                   i: int) -> dict:
    """Full modal chart data (ROADMAP Item 5): CHART_BARS bars with
    open/high/low/close/volume for candlesticks, the same
    spec-referenced overlays and evidence levels as the inline spark,
    just a bigger window."""
    lo = max(0, i - CHART_BARS + 1)
    win = panel.iloc[lo: i + 1]
    fields = _referenced_fields(spec)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in win.index],
        "open": [_r(v) for v in win["open"]],
        "high": [_r(v) for v in win["high"]],
        "low": [_r(v) for v in win["low"]],
        "close": [_r(v) for v in win["close"]],
        "volume": [_r(v, 0) for v in win["volume"]],
        "series": {f: [_r(v) for v in win[f]] for f in sorted(fields)},
        "levels": _evidence_levels(evidence),
    }


MIN_RELIABLE_BARS = 250  # below this, long-lookback indicators (EMA200,
                        # 52-week high/low) are built on insufficient history


def _data_quality_flags(panel: pd.DataFrame, i: int, store_as_of: str
                        ) -> list[dict]:
    """Per-symbol data-quality flags surfaced directly on match cards —
    the same signals `verify`'s adjustment smell test and coverage
    checks look for, but scoped to one symbol and shown where a user is
    actually looking, not buried in a separate CLI report."""
    flags = []
    win = panel.iloc[max(0, i - SPARK_BARS + 1): i + 1]
    jumps = win["close"].pct_change().abs()
    if (jumps > 0.40).any():
        d = jumps.idxmax()
        flags.append({"code": "jump", "reason":
                     f"single-day move >40% on {d.date()} within the "
                     "chart window — possible unadjusted split/demerger; "
                     "levels may straddle a gap"})
    if i + 1 < MIN_RELIABLE_BARS:
        flags.append({"code": "thin_history", "reason":
                     f"only {i + 1} bars of history as of this date — "
                     "long-lookback indicators (EMA200, 52-week high/low) "
                     "may be unreliable"})
    last_bar = panel.index[-1]
    if store_as_of and last_bar < pd.Timestamp(store_as_of):
        flags.append({"code": "stale", "reason":
                     f"last available bar is {last_bar.date()}, before "
                     f"the store's latest ({store_as_of}) — possibly "
                     "suspended or delisted"})
    return flags


def _compute_diff(prior_run: dict | None, matches: list, st: dict,
                  spec: dict, as_of: str, sector_by_symbol, cross_section,
                  breadth=None) -> dict | None:
    """"What changed since last run" (ROADMAP Item 5): symbols new to
    the match set, and symbols that dropped out — re-explained against
    *current* data so the UI can show exactly which condition now
    fails, not just that the symbol vanished."""
    if prior_run is None:
        return None
    prior_matched = set(prior_run.get("matched", []))
    current_matched = {m["symbol"] for m in matches}
    new_syms = sorted(current_matched - prior_matched)
    dropped_syms = sorted(prior_matched - current_matched)

    dropped_detail = []
    for sym in dropped_syms:
        panel = st["panels"].get(sym)
        if panel is None:
            dropped_detail.append({"symbol": sym,
                                   "reason": "no longer in the universe"})
            continue
        i = evaluator._row_at(panel, as_of)
        if i is None:
            dropped_detail.append({"symbol": sym,
                                   "reason": "no data as of this date"})
            continue
        ev = explain.explain_symbol(
            panel, spec, as_of, benchmark=st["benchmark"], symbol=sym,
            sector_by_symbol=sector_by_symbol, cross_section=cross_section,
            breadth=breadth)
        failing = [e["description"] for e in ev if not e["passed"]]
        dropped_detail.append({
            "symbol": sym,
            "reason": (f"now fails: {', '.join(failing)}" if failing
                      else "no longer evaluated (e.g. liquidity gate)"),
        })

    return {"prior_run_ts": prior_run.get("ts"), "new": new_syms,
           "dropped": dropped_detail}


LOG_FILE = config.DATA_DIR / "screen_log.jsonl"
ROTATED_LOG_FILE = config.DATA_DIR / "screen_log.rotated.jsonl"
MAX_LOG_LINES = 5000

# Schema versioning (ROADMAP Item 18 v1.0 hardening). 1: pre-Item-15
# entries (no `universe` field). The next format change is a new
# `if version < N` block here, not a new .setdefault() at another
# read site — screen_log is read from webapp's own /api/log endpoint
# and from cli.py's verify/log/cohort-create commands, and before this
# only the webapp endpoint actually defaulted the field; cli.py's own
# `--from-last-screen` path compared `e.get("universe")` directly,
# which silently treated a pre-Item-15 entry as belonging to no
# universe rather than nifty500 — a real, if minor, inconsistency this
# migration function closes by being the one place both callers use.
SCREEN_LOG_SCHEMA_VERSION = 2


def migrate_screen_log_entry(e: dict) -> dict:
    version = e.get("schema_version", 1)
    if version < 2:
        e.setdefault("universe", universes.DEFAULT_UNIVERSE)
    e["schema_version"] = SCREEN_LOG_SCHEMA_VERSION
    return e


def _rotate_log_if_needed() -> None:
    """Size-capped rotation: once the active log exceeds MAX_LOG_LINES,
    the oldest entries move into the rotated archive so a long-running
    server's log can't grow forever, without discarding history
    outright — `verify` checks both files."""
    if not LOG_FILE.exists():
        return
    lines = LOG_FILE.read_text().splitlines()
    if len(lines) <= MAX_LOG_LINES:
        return
    overflow = len(lines) - MAX_LOG_LINES
    old, keep = lines[:overflow], lines[overflow:]
    with open(ROTATED_LOG_FILE, "a") as fh:
        fh.write("\n".join(old) + "\n")
    LOG_FILE.write_text("\n".join(keep) + "\n")


def _find_prior_run(spec_h: str) -> dict | None:
    """Most recent screen_log entry with the same spec_hash — i.e. the
    same screen criteria run before, regardless of as_of — used for
    screen diffing ("what changed since last run", ROADMAP Item 5).
    Only checks the active log; missing a diff across a rotation
    boundary is an acceptable trade-off for a feature about "since
    yesterday", not deep history."""
    import json as _json
    if not LOG_FILE.exists():
        return None
    for line in reversed(LOG_FILE.read_text().strip().splitlines()):
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if entry.get("spec_hash") == spec_h:
            return entry
    return None


def _log_run(spec: dict, as_of: str, stats: dict, matches: list,
             spec_h: str,
             universe_id: str = universes.DEFAULT_UNIVERSE) -> None:
    """Append-only replay trail: spec + data date fully determine results.
    `universe_id` (ROADMAP Item 15 Phase A) — every entry from here on
    records which universe it ran against; entries written before this
    field existed are defaulted to nifty500 on read, not backfilled."""
    import json as _json
    import datetime as _dt
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(_json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "as_of": as_of, "spec": spec, "english": dsl.describe(spec),
                "stats": stats, "config_hash": config.config_hash(),
                "spec_hash": spec_h, "universe": universe_id,
                "matched": [m["symbol"] for m in matches],
                "schema_version": SCREEN_LOG_SCHEMA_VERSION,
            }) + "\n")
        _rotate_log_if_needed()
    except OSError:
        pass  # logging must never break a screen


@app.get("/api/log")
def screen_log(limit: int = 20):
    import json as _json
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text().strip().splitlines()[-limit:]
    return [migrate_screen_log_entry(_json.loads(l)) for l in reversed(lines)]


# ---------------------------------------------------------------- watchlist
# ROADMAP Item 5: star a match, track whether the tagged setup still holds.
WATCHLIST_FILE = config.DATA_DIR / "watchlist.jsonl"


class WatchlistIn(BaseModel):
    symbol: str
    spec: dict


@app.post("/api/watchlist")
def watchlist_add(body: WatchlistIn):
    import json as _json
    import datetime as _dt
    import uuid as _uuid
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    st = _load_state(_ACTIVE_UNIVERSE)
    panel = st["panels"].get(body.symbol)
    if panel is None:
        return JSONResponse(
            {"error": f"unknown symbol {body.symbol!r}"}, status_code=404)
    as_of = spec.get("as_of", "latest")
    i = evaluator._row_at(panel, as_of)
    if i is None:
        return JSONResponse(
            {"error": "no data as of this date"}, status_code=422)

    entry = {
        "id": _uuid.uuid4().hex[:12],
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "symbol": body.symbol,
        "tagged_date": str(panel.index[i].date()),
        "spec": spec,
        "spec_hash": dsl.spec_hash(spec),
        "close_at_tag": round(float(panel["close"].iloc[i]), 2),
    }
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WATCHLIST_FILE, "a") as fh:
            fh.write(_json.dumps(entry) + "\n")
    except OSError:
        return JSONResponse(
            {"error": "could not write watchlist"}, status_code=500)
    return entry


@app.get("/api/watchlist")
def watchlist_list():
    """Every tagged entry, re-evaluated against *today's* data: current
    close, % move since tag, and whether the originally-tagged spec
    still holds — signal-decay tracking, not just a static bookmark."""
    import json as _json
    if not WATCHLIST_FILE.exists():
        return []
    st = _load_state(_ACTIVE_UNIVERSE)
    out = []
    for line in WATCHLIST_FILE.read_text().strip().splitlines():
        if not line:
            continue
        entry = _json.loads(line)
        row = dict(entry)
        panel = st["panels"].get(entry["symbol"])
        if panel is None:
            row.update(current_close=None, move_pct=None, still_holds=None)
            out.append(row)
            continue
        i = evaluator._row_at(panel, "latest")
        current_close = float(panel["close"].iloc[i])
        fresh_spec = {**entry["spec"], "as_of": "latest"}
        sector_by_symbol, cross_section, breadth = evaluator._cross_sectional_context(
            fresh_spec, st["panels"], st["universe"], "latest")
        still_holds = evaluator.evaluate_symbol(
            panel, fresh_spec, "latest", benchmark=st["benchmark"],
            symbol=entry["symbol"], sector_by_symbol=sector_by_symbol,
            cross_section=cross_section, breadth=breadth)
        row.update(
            current_close=round(current_close, 2),
            move_pct=round(100 * (current_close / entry["close_at_tag"]
                                  - 1), 2),
            still_holds=bool(still_holds),
        )
        out.append(row)
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


@app.delete("/api/watchlist/{item_id}")
def watchlist_remove(item_id: str):
    import json as _json
    if not WATCHLIST_FILE.exists():
        return {"removed": False}
    lines = WATCHLIST_FILE.read_text().strip().splitlines()
    kept = [ln for ln in lines if _json.loads(ln).get("id") != item_id]
    removed = len(kept) != len(lines)
    WATCHLIST_FILE.write_text("\n".join(kept) + ("\n" if kept else ""))
    return {"removed": removed}


# ---------------------------------------------------------------- saved custom screens
# ROADMAP Item 5: user-authored specs, validated identically to the
# built-in preset library (rejected on save, not discovered on run).
USER_PRESETS_FILE = config.DATA_DIR / "user_presets.json"


def _load_user_presets() -> list[dict]:
    import json as _json
    if not USER_PRESETS_FILE.exists():
        return []
    try:
        return _json.loads(USER_PRESETS_FILE.read_text())
    except _json.JSONDecodeError:
        return []


def _save_user_presets(items: list[dict]) -> None:
    import json as _json
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_PRESETS_FILE.write_text(_json.dumps(items, indent=2))


class UserPresetIn(BaseModel):
    name: str
    notes: str = ""
    spec: dict


class UserPresetUpdateIn(BaseModel):
    name: str | None = None
    notes: str | None = None


@app.get("/api/user_presets")
def user_presets_list():
    return _load_user_presets()


@app.post("/api/user_presets")
def user_presets_add(body: UserPresetIn):
    try:
        spec = dsl.validate(body.spec)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    import datetime as _dt
    import uuid as _uuid
    items = _load_user_presets()
    entry = {
        "id": _uuid.uuid4().hex[:12],
        "name": body.name,
        "notes": body.notes,
        "spec": spec,
        "english": dsl.describe(spec),
        "created_ts": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    items.append(entry)
    _save_user_presets(items)
    return entry


@app.put("/api/user_presets/{item_id}")
def user_presets_update(item_id: str, body: UserPresetUpdateIn):
    items = _load_user_presets()
    for it in items:
        if it["id"] == item_id:
            if body.name is not None:
                it["name"] = body.name
            if body.notes is not None:
                it["notes"] = body.notes
            _save_user_presets(items)
            return it
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/user_presets/{item_id}")
def user_presets_remove(item_id: str):
    items = _load_user_presets()
    kept = [it for it in items if it["id"] != item_id]
    removed = len(kept) != len(items)
    _save_user_presets(kept)
    return {"removed": removed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
