# Evidence protocol — the lifecycle of a filter

**Status: DRAFT — needs Rohit's sign-off before it governs anything.**
Sections marked **[PROPOSAL]** are this document's own suggestion, not a
locked decision; sections marked **[LOCKED]** restate a rule already
decided in ROADMAP.md's T1 entry, decided *before* any preset's numbers
existed, on purpose — a retirement bar chosen after looking at results is
not a retirement bar, it's a rationalization.

## Why this exists

This screener now runs forward cohorts on real signals in real time
(ROADMAP Item 16/17). Once a filter has out-of-sample numbers attached
to it, three questions become unavoidable and need answers decided in
advance, not improvised under the pressure of a number that just came in:
when do you stop trusting a filter that's underperforming, when do you
start trusting one enough to act on it for real, and how often do you
actually look. This document is the answer to all three, written down
before the pressure exists.

## 1. Pre-registration [LOCKED, process — not code-enforced today]

A hypothesis must be stated *before* running a backtest, not fitted to
its result afterward. The mechanism already exists —
`backtest --hypothesis "..."` / `backtest_spec(hypothesis=...)` — logs
the stated expectation alongside the backtest's own numbers
(`data/backtest_log.jsonl`), so a hypothesis and its outcome are tied
together in the permanent record rather than one being quietly
forgotten if it turns out wrong.

**Today this is a discipline, not a gate**: `--hypothesis` is optional,
not required — nothing currently stops a backtest from running without
one. Treat "did I write the hypothesis down first" as a personal check
before every backtest, the same way pre-registration works in any other
empirical discipline. **[PROPOSAL]**: if this discipline turns out hard
to keep informally, `backtest_spec()` could require `hypothesis` for any
run whose output feeds a `cohort create`, i.e. enforced exactly at the
point a backtest is about to become a live-tracked commitment — not
enforced on exploratory/throwaway backtests, which would just encourage
inventing a hypothesis to satisfy the gate rather than to mean it.

## 2. Retirement rule [LOCKED]

A preset with **≥6 forward cohorts AND ≥90 days** since the earliest
one's `entry_date` retires if, at the **20-bar horizon**: OOS mean
excess net < 0, **OR** hit rate < 45%.

**The 20-bar horizon is this implementation's own judgment call, not
specified in the locked ROADMAP text — flagged here for sign-off, not
silently assumed.** 20 bars matches `backtest.py`'s own
`SENSITIVITY_HORIZON` convention as "the" reference horizon elsewhere in
this codebase (the sensitivity grid, the cache-hit-vs-cold-build perf
test) — reused here for consistency, not because 20 bars is uniquely
correct. Alternatives considered and rejected for now: the 5-bar horizon
reacts to noise too fast for a retirement decision; the 60-bar horizon
takes nearly a quarter to accumulate 6 cohorts' worth of completed
milestones, which fights the 90-day timeline this same rule sets.
**If you'd rather retire on a different horizon, or require agreement
across more than one, say so and this changes — it's a one-line
constant (`cohorts.RETIREMENT_HORIZON`).**

**Mechanism.** `cohorts.scorecard(universe_id, spec_hash, ...)`'s
return dict carries a `retirement` block computed automatically on
every call — never a separate step to remember:
```
{"eligible": bool,           # >=6 cohorts and >=90 days met?
 "verdict": "retain" | "retire" | "insufficient_evidence",
 "n_cohorts": int, "days_elapsed": int | None,
 "min_cohorts": 6, "min_days": 90, "horizon": 20,
 "mean_excess_net": float | None, "hit_rate": float | None,
 "hit_rate_floor": 0.45}
```
Surfaced in the CLI (`scorecard` command, a `T1 retirement check` line)
and the web UI (cohorts → scorecard view, same line). **This is a
diagnostic only — nothing in this codebase archives a preset
automatically.** `screener/presets.py` is a Python source list, not a
runtime store; there is no code path that could silently retire
something while nobody's looking. A "retire" verdict is read by a
human during the weekly review (§4) and acted on by hand:

1. Edit the preset's dict literal in `presets.py`: set
   `"status": presets.STATUS_ARCHIVED` and attach a `retirement_record`
   — required keys `date`, `verdict`, `n_cohorts`, `days_elapsed`,
   `horizon`, `mean_excess_net`, `hit_rate` (copy straight from the
   `retirement` block above). Validated at import time — a malformed
   record fails the whole test suite immediately, the same fail-fast
   posture as an invalid DSL spec.
2. Archived presets disappear from `presets.active_presets()` (the CLI
   `presets` list, the webapp dropdown) but stay reachable by id via
   `presets.get()` — an old saved dashboard selection or a direct link
   doesn't silently 404, it just stops showing up in discovery.
3. **The cohorts themselves are never touched.** Retiring a preset is
   not the same operation as `cohort delete` — the OOS track record
   that justified retirement stays exactly where it is, still counted
   by `scorecard()`, for exactly the same survivorship-guard reason the
   two-tier cohort deletion (ROADMAP v0.15.1/.2) exists: a retired
   filter's own losing record is the evidence, and quietly erasing it
   the moment it stops looking good would be the precise bias this
   whole tracking system was built to escape.

## 3. Promotion rule [PROPOSAL — no locked text exists for this yet]

The ROADMAP's own T1 entry names this open: "what OOS evidence
qualifies a filter as an entry trigger for the full momentum system."
No default exists in this codebase to fall back on, and — importantly —
**this codebase has no execution layer to promote a filter *into***:
`allocate.py` sizes a hypothetical position, it does not place an order;
every backtest/cohort report carries a "not investment advice" posture
by design (TECHNICAL_DESIGN.md §1). Promotion is entirely about your own
separate trading process, not a code change here. Proposed bar, mirroring
the retirement rule's own shape and strictness rather than inventing a
looser one just because promotion feels like the more exciting decision:

- **≥6 forward cohorts AND ≥90 days** (same eligibility gate as
  retirement — no filter should be actionable on less evidence than the
  minimum needed to *even measure* it) — **AND**
- 20-bar OOS mean excess net > 0 **AND** hit rate ≥ 55% (a real margin
  above the 45% retirement floor, not just "technically not retired" —
  "not yet disproven" and "positively earned" should not be the same
  bar) — **AND**
- The IS (backtest) and OOS (cohort) numbers agree in sign at the same
  horizon (`scorecard()`'s `in_sample`/`horizons` blocks, side by side
  already) — a filter whose backtest and forward record disagree in
  direction has a diagnosis problem, not a promotion case.

None of this is enforced or even computed anywhere yet — no
`cohorts.promotion_verdict()` exists (unlike retirement, which is
wired into every `scorecard()` call). Deliberately not built until this
section is actually signed off, since shipping code for a policy that
might change on review is more churn than value. **Needs your read
before it's real, more than any other section here** — retirement only
risks over-trusting a filter for a few extra weeks before the rule
catches it; promotion risks acting on a filter that shouldn't be acted
on yet.

## 4. Weekly scorecard review ritual [PROPOSAL]

No review cadence existed anywhere in this codebase's docs before this
one — first review already informally targeted for **~2026-07-25**
(ROADMAP SEQUENCING). Proposed ritual, intentionally lightweight (this
is a human process, not new automation — "small code support" per the
ROADMAP's own T1 scope was archival plumbing, not a scheduler):

1. For every preset with `status: active` and at least one forward
   cohort: `python -m screener.cli scorecard <preset_id>` (or the
   webapp's cohorts → scorecard view). Read the `T1 retirement check`
   line.
2. `verdict: retire` → apply §2's archival steps by hand. `verdict:
   retain` → no action, evidence is holding. `verdict:
   insufficient_evidence` → no action, not enough data yet to judge
   either way — this is the expected state for most presets for most
   of the first ~3 months, not a problem to fix.
3. Once §3 is signed off: for every preset meeting the promotion bar,
   flag it for your own separate review of whether to act on it — this
   codebase stops at the flag, deliberately (§3's own "no execution
   layer" point).
4. Note anything surprising (a verdict flipping since last week, a
   preset suddenly eligible) — informally for now; a dedicated running
   log is future scope, not blocking this ritual from starting.

## Terminology note

"Archived" already has one existing, different meaning in this
codebase: a cohort that's reached its final (60-bar) milestone is
informally described as "archived" in TECHNICAL_DESIGN.md prose (its
formal name is `STATUS_COMPLETED`, not `STATUS_ARCHIVED`) — it stays
fully visible in every view and the scorecard, nothing about it is
hidden. This document's "archived preset" is a **different, formal**
status (`presets.STATUS_ARCHIVED`) meaning excluded from discovery. Say
"archived preset" or "completed cohort" explicitly rather than bare
"archived" when it matters which one you mean.

## Sign-off

- [ ] §1 pre-registration discipline — acknowledged, or the
      backtest-requires-hypothesis-before-cohort-create gate should be
      built instead of left as a personal check
- [ ] §2 retirement rule — the 90-day/6-cohort/45%-hit-rate numbers and
      the 20-bar horizon choice, both as specified above or amended
- [ ] §3 promotion rule — as proposed above, amended, or deferred
      further pending more OOS data to calibrate against
- [ ] §4 review ritual — as proposed, or on a different cadence
