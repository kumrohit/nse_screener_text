"""Cohort tracker — walk-forward out-of-sample filter validation
(ROADMAP Item 16, v0.14).

Answers the question the backtester structurally cannot: does this
filter's edge hold up on names nobody hand-picked, followed forward in
real time, with the ones that go bad NOT quietly excluded? A cohort
freezes a set of matches (or a sized allocation) at signal time and
tracks them forward at the exact same horizons/baseline/entry
convention as `screener/backtest.py`, so IS (backtest) and OOS (cohort)
numbers for the same spec are directly comparable. This is a *filter
validator*, not a trade tracker: no fills, no partial exits, no P&L
accounting — see `screener/allocate.py` for position sizing and
`screener/backtest.py` for the in-sample event study this complements.

Methodology — locked, see ROADMAP.md Item 16:

  Dates frozen, never prices   A cohort record stores `entry_date`
                                (a string) and nothing price-derived
                                until a milestone freezes. Returns are
                                recomputed from the live store on every
                                read until frozen, so a retroactive
                                split/bonus adjustment changes nothing
                                (percentage returns are scale-invariant
                                by construction — verified by a
                                dedicated test, not just asserted).
  Entry convention              open[entry_date], identical to the
                                backtester's open[t+1] — entry_date IS
                                the first trading day after the cohort
                                was created, so "the day before
                                entry_date" plays exactly the role of
                                the backtester's signal bar t. Day-0 is
                                `pending`: no returns, ever, until that
                                bar actually exists in the store.
  Milestone snapshots           At 5/20/60 bars post-entry (counted the
                                same way backtest.py counts h — from
                                the signal-bar equivalent, not from
                                entry itself), per-symbol and cohort-
                                aggregate returns freeze permanently
                                into the record the first time they're
                                computed. A live, never-frozen "current"
                                mark-to-market value is always
                                available separately for the active-
                                cohorts view.
  Baseline parity                Same-date equal-weight universe
                                forward return — computed by
                                `backtest.compute_baseline()`, the
                                exact same function the backtester
                                calls, not a reimplementation.
  Symbol lifecycle               A symbol that stops trading before a
                                milestone is reached (delisted,
                                suspended) is flagged `stale` and its
                                last available close carries forward as
                                the exit price — never silently
                                dropped from the cohort's aggregate,
                                which is the whole point of a forward
                                tracker: the backtester's survivorship
                                bias comes from exactly this kind of
                                quiet exclusion.
  Scorecard honesty               A spec's cohorts aggregate into an
                                IS-vs-OOS scorecard; below 20 total
                                names at a horizon, the mean is
                                suppressed as "insufficient sample" —
                                this is evidence accumulation, not
                                hypothesis testing, and says so.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import uuid as _uuid

import numpy as np
import pandas as pd

from . import backtest, config, dsl

HORIZONS = (5, 20, 60)
MIN_SCORECARD_NAMES = 20

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_DELETED = "deleted"     # tombstone — hidden from views, counted
                               # in the scorecard (survivorship guard)

MODE_FORWARD = "forward"
MODE_REPLAY = "replay"

# Schema versioning (ROADMAP Item 18 v1.0 hardening). 1: pre-Item-17
# records (no mode/as_of — the only field this schema has ever gained
# that old records don't already have; v0.15.2's tombstone fields
# (status="deleted", deleted_ts, delete_reason) are purely additive —
# a status value that didn't exist before isn't a migration, it's just
# a status old records will never happen to have — so no version bump
# was needed for that change). The next format change is a new `if
# version < N` block in migrate_record(), not a new .setdefault()
# scattered at another read site.
SCHEMA_VERSION = 2

REPLAY_SURVIVORSHIP_NOTE = (
    "Replay cohort: created as of a historical date with all later data "
    "already visible — in-sample by construction, like the backtester, "
    "and excluded from the out-of-sample scorecard for that reason. It "
    "also carries the backtester's own survivorship caveat: symbols that "
    "left the index/universe between the as-of date and today aren't in "
    "today's universe list to have been trackable in the first place."
)

SURVIVORSHIP_FREE_NOTE = (
    "Out-of-sample cohorts are survivorship-free by construction: every "
    "tracked symbol stays in its cohort even if later delisted or "
    "suspended (flagged stale, never dropped). This is the one bias the "
    "backtester cannot fully remove — IS (backtest) numbers are "
    "survivorship-flattered, OOS (cohort) numbers are small-sample. "
    "Neither is a return forecast; both are for ranking filters."
)


def migrate_record(c: dict) -> dict:
    """Bring one cohort record up to SCHEMA_VERSION, in place, and
    return it. Idempotent — calling this on an already-current record
    is a no-op beyond re-stamping the version number it already had."""
    version = c.get("schema_version", 1)
    if version < 2:
        c.setdefault("mode", MODE_FORWARD)
        c.setdefault("as_of", None)
    c["schema_version"] = SCHEMA_VERSION
    return c


# ------------------------------------------------------------ storage
def _load_all(universe_id: str) -> list[dict]:
    f = config.cohorts_file(universe_id)
    if not f.exists():
        return []
    cohorts = [_json.loads(line) for line in f.read_text().strip().splitlines()
              if line]
    # migrate in place and persist if anything actually changed — same
    # before/after diff-and-write pattern list_cohorts() already uses
    # for refresh_cohort(), so a migrated record is written back to
    # disk on its first read rather than re-migrated (cheap, but never
    # actually landing) on every single subsequent read forever.
    migrated = False
    for c in cohorts:
        before = _json.dumps(c, sort_keys=True)
        migrate_record(c)
        if _json.dumps(c, sort_keys=True) != before:
            migrated = True
    if migrated:
        _save_all(universe_id, cohorts)
    return cohorts


def _save_all(universe_id: str, cohorts: list[dict]) -> None:
    f = config.cohorts_file(universe_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(_json.dumps(c) for c in cohorts)
    f.write_text(body + ("\n" if cohorts else ""))


# ------------------------------------------------------------ weights
def weights_from_symbols(symbols: list[str]) -> dict:
    """Equal weight — 'track these matches' from a plain screen."""
    n = len(symbols)
    return {"method": "equal",
           "by_symbol": {s: round(1.0 / n, 6) for s in symbols}}


def weights_from_positions(positions: list[dict], method: str) -> dict:
    """'Track this portfolio' from an `allocate()` result's `positions`
    list. Weight = each position's share of the TRACKED capital (sum
    of the positions themselves), not the original allocation's input
    capital — un-deployed cash isn't part of what's being tracked, so
    it isn't diluting the measured return. Weights always sum to 1.0
    regardless of origin, so equal- and allocation-weighted cohorts
    aggregate the same way."""
    total = sum(p["value"] for p in positions)
    if total <= 0:
        raise ValueError("positions have zero total value")
    return {"method": method,
           "by_symbol": {p["symbol"]: round(p["value"] / total, 6)
                        for p in positions}}


# ------------------------------------------------------------ calendar / entry
def _resolve_as_of(panels: dict[str, pd.DataFrame], as_of: str) -> str:
    """Canonicalize a user-supplied as_of to an actual trading day at or
    before it (same lenient semantics /api/screen's as-of picker already
    uses — a weekend/holiday date rolls back to the prior close rather
    than being rejected), and validate it leaves at least one later bar
    to evaluate against (ROADMAP Item 17's creation validation). Raises
    ValueError rather than silently returning something usable, since a
    replay cohort's entry_date and mode are derived from this value and
    frozen forever once created."""
    dates = backtest.common_dates(panels)
    ts = pd.Timestamp(as_of)
    idx = dates[dates <= ts]
    if len(idx) == 0:
        raise ValueError(f"as_of {as_of} predates the earliest bar in the store")
    resolved = idx[-1]
    pos = dates.get_loc(resolved)
    if pos >= len(dates) - 1:
        raise ValueError(
            f"as_of {as_of} leaves no later bar to evaluate — pick an "
            "earlier date")
    return str(resolved.date())


def _next_trading_day(panels: dict[str, pd.DataFrame], symbols: list[str],
                      after: pd.Timestamp) -> str | None:
    """First trading day strictly after `after`, using the union of
    dates across the cohort's own symbols (falls back to the full
    universe if none of them have data — shouldn't happen for symbols
    a screen just matched, but never silently guess an entry date)."""
    ref = {s: panels[s] for s in symbols if s in panels} or panels
    if not ref:
        return None
    dates = backtest.common_dates(ref)
    later = dates[dates > after]
    return str(later[0].date()) if len(later) else None


def _signal_row(panel: pd.DataFrame, entry_date: str) -> int | None:
    """Row index t such that panel.index[t+1] is entry_date's own bar
    on this symbol's panel — the backtester's signal-bar convention,
    reverse-engineered from a known entry date instead of a fired
    condition. None if this symbol's panel doesn't have that bar yet."""
    ts = pd.Timestamp(entry_date)
    idx = panel.index[panel.index == ts]
    if not len(idx):
        return None
    row = panel.index.get_loc(ts)
    return row - 1 if row > 0 else None


def _milestone_reached(panels: dict[str, pd.DataFrame], entry_date: str,
                       h: int) -> bool:
    """Has horizon h (bars, backtester-counted from the signal bar)
    elapsed since entry, using the shared universe calendar — not any
    one symbol's own history, which may be gappy or stale. This is
    what lets a per-symbol staleness check distinguish 'this name
    delisted early' from 'the cohort just hasn't aged that far yet'."""
    dates = backtest.common_dates(panels)
    pos = dates.searchsorted(pd.Timestamp(entry_date))
    target = pos - 1 + h
    return 0 <= target < len(dates)


def _symbol_return(panel: pd.DataFrame, entry_date: str, h: int
                   ) -> tuple[float | None, bool]:
    """(return, stale) at horizon h for one symbol, given the cohort
    has already aged enough overall (see `_milestone_reached`). If
    this symbol's own panel doesn't reach that far, it stopped trading
    early — stale — and its last available close carries forward as
    the exit price rather than dropping the symbol."""
    t = _signal_row(panel, entry_date)
    if t is None:
        return None, False
    entry_price = panel["open"].iloc[t + 1]
    if pd.isna(entry_price) or entry_price <= 0:
        return None, False
    if t + h < len(panel):
        exit_price = panel["close"].iloc[t + h]
        if pd.notna(exit_price):
            return float(exit_price / entry_price - 1), False
    last_close = panel["close"].iloc[-1]
    if pd.isna(last_close):
        return None, False
    return float(last_close / entry_price - 1), True


def current_return(panel: pd.DataFrame, entry_date: str) -> float | None:
    """Live mark-to-market return using the panel's latest close as an
    undated exit — for the 'current' row that keeps drifting until a
    fixed milestone freezes it. Never frozen, never stored."""
    t = _signal_row(panel, entry_date)
    if t is None:
        return None
    entry_price = panel["open"].iloc[t + 1]
    if pd.isna(entry_price) or entry_price <= 0:
        return None
    last_close = panel["close"].iloc[-1]
    if pd.isna(last_close):
        return None
    return float(last_close / entry_price - 1)


def _signal_date(panels: dict[str, pd.DataFrame], entry_date: str
                 ) -> pd.Timestamp | None:
    dates = backtest.common_dates(panels)
    pos = dates.searchsorted(pd.Timestamp(entry_date))
    return dates[pos - 1] if pos > 0 else None


def current_snapshot(cohort: dict, panels: dict[str, pd.DataFrame],
                     cost_pct: float = backtest.DEFAULT_COST_PCT
                     ) -> dict | None:
    """Live mark-to-market view for the active-cohorts list — same
    weighted-aggregate shape as a frozen milestone, but using each
    symbol's latest close as an undated exit. None for a still-pending
    cohort (no entry bar yet, nothing to mark)."""
    if cohort["status"] == STATUS_PENDING or cohort["entry_date"] is None:
        return None
    weights = cohort["weights"]["by_symbol"]
    per_symbol = {}
    weighted_sum, weight_total = 0.0, 0.0
    for sym in cohort["symbols"]:
        panel = panels.get(sym)
        ret = current_return(panel, cohort["entry_date"]) \
            if panel is not None else None
        per_symbol[sym] = {"return": round(ret, 4) if ret is not None else None}
        if ret is not None:
            w = weights.get(sym, 0.0)
            weighted_sum += w * ret
            weight_total += w
    gross = weighted_sum / weight_total if weight_total > 0 else None
    net = gross - cost_pct / 100 if gross is not None else None
    return {"per_symbol": per_symbol,
           "gross": round(gross, 4) if gross is not None else None,
           "net": round(net, 4) if net is not None else None}


# ------------------------------------------------------------ aggregation
def _aggregate_milestone(panels: dict[str, pd.DataFrame], symbols: list[str],
                         weights_by_symbol: dict[str, float],
                         entry_date: str, h: int, cost_pct: float,
                         baseline_h: pd.Series) -> dict | None:
    """One horizon's frozen snapshot, or None if not reached yet."""
    if not _milestone_reached(panels, entry_date, h):
        return None

    per_symbol, stale_count = {}, 0
    weighted_sum, weight_total = 0.0, 0.0
    for sym in symbols:
        panel = panels.get(sym)
        if panel is None:
            per_symbol[sym] = {"return": None, "stale": True}
            stale_count += 1
            continue
        ret, stale = _symbol_return(panel, entry_date, h)
        per_symbol[sym] = {"return": round(ret, 4) if ret is not None else None,
                          "stale": stale}
        if stale:
            stale_count += 1
        if ret is not None:
            w = weights_by_symbol.get(sym, 0.0)
            weighted_sum += w * ret
            weight_total += w

    gross = weighted_sum / weight_total if weight_total > 0 else None
    net = gross - cost_pct / 100 if gross is not None else None

    sig_date = _signal_date(panels, entry_date)
    b = baseline_h.get(sig_date) if sig_date is not None else None
    b = float(b) if b is not None and pd.notna(b) else None
    excess_gross = gross - b if gross is not None and b is not None else None
    excess_net = net - b if net is not None and b is not None else None

    return {
        "per_symbol": per_symbol,
        "n_symbols": len(symbols),
        "n_stale": stale_count,
        "gross": round(gross, 4) if gross is not None else None,
        "net": round(net, 4) if net is not None else None,
        "baseline": round(b, 4) if b is not None else None,
        "excess_gross": round(excess_gross, 4) if excess_gross is not None else None,
        "excess_net": round(excess_net, 4) if excess_net is not None else None,
        "frozen_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------ lifecycle
def refresh_cohort(cohort: dict, panels: dict[str, pd.DataFrame],
                   min_turnover_cr: float,
                   cost_pct: float = backtest.DEFAULT_COST_PCT) -> dict:
    """Advance a cohort in place: resolve entry_date if still pending,
    freeze any newly-reached milestone, complete at 60 bars. This IS
    the "nightly refresh" — there's no separate cron state, milestones
    are computed lazily from frozen dates whenever a cohort is read
    (ROADMAP Item 16's explicit design), so viewing is refreshing."""
    symbols = list(cohort["symbols"])

    if cohort.get("status") == STATUS_DELETED:
        return cohort  # tombstones never advance

    if cohort["status"] == STATUS_PENDING:
        # ROADMAP Item 17: a replay cohort's entry is anchored to its
        # (already-historical, already-validated) as_of date, not to
        # "now" — this is what lets it resolve to active on the very
        # next refresh instead of waiting for real calendar time to
        # pass, the whole point of replay. Forward cohorts are
        # unaffected (mode defaults to "forward", anchor stays created_ts).
        anchor_str = (cohort["as_of"] if cohort["mode"] == MODE_REPLAY
                     else cohort["created_ts"])
        anchor = pd.Timestamp(anchor_str).normalize()
        entry_date = _next_trading_day(panels, symbols, anchor)
        if entry_date is None:
            return cohort
        cohort["entry_date"] = entry_date
        cohort["status"] = STATUS_ACTIVE

    if cohort["status"] == STATUS_ACTIVE:
        weights = cohort["weights"]["by_symbol"]
        baseline = None
        for h in HORIZONS:
            key = str(h)
            if cohort["milestones"].get(key) is not None:
                continue
            if baseline is None:  # computed at most once per refresh call
                baseline = backtest.compute_baseline(
                    panels, list(panels.keys()), HORIZONS, min_turnover_cr)
            snap = _aggregate_milestone(
                panels, symbols, weights, cohort["entry_date"], h,
                cost_pct, baseline[h])
            if snap is not None:
                cohort["milestones"][key] = snap
        if cohort["milestones"].get(str(HORIZONS[-1])) is not None:
            cohort["status"] = STATUS_COMPLETED

    return cohort


def create_cohort(*, universe_id: str, spec: dict, symbols: list[str],
                  weights: dict, notes: str = "",
                  created_ts: str | None = None,
                  as_of: str | None = None,
                  panels: dict[str, pd.DataFrame] | None = None) -> dict:
    """Freeze a new cohort. `spec` is re-validated defensively (same
    discipline as backtest_spec) since a caller-supplied dict may not
    have gone through dsl.validate yet. `created_ts` defaults to now —
    the override exists for tests that need a cohort created 60+ real
    trading days in the past to exercise milestone freezing without
    waiting two months for real data to accumulate.

    ROADMAP Item 17: `as_of` (needs `panels`) creates a REPLAY cohort —
    as of any historical date already in the store, with all later data
    already visible. Mode is derived here, once, and never editable
    afterward: any explicit as_of makes this a replay cohort by
    definition (the future is visible at creation time), never
    "forward" no matter how recent the date. `as_of` is canonicalized
    to an actual trading day and validated to leave a later bar via
    `_resolve_as_of` — a bad as_of raises rather than silently creating
    an unevaluable cohort."""
    spec = dsl.validate(spec)
    if not symbols:
        raise ValueError("a cohort needs at least one symbol")
    mode = MODE_FORWARD
    resolved_as_of = None
    if as_of and as_of != "latest":
        if panels is None:
            raise ValueError("as_of requires panels to validate against")
        resolved_as_of = _resolve_as_of(panels, as_of)
        mode = MODE_REPLAY
    cohort = {
        "cohort_id": _uuid.uuid4().hex[:12],
        "created_ts": created_ts or _dt.datetime.now().isoformat(
            timespec="seconds"),
        "universe": universe_id,
        "spec": spec,
        "spec_hash": dsl.spec_hash(spec),
        "symbols": list(symbols),
        "weights": weights,
        "entry_date": None,
        "status": STATUS_PENDING,
        "notes": notes,
        "milestones": {str(h): None for h in HORIZONS},
        "mode": mode,
        "as_of": resolved_as_of,
        "schema_version": SCHEMA_VERSION,
    }
    cohorts = _load_all(universe_id)
    cohorts.append(cohort)
    _save_all(universe_id, cohorts)
    return cohort


def list_cohorts(universe_id: str, panels: dict[str, pd.DataFrame],
                 min_turnover_cr: float, spec_hash: str | None = None
                 ) -> list[dict]:
    """Every cohort for this universe, refreshed against current data
    (lazy — see `refresh_cohort`) before returning. Persists any
    lifecycle change (pending->active, a newly-frozen milestone,
    active->completed) so the next read doesn't recompute it."""
    cohorts = _load_all(universe_id)
    changed = False
    for c in cohorts:
        before = _json.dumps(c, sort_keys=True)
        refresh_cohort(c, panels, min_turnover_cr)
        if _json.dumps(c, sort_keys=True) != before:
            changed = True
    if changed:
        _save_all(universe_id, cohorts)
    cohorts = [c for c in cohorts
              if c.get("status") != STATUS_DELETED]
    if spec_hash:
        cohorts = [c for c in cohorts if c["spec_hash"] == spec_hash]
    return cohorts


def get_cohort(universe_id: str, cohort_id: str,
               panels: dict[str, pd.DataFrame], min_turnover_cr: float
               ) -> dict | None:
    for c in list_cohorts(universe_id, panels, min_turnover_cr):
        if c["cohort_id"] == cohort_id:
            return c
    return None


def delete_cohort(universe_id: str, cohort_id: str,
                 reason: str | None = None) -> dict:
    """Two-tier deletion (survivorship guard).

    HARD delete (record removed): replay cohorts and pending forward
    cohorts — no out-of-sample evidence value at stake (a replay date
    was chosen with hindsight; a pending cohort has no entry bar yet).

    TOMBSTONE (record kept, hidden, counted): forward cohorts at or
    past their entry bar. These ARE the OOS track record — silently
    hard-deleting the losers would hand-craft the exact survivorship
    bias the tracker exists to escape. Requires a non-empty `reason`,
    which the scorecard surfaces as a per-spec deleted count.

    Returns {"removed": bool, "tombstoned": bool, "error": str|None}.
    """
    cohorts = _load_all(universe_id)
    target = next((c for c in cohorts if c["cohort_id"] == cohort_id),
                  None)
    if target is None:
        return {"removed": False, "tombstoned": False, "error": None}
    if target.get("status") == STATUS_DELETED:
        return {"removed": False, "tombstoned": False,
                "error": "already deleted"}

    hard = (target.get("mode") == MODE_REPLAY
            or target.get("status") == STATUS_PENDING)
    if hard:
        _save_all(universe_id,
                  [c for c in cohorts if c["cohort_id"] != cohort_id])
        return {"removed": True, "tombstoned": False, "error": None}

    if not (reason and reason.strip()):
        return {"removed": False, "tombstoned": False,
                "error": "forward cohort past entry: a deletion reason "
                         "is required (tombstoned, not erased — the OOS "
                         "scorecard counts deletions)"}
    target["status"] = STATUS_DELETED
    target["deleted_ts"] = pd.Timestamp.now().isoformat(timespec="seconds")
    target["delete_reason"] = reason.strip()
    _save_all(universe_id, cohorts)
    return {"removed": True, "tombstoned": True, "error": None}


def deleted_forward_summary(universe_id: str, spec_hash: str) -> dict:
    """Tombstone census for one spec — feeds the scorecard footer."""
    tombs = [c for c in _load_all(universe_id)
            if c.get("status") == STATUS_DELETED
            and c.get("mode") == MODE_FORWARD
            and c["spec_hash"] == spec_hash]
    return {"count": len(tombs),
            "reasons": [c.get("delete_reason", "") for c in tombs]}


# ------------------------------------------------------------ evidence protocol (T1)
# Retirement rule — locked in EVIDENCE_PROTOCOL.md / ROADMAP's T1 track,
# decided before results exist rather than fit to whatever a preset's
# numbers happen to look like once they exist. `RETIREMENT_HORIZON`
# (which of the 5/20/60-bar milestones the rule reads) is this
# implementation's own judgment call, not specified in the locked text
# — 20 bars matches backtest.py's own SENSITIVITY_HORIZON convention as
# "the" reference horizon elsewhere in this codebase. Flagged in
# EVIDENCE_PROTOCOL.md as needing sign-off, not silently assumed.
MIN_RETIREMENT_COHORTS = 6
MIN_RETIREMENT_DAYS = 90
RETIREMENT_HORIZON = 20
RETIREMENT_HIT_RATE_FLOOR = 0.45


def _retirement_verdict(forward_cohorts: list[dict], per_horizon: dict,
                        horizon: int = RETIREMENT_HORIZON) -> dict:
    """Diagnostic only — never mutates anything. `presets.py` has no
    runtime store to archive a preset in (it's a Python source list),
    so this never retires anything itself: a human reads a "retire"
    verdict during the weekly review and edits the preset's dict
    literal to `status=STATUS_ARCHIVED` with this record attached,
    per EVIDENCE_PROTOCOL.md's own "process, not code" framing."""
    n_cohorts = len(forward_cohorts)
    entry_dates = [c["entry_date"] for c in forward_cohorts
                  if c.get("entry_date")]
    days_elapsed = None
    if entry_dates:
        earliest = min(pd.Timestamp(d) for d in entry_dates)
        days_elapsed = int(
            (pd.Timestamp.now().normalize() - earliest).days)

    base = {"n_cohorts": n_cohorts, "days_elapsed": days_elapsed,
           "min_cohorts": MIN_RETIREMENT_COHORTS,
           "min_days": MIN_RETIREMENT_DAYS, "horizon": horizon,
           "hit_rate_floor": RETIREMENT_HIT_RATE_FLOOR,
           "mean_excess_net": None, "hit_rate": None}

    eligible = (n_cohorts >= MIN_RETIREMENT_COHORTS
               and days_elapsed is not None
               and days_elapsed >= MIN_RETIREMENT_DAYS)
    h = per_horizon.get(str(horizon), {})
    if not eligible or h.get("insufficient", True):
        return {**base, "eligible": eligible,
               "verdict": "insufficient_evidence"}

    fails = (h["mean_excess_net"] < 0
            or h["hit_rate"] < RETIREMENT_HIT_RATE_FLOOR)
    return {**base, "eligible": True,
           "verdict": "retire" if fails else "retain",
           "mean_excess_net": h["mean_excess_net"],
           "hit_rate": h["hit_rate"]}


# ------------------------------------------------------------ scorecard
def _aggregate_scorecard_horizons(cohorts: list[dict]) -> dict:
    """Per-horizon mean/median/hit-rate on excess net, from whatever
    cohort group is handed in — the same aggregation reused for both
    the OOS (forward) group and the walled-off replay (in-sample)
    group (ROADMAP Item 17), so the two numbers are computed identically
    and only differ in which cohorts fed them."""
    per_horizon = {}
    for h in HORIZONS:
        key = str(h)
        snaps = [c["milestones"][key] for c in cohorts
                if c["milestones"].get(key) is not None]
        n_names = sum(s["n_symbols"] for s in snaps)
        excess_vals = [s["excess_net"] for s in snaps
                      if s["excess_net"] is not None]
        if n_names < MIN_SCORECARD_NAMES or not excess_vals:
            per_horizon[key] = {
                "n_cohorts": len(snaps), "n_names": n_names,
                "insufficient": True, "mean_excess_net": None,
                "median_excess_net": None, "hit_rate": None,
            }
            continue
        arr = np.array(excess_vals)
        per_horizon[key] = {
            "n_cohorts": len(snaps), "n_names": n_names,
            "insufficient": False,
            "mean_excess_net": round(float(arr.mean()), 4),
            "median_excess_net": round(float(np.median(arr)), 4),
            "hit_rate": round(float((arr > 0).mean()), 4),
        }
    return per_horizon


def scorecard(universe_id: str, spec_hash: str,
             panels: dict[str, pd.DataFrame], min_turnover_cr: float,
             backtest_log_entries: list[dict] | None = None) -> dict:
    """Per-spec IS-vs-OOS scorecard: this spec's cohorts aggregated per
    horizon, side by side with the most recent logged backtest run for
    the same spec_hash (if any). <20 total tracked names at a horizon
    suppresses the mean — evidence accumulation, not hypothesis
    testing, and the scorecard says so rather than printing a number
    that looks more confident than the sample supports.

    ROADMAP Item 17's integrity wall: replay-mode cohorts (created as
    of a historical date, all later data already visible at creation
    time) are in-sample by construction and NEVER enter `horizons` —
    they get their own clearly-labelled `replay` block, using the same
    aggregation helper, so they're visible without being able to
    silently inflate the out-of-sample numbers."""
    cohorts = list_cohorts(universe_id, panels, min_turnover_cr,
                           spec_hash=spec_hash)
    forward_cohorts = [c for c in cohorts if c["mode"] == MODE_FORWARD]
    replay_cohorts = [c for c in cohorts if c["mode"] == MODE_REPLAY]
    per_horizon = _aggregate_scorecard_horizons(forward_cohorts)

    in_sample = None
    if backtest_log_entries:
        # spec_hash alone isn't enough: the same spec can legitimately
        # run on more than one universe, and pairing an OOS cohort with
        # an IS backtest from a DIFFERENT universe would silently
        # compare apples to oranges.
        matches = [e for e in backtest_log_entries
                  if e.get("spec_hash") == spec_hash
                  and e.get("universe") == universe_id]
        if matches:
            in_sample = matches[-1].get("horizons")

    return {
        "spec_hash": spec_hash,
        "n_cohorts_total": len(forward_cohorts),
        "deleted_forward": deleted_forward_summary(universe_id, spec_hash),
        "horizons": per_horizon,
        "retirement": _retirement_verdict(forward_cohorts, per_horizon),
        "replay": {
            "label": "replay (in-sample) — NOT part of the OOS scorecard",
            "n_cohorts": len(replay_cohorts),
            "horizons": _aggregate_scorecard_horizons(replay_cohorts),
        },
        "in_sample": in_sample,
        "footnote": "IS is survivorship-flattered; OOS is small-sample.",
        "survivorship_free_note": SURVIVORSHIP_FREE_NOTE,
    }
