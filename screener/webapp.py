"""Web backend.

    python -m screener.webapp          # http://127.0.0.1:8501

Endpoints
---------
GET  /             the single-page UI
GET  /api/status   data mode (live/demo), as-of date, universe size
POST /api/parse    {"query": str} -> {"spec", "english"} | 422 {"error"}
POST /api/screen   {"spec": dict} -> stats + matches with per-condition
                   evidence (and near-misses: stocks failing exactly one
                   condition, so the user sees the boundary of the filter)

Falls back to a synthetic 8-stock demo universe when no price store exists,
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

import threading

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import config, dsl, evaluator, explain, indicators

app = FastAPI(title="NSE Text Screener")

_state: dict = {}
_lock = threading.Lock()


def _load_state() -> dict:
    with _lock:
        if _state:
            return _state
        if config.PRICE_STORE.exists():
            from . import data_ingest, universe as uni_mod
            prices = pd.read_parquet(config.PRICE_STORE)
            latest = data_ingest.assert_fresh(prices)
            _state.update(
                mode="live",
                panels=indicators.build_panels(prices),
                universe=uni_mod.fetch_universe(),
                benchmark=data_ingest.load_benchmark(),
                as_of=str(latest.date()),
            )
        else:
            from . import demo
            panels, uni, bench = demo.build_demo()
            _state.update(mode="demo", panels=panels, universe=uni,
                          benchmark=bench,
                          as_of=str(panels["STEADY"].index[-1].date()))
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
        spec = parser.parse(body.query)
    except dsl.DSLValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except KeyError:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY not set — plain-English parsing "
                      "is unavailable. Use the JSON spec tab instead."},
            status_code=422)
    return {"spec": spec, "english": dsl.describe(spec)}


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

    for sym, panel in st["panels"].items():
        med_turnover = panel["turnover_cr"].tail(20).median()
        if (st["mode"] == "live"
                and pd.notna(med_turnover)
                and med_turnover < config.MIN_MEDIAN_TURNOVER_CR):
            liquidity_excluded += 1
            continue
        evaluated += 1
        evidence = explain.explain_symbol(panel, spec, as_of,
                                          benchmark=st["benchmark"])
        n_passed = sum(e["passed"] for e in evidence)
        matched = (n_passed == len(evidence) if logic == "AND"
                   else n_passed > 0)
        if not matched and not (logic == "AND"
                                and n_passed == len(evidence) - 1):
            continue

        last = panel.iloc[-1]
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
        (matches if matched else near_misses).append(row)

    matches.sort(key=lambda r: (r["metrics"]["ret_3m_pct"] is None,
                                -(r["metrics"]["ret_3m_pct"] or 0)))
    near_misses.sort(key=lambda r: -r["conditions_passed"])
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
        "matches": matches,
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
        },
    }


def _r(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
