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

from . import config, dsl, evaluator, explain, indicators

app = FastAPI(title="NSE Text Screener")

_state: dict = {}
_lock = threading.Lock()


def _store_mtime() -> float | None:
    """mtime of the file a long-running server must watch for changes.
    None in demo mode (nothing on disk to watch) — kept distinct from
    any real mtime so a demo->live transition is also detected."""
    return (config.PRICE_STORE.stat().st_mtime
           if config.PRICE_STORE.exists() else None)


def _load_state() -> dict:
    """Cached, but self-invalidating: a long-running server used to load
    panels once at startup and never notice `python -m screener.cli
    update` writing a fresh prices.parquet overnight, silently screening
    yesterday's data forever. Now every call compares the store's mtime
    against what was loaded and rebuilds on any change."""
    with _lock:
        mtime = _store_mtime()
        if _state and _state.get("_mtime") == mtime:
            return _state
        _state.clear()
        if config.PRICE_STORE.exists():
            from . import cross_section, data_ingest, universe as uni_mod
            prices = pd.read_parquet(config.PRICE_STORE)
            latest = data_ingest.assert_fresh(prices)
            _state.update(
                mode="live",
                panels=indicators.build_panels(prices),
                universe=uni_mod.fetch_universe(),
                benchmark=data_ingest.load_benchmark(),
                as_of=str(latest.date()),
                _mtime=mtime,
            )
            cross_section._CACHE.clear()  # keyed by id(panels) — a GC-reused
                                          # id could otherwise serve stale
                                          # ranks after a rebuild
        else:
            from . import demo
            panels, uni, bench = demo.build_demo()
            _state.update(mode="demo", panels=panels, universe=uni,
                          benchmark=bench,
                          as_of=str(panels["STEADY"].index[-1].date()),
                          _mtime=None)
        return _state


class ParseIn(BaseModel):
    query: str


class ScreenIn(BaseModel):
    spec: dict


@app.get("/")
def index():
    return FileResponse(config.ROOT / "web" / "index.html")


@app.get("/api/status")
def status():
    st = _load_state()
    return {"mode": st["mode"], "as_of": st["as_of"],
            "universe_size": len(st["panels"]),
            "history_years": config.HISTORY_YEARS}


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
    st = _load_state()
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
             "english": dsl.describe(p["spec"])}
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

    st = _load_state()
    as_of = spec.get("as_of", "latest")
    logic = spec.get("logic", "AND")
    matches, near_misses = [], []
    evaluated = liquidity_excluded = 0

    sector_by_symbol, cross_section = evaluator._cross_sectional_context(
        spec, st["panels"], st["universe"], as_of)

    for sym, panel in st["panels"].items():
        i = evaluator._row_at(panel, as_of)
        if i is None or i < 0:
            continue
        med_turnover = panel["turnover_cr"].iloc[
            max(0, i - 19): i + 1].median()
        if (st["mode"] == "live"
                and pd.notna(med_turnover)
                and med_turnover < config.MIN_MEDIAN_TURNOVER_CR):
            liquidity_excluded += 1
            continue
        evaluated += 1
        evidence = explain.explain_symbol(
            panel, spec, as_of, benchmark=st["benchmark"], symbol=sym,
            sector_by_symbol=sector_by_symbol, cross_section=cross_section)
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
    _log_run(spec, st["as_of"] if as_of == "latest" else as_of,
             {"matched": len(matches), "evaluated": evaluated}, matches)
    return {
        "english": dsl.describe(spec),
        "spec": spec,
        "as_of": st["as_of"] if as_of == "latest" else as_of,
        "mode": st["mode"],
        "stats": {"universe": len(st["panels"]),
                  "liquidity_excluded": liquidity_excluded,
                  "evaluated": evaluated,
                  "matched": len(matches),
                  "near_misses": len(near_misses)},
        "matches": matches[:MAX_MATCHES],
        "near_misses": near_misses[:15],
        "methodology": {
            "data": ("Synthetic demo data — run `python -m screener.cli "
                     "backfill` for live Nifty 500 prices"
                     if st["mode"] == "demo" else
                     f"NSE daily bars via Yahoo Finance, split/bonus "
                     f"adjusted, {config.HISTORY_YEARS}y history"),
            "liquidity_gate": f"20-day median turnover ≥ "
                              f"₹{config.MIN_MEDIAN_TURNOVER_CR} cr",
            "nan_policy": "any missing input ⇒ condition fails "
                          "(insufficient history never matches)",
            "config_hash": config.config_hash(),
        },
    }


def _r(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


_PLOTTABLE = {"ema_10", "ema_20", "ema_50", "ema_100", "ema_200",
              "sma_20", "sma_50", "sma_200", "bb_upper", "bb_lower",
              "high_52w", "low_52w"}
_LEVEL_KEYS = ("support", "resistance", "level")
# Overridable via data/config_local.toml (ROADMAP Item 6) — aliased here
# since they're referenced throughout this module.
SPARK_BARS = config.SPARK_BARS
# Cap how many match cards the payload carries — stats.matched still
# reports the true total, so a loose filter's size is never hidden,
# just not rendered as hundreds of DOM cards.
MAX_MATCHES = config.MAX_MATCHES


def _spark(panel: pd.DataFrame, spec: dict, evidence: list[dict],
           i: int) -> dict:
    """Last SPARK_BARS bars up to the as-of row, plus every series the
    spec references and every horizontal level the evidence produced —
    so the mini-chart shows exactly what the conditions looked at."""
    lo = max(0, i - SPARK_BARS + 1)
    win = panel.iloc[lo: i + 1]
    fields: set[str] = set()
    for c in spec["conditions"]:
        if c.get("timeframe") == "weekly":
            continue  # daily chart; weekly overlays would mislead
        for k in ("left", "right", "ref", "ma", "fast", "slow", "field"):
            v = c.get(k)
            if isinstance(v, str) and v in _PLOTTABLE:
                fields.add(v)
    levels = {}
    for e in evidence:
        for k in _LEVEL_KEYS:
            if e["values"].get(k) is not None:
                levels[k] = e["values"][k]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in win.index],
        "close": [_r(v) for v in win["close"]],
        "low": [_r(v) for v in win["low"]],
        "high": [_r(v) for v in win["high"]],
        "series": {f: [_r(v) for v in win[f]] for f in sorted(fields)},
        "levels": levels,
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


LOG_FILE = config.DATA_DIR / "screen_log.jsonl"
ROTATED_LOG_FILE = config.DATA_DIR / "screen_log.rotated.jsonl"
MAX_LOG_LINES = 5000


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


def _log_run(spec: dict, as_of: str, stats: dict, matches: list) -> None:
    """Append-only replay trail: spec + data date fully determine results."""
    import json as _json
    import datetime as _dt
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(_json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "as_of": as_of, "spec": spec, "english": dsl.describe(spec),
                "stats": stats, "config_hash": config.config_hash(),
                "matched": [m["symbol"] for m in matches],
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
    return [_json.loads(l) for l in reversed(lines)]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
