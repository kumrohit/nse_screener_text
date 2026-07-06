"""Portfolio allocation engine (ROADMAP Item 10).

Turns a ranked list of already-screened symbols + capital + a risk
tolerance into integer-share position sizes. This is a *sizing
calculator with documented methodology*, not a recommendation engine —
that framing holds in this module, the API, and the UI. See
TECHNICAL_DESIGN.md §12d for the full reasoning, including the explicit
non-goals: no mean-variance optimisation (estimation error dominates on
small screened subsets — DeMiguel, Garlappi & Uppal 2009), no Kelly
criterion (drawdown profile unsuitable for discretionary use), no return
forecasts, no auto-execution. Refusing these is a feature, not a gap.

Three sizing methods, all computed against the same ranked symbol list
and the same constraints (position cap, sector cap, min ticket):

  "risk"        — fixed-fractional risk sizing (Van Tharp / Turtle-style):
                  shares sized so a 2xATR(14) stop-out loses exactly
                  risk_pct% of capital.
  "inverse_vol" — naive risk parity: capital weight inversely
                  proportional to ATR% (more capital to calmer names,
                  same idea as the low-volatility literature in
                  LITERATURE.md §6, applied to sizing rather than
                  selection).
  "equal"       — 1/N. Always computed as a `baseline` alongside
                  whichever method the caller requested (unless the
                  caller requested "equal" itself) — DeMiguel, Garlappi
                  & Uppal (2009) found 1/N is the benchmark no optimiser
                  reliably beats out-of-sample on estimated inputs, so
                  it is shown for comparison rather than buried as just
                  another dropdown option.

NaN-ATR names (insufficient history) are excluded with a stated reason,
never sized blind — the same "insufficient data never silently
matches/sizes" discipline as the rest of this codebase.
"""
from __future__ import annotations

import math

import pandas as pd

from . import evaluator

METHODS = ("risk", "inverse_vol", "equal")
RISK_PRESETS = {"conservative": 0.5, "moderate": 1.0, "aggressive": 2.0}

DEFAULT_MAX_POSITIONS = 10
DEFAULT_MAX_POSITION_PCT = 15.0
DEFAULT_SECTOR_CAP_PCT = 30.0
DEFAULT_MIN_TICKET = 5000.0


def _gather_candidates(symbols: list[str], panels: dict[str, pd.DataFrame],
                       sector_by_symbol: pd.Series, max_positions: int,
                       as_of: str | None) -> tuple[list[dict], list[dict]]:
    """Resolve each ranked symbol to (entry price, ATR, sector) as of the
    given date, or an excluded-with-reason entry. Order is preserved —
    the caller's ranking (e.g. by RS percentile) decides who gets
    capital/sector-budget priority when candidates exceed the caps."""
    candidates, excluded = [], []
    for sym in symbols[:max_positions]:
        panel = panels.get(sym)
        if panel is None:
            excluded.append({"symbol": sym, "reason": "no price data"})
            continue
        i = evaluator._row_at(panel, as_of)
        if i is None:
            excluded.append({"symbol": sym,
                             "reason": "no data as of this date"})
            continue
        entry = panel["close"].iloc[i]
        atr = panel["atr"].iloc[i]
        atr_pct = panel["atr_pct"].iloc[i]
        if pd.isna(entry) or entry <= 0:
            excluded.append({"symbol": sym, "reason": "no valid close price"})
            continue
        if pd.isna(atr) or atr <= 0:
            excluded.append({
                "symbol": sym,
                "reason": "ATR unavailable (insufficient history) — "
                         "never sized blind",
            })
            continue
        candidates.append({
            "symbol": sym, "entry": float(entry), "atr": float(atr),
            "atr_pct": float(atr_pct) if pd.notna(atr_pct) else None,
            "sector": sector_by_symbol.get(sym) or "—",
        })
    for sym in symbols[max_positions:]:
        excluded.append({
            "symbol": sym,
            "reason": f"beyond max_positions ({max_positions})",
        })
    return candidates, excluded


def _target_values(candidates: list[dict], capital: float, method: str,
                   risk_pct: float) -> None:
    """Mutates each candidate in place: sets target_value (pre-cap ₹
    the method would deploy) and a rationale prefix describing how."""
    n = len(candidates)
    if method == "equal":
        weight = 1.0 / n
        for c in candidates:
            c["target_value"] = capital * weight
            c["weight_note"] = f"equal weight 1/{n} ({weight * 100:.1f}%)"
    elif method == "inverse_vol":
        inv = [(1.0 / c["atr_pct"]) if c["atr_pct"] else 0.0
              for c in candidates]
        total = sum(inv)
        for c, iv in zip(candidates, inv):
            w = (iv / total) if total else 1.0 / n
            c["target_value"] = capital * w
            c["weight_note"] = (f"inverse-vol weight {w * 100:.1f}% "
                               f"(ATR% {c['atr_pct']:.2f})")
    else:  # "risk"
        risk_amount = capital * risk_pct / 100
        for c in candidates:
            c["stop_distance"] = 2 * c["atr"]
            shares_raw = (math.floor(risk_amount / c["stop_distance"])
                         if c["stop_distance"] > 0 else 0)
            c["target_value"] = shares_raw * c["entry"]
            c["risk_amount"] = risk_amount


def _apply_caps_and_build(candidates: list[dict], capital: float,
                          method: str, max_position_pct: float,
                          sector_cap_pct: float, min_ticket: float,
                          excluded: list[dict]) -> list[dict]:
    max_position_value = capital * max_position_pct / 100
    sector_budget = capital * sector_cap_pct / 100
    sector_deployed: dict[str, float] = {}
    capital_deployed = 0.0
    positions = []

    for c in candidates:
        stop_distance = c.get("stop_distance", 2 * c["atr"])
        cap_reason = None
        target_value = c["target_value"]
        if target_value > max_position_value:
            target_value = max_position_value
            cap_reason = f"{max_position_pct:.0f}% position cap"
        sector = c["sector"]
        room = sector_budget - sector_deployed.get(sector, 0.0)
        if target_value > max(room, 0.0):
            target_value = max(room, 0.0)
            cap_reason = f"{sector_cap_pct:.0f}% sector cap ({sector})"
        # "risk" sizing computes each position independently off the risk
        # budget, with no built-in aggregate cap — without this, enough
        # positions can individually pass the per-position/sector caps
        # while summing to more than the capital actually available (a
        # real gap caught via live 500-symbol testing, not synthetic
        # data: 9 positions capped at ~15% each summed past 100%).
        capital_room = capital - capital_deployed
        if target_value > max(capital_room, 0.0):
            target_value = max(capital_room, 0.0)
            cap_reason = "remaining capital"
        shares = math.floor(target_value / c["entry"]) if c["entry"] > 0 \
            else 0
        value = shares * c["entry"]
        if shares <= 0 or value < min_ticket:
            if shares <= 0 and capital_room <= 0:
                reason = "capital fully deployed"
            elif shares <= 0 and room <= 0:
                reason = "sector cap exhausted"
            elif shares <= 0:
                reason = "position sizes to zero shares"
            else:
                reason = "below minimum ticket size (₹5k)"
            excluded.append({"symbol": c["symbol"], "reason": reason})
            continue

        risk_amt = shares * stop_distance
        stop_level = c["entry"] - stop_distance
        pct_of_capital = 100 * value / capital
        sector_deployed[sector] = sector_deployed.get(sector, 0.0) + value
        capital_deployed += value

        # The rationale must show the arithmetic that actually produced
        # the share count. When a cap binds, the risk formula's raw share
        # count differs from the final one — say so, and show the
        # effective (reduced) risk, or the ledger lies.
        if method == "risk":
            shares_raw = (math.floor(c["risk_amount"] / stop_distance)
                          if stop_distance > 0 else 0)
            base = (f"risk ₹{c['risk_amount']:,.0f} ÷ stop "
                    f"distance ₹{stop_distance:,.2f} = {shares_raw} shares")
            if cap_reason and shares < shares_raw:
                rationale = (f"{base} → {cap_reason} limits to {shares} "
                             f"shares = ₹{value:,.0f} "
                             f"({pct_of_capital:.1f}%); effective risk "
                             f"₹{risk_amt:,.0f}")
            else:
                rationale = (f"{base} = ₹{value:,.0f} "
                             f"({pct_of_capital:.1f}%)")
        else:
            rationale = (f"{c['weight_note']} of ₹{capital:,.0f} ÷ "
                        f"entry ₹{c['entry']:,.2f} = {shares} shares "
                        f"= ₹{value:,.0f} ({pct_of_capital:.1f}%)"
                        + (f" [reduced by {cap_reason}]"
                           if cap_reason else ""))

        positions.append({
            "symbol": c["symbol"], "sector": sector,
            "entry": round(c["entry"], 2), "shares": shares,
            "value": round(value, 2),
            "pct_of_capital": round(pct_of_capital, 2),
            "stop_level": round(stop_level, 2),
            "risk": round(risk_amt, 2),
            "rationale": rationale,
        })
    return positions


def _summarize(positions: list[dict], capital: float) -> dict:
    deployed = sum(p["value"] for p in positions)
    sector_totals: dict[str, float] = {}
    for p in positions:
        sector_totals[p["sector"]] = sector_totals.get(p["sector"], 0.0) \
            + p["value"]
    return {
        "deployed": round(deployed, 2),
        "cash": round(capital - deployed, 2),
        "portfolio_risk": round(sum(p["risk"] for p in positions), 2),
        "largest_sector": (max(sector_totals, key=sector_totals.get)
                          if sector_totals else None),
        "n_positions": len(positions),
    }


def _run_method(symbols: list[str], panels: dict[str, pd.DataFrame],
                sector_by_symbol: pd.Series, capital: float, method: str,
                risk_pct: float, max_positions: int, max_position_pct: float,
                sector_cap_pct: float, min_ticket: float,
                as_of: str | None) -> dict:
    candidates, excluded = _gather_candidates(
        symbols, panels, sector_by_symbol, max_positions, as_of)
    if not candidates:
        return {"method": method, "positions": [], "excluded": excluded,
               "summary": _summarize([], capital)}
    _target_values(candidates, capital, method, risk_pct)
    positions = _apply_caps_and_build(
        candidates, capital, method, max_position_pct, sector_cap_pct,
        min_ticket, excluded)
    return {"method": method, "positions": positions, "excluded": excluded,
           "summary": _summarize(positions, capital)}


def allocate(symbols: list[str], panels: dict[str, pd.DataFrame],
            universe: pd.DataFrame | None, capital: float,
            method: str = "risk", risk_pct: float = 1.0,
            max_positions: int = DEFAULT_MAX_POSITIONS,
            max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
            sector_cap_pct: float = DEFAULT_SECTOR_CAP_PCT,
            min_ticket: float = DEFAULT_MIN_TICKET,
            as_of: str | None = "latest") -> dict:
    """(ranked symbols, panels, universe, capital, params) -> allocation
    table + summary + an always-computed equal-weight baseline for
    comparison. `symbols` must already be ranked by whatever priority
    the caller wants (e.g. RS percentile) — this module doesn't re-rank."""
    if method not in METHODS:
        raise ValueError(
            f"method must be one of {METHODS}, got {method!r}")
    if capital <= 0:
        raise ValueError("capital must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")

    sector_by_symbol = (universe.set_index("symbol")["industry"]
                        if universe is not None else pd.Series(dtype=str))

    result = _run_method(symbols, panels, sector_by_symbol, capital, method,
                         risk_pct, max_positions, max_position_pct,
                         sector_cap_pct, min_ticket, as_of)
    result["capital"] = capital

    if method != "equal":
        baseline = _run_method(symbols, panels, sector_by_symbol, capital,
                               "equal", risk_pct, max_positions,
                               max_position_pct, sector_cap_pct, min_ticket,
                               as_of)
        result["baseline"] = baseline

    return result
