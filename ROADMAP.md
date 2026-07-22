# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.16 — **Market breadth regime fields complete**
2026-07-12 (ROADMAP §C, LITERATURE.md §9) — `pct_above_200dma` and
`pct_at_20d_high`, computed from the universe itself with no external
data feed, and a new `breadth` DSL condition ("market breadth
positive" → pct_above_200dma ≥ 50) usable everywhere a condition can
be: screening, evidence trails, and the backtester (a vectorized
`compute_breadth_series`/`_vec_breadth` path, verified byte-exact
against the row-by-row evaluator — not stride-approximated, since
breadth is cheap to compute exactly). Deliberately shipped as a
standalone gap-fill, decoupled from the nse_full-vs-nifty500 preset
backtest comparison it was originally paired with in the ROADMAP §C
sequencing — that comparison stays blocked on pre-registered hypotheses
(the point of pre-registering is writing the prediction down before
running the analysis), so building the tool wasn't held hostage to a
human prerequisite. Also shipped this session: two-tier cohort
deletion (v0.15.2) — a forward cohort past its entry bar is now
tombstoned with a required reason and counted on the scorecard rather
than hard-deleted, closing a survivorship-bias gap in v0.15.1's
original plain-delete.

**v0.15** — **Item 17 (cohort replay & performance
engine) complete** 2026-07-11 — a cohort can now be created as of ANY
historical date (`as_of`, resolved leniently to the trading day at or
before it, validated to leave ≥1 later bar) instead of only "starting
now," and every cohort — replay or forward — gets a full performance
panel (`screener/cohort_perf.py`, pure functions, no code duplicated
from Item 16's baseline/entry-convention machinery): cumulative
return gross/net, excess vs. the same-entry-date universe baseline
and vs. Nifty, annualised vol, max drawdown with peak/trough dates
(cohort-level AND per-symbol), hit rates, weighted contributors, and
an equity curve (cohort/baseline/Nifty, indexed to 100 at entry) for
an arbitrary window (`end_date`, clamped to the latest bar, resolved
leniently on non-trading days) — Sharpe reported only ≥60 bars, an
honest "window too short" otherwise. The integrity wall is the
headline: a replay cohort (any explicit `as_of`) is in-sample by
construction — mode is derived server-side, not a field a caller can
set — and is walled out of the OOS scorecard into its own
clearly-labelled block, verified by a dedicated test. Three surfaces:
UI ("track these matches"/"track this portfolio" now thread the
screen's own as-of date through automatically, so replaying is the
natural result of tracking an as-of screen — no separate control
needed; a Cohorts detail view gained a REPLAY badge, an equity-curve
chart, the metrics panel, and an evaluate-to date control), CLI
(`cohort create --as-of`, `cohort perf <id> [--end]`), and API
(`as_of` on `POST /api/cohorts`, `GET /api/cohorts/{id}/performance`).
33 new tests (21 core + 8 API + 4 follow-up), 313 total, all green.
One real bug caught via live Playwright testing, not unit tests: the
initial (default-window) performance fetch and a later
"evaluate-to-a-different-date" fetch could race, and the slower one —
whichever it was — would land last and silently overwrite the newer
result; fixed by capturing the requested end-date at fetch-start and
discarding the response if it no longer matches current UI state
before applying it. Two gaps found via post-implementation spec review
before shipping (own max-drawdown and weighted contribution were
missing from the per-symbol row; the survivorship note was defined but
never actually attached to any API/CLI/UI surface) — both closed and
covered by new tests rather than left as silent scope-narrowing.

**v0.14** — **Item 16 (cohort tracker, walk-forward
out-of-sample filter validation) complete** 2026-07-11 —
`screener/cohorts.py` freezes a cohort of matches (or a sized
allocation) at signal time and tracks it forward at the exact same
horizons/baseline/entry convention as the Item-14 backtester
(`backtest.compute_baseline()` is shared, not reimplemented), with
nothing ever dropped — a symbol that delists or gets suspended before
a milestone is flagged stale and carries its last close forward
instead of quietly exiting the sample, which is the specific bias a
forward tracker exists to avoid. Dates are frozen, never prices, so
returns recomputed on every read are invariant to retroactive
split/bonus adjustments (verified by a dedicated halve-the-series
test, not just asserted). Milestones (5/20/60 bars) freeze permanently
into the record the first time they're reached; a live "current"
mark-to-market view is always available separately and never stored.
A per-spec scorecard aggregates cohorts into OOS mean/median/hit-rate
per horizon side by side with the most recent logged IS backtest for
the same spec_hash *and* universe (a real gap caught while building
this: the backtest log had no universe field, so the same spec_hash
run on two universes could silently cross-pair — fixed and
regression-tested), suppressing the mean below 20 tracked names rather
than printing a falsely-confident number. Three surfaces: web UI
("track these matches" / "track this portfolio" buttons, a Cohorts tab
with an active-cohorts list, per-symbol detail with click-to-chart
entry markers, and a scorecard view), a CLI (`cohort create/list/show`,
`scorecard`), and the API. 22 tests (exceeds the ≥14 target); two real
bugs also fixed along the way — `tests/test_evaluator.py` was
unconditionally writing every `/api/screen` test call to the real
`data/screen_log.jsonl` (no isolation fixture existed for it), and the
CLI's own `screen` command never logged at all, silently breaking
`cohort create --from-last-screen` for CLI-only workflows; both fixed
and a `data/*/*.jsonl` gitignore gap (per-universe cohort stores were
untracked-but-not-ignored) closed alongside. Cohorts seeded from all
four named presets (`support_50ema_uptrend`, `momentum_12_1_leaders`,
`minervini_stage2`, `flat_base_52w`) on both `nifty500` and `nse_full`
per the sequencing plan, so early milestones mature ahead of the
bhavcopy cutover.

**v0.7 track complete** (Items 5 and 6);
**Item 9 (evidence-based strategy presets) complete**; **Item 10
(portfolio allocation engine) complete**; **Item 11 (UI professional
redesign) shipped in part** — the sidebar layout restructure is
explicitly deferred by decision, everything else done; **Item 14
(screen backtester) complete** — Item 3 (bhavcopy cutover) now unparked
per Item 14's spec note, still calendar-gated on its own evidence
window; **Item 15 Phase A (universe registry) complete** 2026-07-10 —
three universes registered (`nifty500`, `nse_full` 2,047 symbols,
`nse_etf` 36 curated broad equity-index ETFs), `--universe` CLI
threading, a webapp universe selector, the hard memory gate measured
and passed (782 MB, no architecture change needed), a sector-data-gap
warning (loud, not a silent zero-match), and preset `universes` tags
computed from spec content. Point-in-time index membership (Phase B)
remains gated on data-source archaeology.
Data layer live-verified (500/500), 21-condition DSL (incl. sector
filters & cross-sectional relative strength, gap, atr_pct_percentile),
patterns, 26 built-in presets (all evidence-annotated, see
LITERATURE.md) + unlimited saved custom screens, web UI with
evidence trails, sparklines, as-of replay, screen log, CSV export,
recent-screens replay, data-quality badges, config-hash footer,
screen diff, full chart modal, watchlist, multi-screen dashboard,
sortable/filterable results, evidence tags on the preset picker.
NSE bhavcopy data layer v2 built and validated, running side-by-side
(2-week evidence clock started 2026-07-05); not cut over, nothing
reads from it yet. Portfolio allocation engine (`allocate.py`,
`/api/allocate`, an "Allocate" UI panel) sizes any result set into
integer-share positions via fixed-fractional risk, inverse-volatility,
or equal weight, with per-position/sector/aggregate-capital caps — the
aggregate-capital cap was a real gap caught only via live 500-symbol
testing, fixed and regression-tested before shipping. UI split into
`app.css`/`app.js`/`index.html` with a documented design-token system,
a verified accessibility floor (keyboard-operable end to end, WCAG AA
contrast), toasts, print/report mode, and a committed Playwright
visual-regression baseline (`web/visual/`). The screen backtester
(`screener/backtest.py`, `/api/backtest`, a "🧪 backtest" UI panel, and
a `backtest` CLI command) turns any DSL spec into an event study vs. a
same-date universe baseline — cooldown-deduped events, entry at
open[t+1], gross/net costs, date-level block bootstrap, a <30-event
suppression floor, an auto-detected one-at-a-time sensitivity grid, and
a survivorship caveat on every surface. Vectorized for the cheap
condition types; the expensive ones (S/R, Bollinger squeeze,
cross-sectional percentiles) use a `stride=20` date grid — raised from
the originally-planned 5 after live measurement showed 5 took 6-15
minutes per condition against the real 500-symbol store. The universe
registry (`screener/universes.py`) turns "which symbols/store/benchmark"
into a config entry rather than hardcoded paths — `nifty500` (migrated
from a flat `data/` layout into `data/nifty500/` via an idempotent
one-time move), `nse_full` (all 2,047 NSE EQ-series symbols, backfilled
live over the existing yfinance pipeline — no new adjustment-correctness
code), and `nse_etf` (36 curated broad domestic equity-index ETFs —
NSE's own ETF listing turned out too inconsistently classified to
auto-derive this list reliably, checked live before committing to an
approach), each with its own liquidity gate and survivorship note,
selectable from a webapp header dropdown or `--universe` on the CLI.
Memory gate measured and passed (782 MB peak RSS with panels resident
for both larger universes simultaneously, vs. a 4 GB target) — no
on-demand/LRU rearchitecture needed. A universe with no sector/industry
data (nse_full, nse_etf) now warns loudly on a sector-based screen
instead of silently returning zero matches, and the preset dropdown
filters itself to what's applicable on the active universe. 334 tests
green, no known failures — `tests/conftest.py` makes the suite hermetic
(forces demo mode, and isolates every webapp log/store file to a
per-test tmp path, so it passes identically in CI and on a dev machine
that has already run `backfill`, without ever touching real `data/`).
Next up: the nse_full-vs-nifty500 preset backtest comparison (needs
Rohit's pre-registered hypotheses first — breadth fields themselves are
done, see status snapshot above), point-in-time index membership
(Item 15 Phase B, gated on data-source archaeology), the deferred
sidebar layout restructure, the preset evidence loop-closure (Item
14's own follow-on, now also fed by Items 16/17's OOS/replay
scorecards), or Item 3's bhavcopy cutover once its evidence window
closes.

---

---

## SEQUENCING — updated 2026-07-20 (cutover clock reset)

**Landed:** v0.18.0 indicator cache (Item 20 P1+P5) — warm screens now
seconds, not minutes; targets live-verified. v0.17 Link screens with
the divergence recall follow-up filed in Item 19 (check its backtest
event counts before amending — still open, blocked once by an
unrelated local I/O issue, retry). 395 tests.

**Milestone watch:** forward-cohort **5-bar milestones froze 07-17/20**
— first OOS numbers exist. First scorecard review ~07-25; **T1 evidence
protocol must be signed before it (7 days).**

**Cutover: clock was wrong, now corrected.** `bhavcopy-update` is a
manual command and was run exactly once (2026-07-05) — the "2-week
clock" in the previous version of this doc counted calendar time while
the actual side-by-side store sat frozen at 6 days of real data
(06-25→07-03) for over two weeks. Caught 2026-07-20 by checking the
store's file mtime directly; **the cutover verdict was deferred**
rather than decided on stale evidence. `bhavcopy-update` re-run same
day, catching the store up to 07-17 (16 real trading days now
overlapping) — now wired into the documented nightly cron (README,
TECHNICAL_DESIGN §11) so this can't lapse silently again. Early read on
the fuller window is **reassuring, not alarming**: 382/7,986 bars over
the 0.5% WARN threshold sounds high, but it's fully explained —
directly confirmed against NSE's own corporate-actions feed — by real
dividend ex-dates inside the window (yfinance retroactively adjusts for
them, bhavcopy/our own pipeline correctly don't, by design). See
TECHNICAL_DESIGN.md §4a for the full analysis. **The actual go/no-go
verdict and config flip are still Rohit's call**, once a real
continuous window has accumulated from today's restart — not decided
by this analysis alone.

**Remaining to v1.0:** cutover verdict (Rohit, once the restarted
window matures) → config flip + delivery condition/preset + risk log
(risk log now written, §4a) + seed delivery cohorts; comparison run
(hypotheses — Rohit); 15-B membership (archaeology — Rohit); sidebar;
Item 20 P2 (parallel rebuild — only the cron pays the build now, so P2
is nice-to-have, no longer gating) and P3 (webapp evaluate-first,
optional). Then TAG v1.0.

**After the tag:** T1 (deadline above) → Item 21 pairs discovery
(first post-cutover build session) → divergence recall check → T2
regime-conditional.

---

## 0. One-time setup & validation

- [x] **Push to GitHub** — single permanent working folder, retire the
      `_v1`-style folder copies. Done 2026-07-05: stray `latest` remote
      (pointing at a sibling folder copy, `nse_screener4`) removed,
      pending commit pushed to `origin/main`.
- [x] **Fix commit author** — used a `.mailmap` (non-destructive: remaps
      display identity without rewriting already-pushed history or
      force-pushing) plus fixed local `git config user.name/email` for
      future commits, instead of `--amend --reset-author`.
- [x] **Classify the 6 jump bars** — `verify --jumps`. 3 confirmed
      genuine demergers via public news (ABFRL — Aditya Birla Lifestyle
      Brands spin-off, record date 2025-05-22; TMPV — Tata Motors CV/PV
      split, 2025-10-14; VEDL — Vedanta 5-way demerger, 2026-04-30). The
      other 3 (CGCL, GPIL, MOTILALOFS) are **not** simple unadjusted
      splits — refetching returned byte-identical data. Their ratios
      match real 2024 corporate actions almost exactly, but the jump
      lands on 2024-01-01 for all three, months before the true record
      dates (Mar/Jun/Oct 2024) — a yfinance/Yahoo adjustment-splice bug,
      documented as a known caveat in TECHNICAL_DESIGN.md §4. Not
      currently affecting live screens (252-bar lookback as of
      2026-07-03 only reaches back to 2025-06-30) but would corrupt
      historical as-of replays dated in that window for those 3 symbols.
- [x] **Calibrate `support_at_ma` tolerance** — ran the flagship preset
      (`support_50ema_uptrend`) across 4 as-of dates (2026-04-01,
      2026-05-01, 2026-06-01, 2026-07-03), 3 matches each. Decision:
      **keep 1.5%/3 bars** for now — recorded in TECHNICAL_DESIGN.md §6.
      Revisit with a larger sample if near-miss lists suggest the
      tolerance is too tight/loose in practice.

Deferred from this item — see §6 below: **live golden-query harness
run** (`python -m tests.golden_harness`, needs `ANTHROPIC_API_KEY`,
unavailable in the current dev environment).

## 1. Item 2 — Sector filters & cross-sectional relative strength ✅ done 2026-07-05

The one structural change: a **cross-sectional pre-pass** computed over
the whole universe per date, cached like the benchmark. Everything else
hangs off it.

- [x] **`screener/cross_section.py`** — per-date universe-wide table:
      RS percentile of each stock (63-bar return rank, configurable
      window), equal-weight sector aggregate returns, sector momentum
      ranks. Computed lazily from panels, cached in-process keyed by
      (id(panels), as_of, window); no disk state. Pure function
      panels→DataFrame; deterministic; NaN-safe (thin-history symbols
      excluded from ranks via pandas' `rank(na_option="keep")`, never
      defaulted to 0th/100th percentile).
- [x] **DSL: `sector` condition** — `{"type":"sector","in":[…]}` matching
      the universe file's industry column (20 exact strings in
      `dsl.KNOWN_SECTORS`; validation rejects unknown names with the
      list of valid ones). `evaluate_symbol`/`run_screen` gained
      `symbol`/`sector_by_symbol` params (threaded through like
      benchmark; both default `None`, so existing call sites are
      unchanged).
- [x] **DSL: `rs_percentile` condition** —
      `{"type":"rs_percentile","window":63,"op":">=","value":80}`.
      Percentile among symbols with sufficient history on the as-of date.
- [x] **DSL: `sector_rank` condition** —
      `{"type":"sector_rank","window":63,"top":3}` (or `"bottom":3`, a
      small symmetric extension beyond the literal spec, needed for the
      lagging-sector preset — mutually exclusive, validated the same way
      as trend up/down). Equal-weight construction documented in
      TECHNICAL_DESIGN.md §6a (no cap weights available).
- [x] **Historical as-of correctness** — verified by a dedicated
      look-ahead test: two sectors whose relative performance flips
      between an early as-of date and "latest" rank in opposite order at
      each date (`TestCrossSection.test_no_lookahead`).
- [x] **Explainers** — `_ex_rs_percentile` (return, percentile, cutoff),
      `_ex_sector_rank` (sector, rank, top-3 leaders with returns),
      `_ex_sector` (stock's industry string).
- [x] **Parser vocabulary** — sector short-forms ("IT" → Information
      Technology, "pharma" → Healthcare, etc.), "RS above N", "market
      leaders"/"top sector", "lagging sector". Refuses when a sector
      adjective doesn't clearly match one of the 20 allowed strings.
- [x] **Presets** — `sector_leader_pullback` (top-3 sector +
      support_at_ma), `rs_leader_near_high` (RS≥80 near 52w high),
      `lagging_sector_bounce` (bottom-3 sector + hammer, clearly labelled
      contrarian). 17 presets total.
- [x] **Golden fixtures** — 4 new (IT-sector uptrend, RS-above-80 near
      52w high, market-leaders sector_rank, and an unknown-sector
      refusal). 14 → 18 fixtures.
- [x] **Tests** — synthetic 3-sector/9-symbol universe with engineered
      momentum dispersion + a thin-history symbol; 14 new tests
      (`TestCrossSection`, `TestSectorConditions`, plus 3 DSL validation
      tests). Suite: 50 → 64, all green except the 3 pre-existing
      local-environment failures noted in Item 0 (unrelated).
- [x] **Docs** — DSL table + new §6a in TECHNICAL_DESIGN.md, README vocab
      rows, changelog 0.6.
- [x] **Perf check** — measured on the live 500-symbol/5y store: a
      screen using `sector_rank`+`rs_percentile` (cold cache) was not
      measurably slower than a plain screen (both ~0.2s) — well under
      the 5s budget.

## 2. Item 3 — NSE bhavcopy migration (data layer v2) — build done 2026-07-05, evidence clock RESET 2026-07-20

Deliberately after Item 2. Run side-by-side with yfinance; cut over only
on evidence. **The original "2-week clock" (started 2026-07-05) never
actually accumulated 2 weeks of evidence** — `bhavcopy-update` is a
manual command and was run exactly once before the collection gap was
caught 2026-07-20 (real data sat frozen at 6 days, 06-25→07-03). Do not
trust a calendar-time claim about this evidence window without checking
`data/bhavcopy_prices.parquet`'s actual date range directly.

- [x] **`screener/bhavcopy.py`** — daily bhavcopy download (retry,
      weekend/holiday-aware via 404-as-skip), parse to the store schema,
      EQ series filter. Source turned out to be the single
      `sec_bhavdata_full_DDMMYYYY.csv` file (OHLCV + delivery % together
      — confirmed against a live fetch, not the originally-assumed
      separate UDiFF zip + delivery file). Acceptance met: one real
      day (2026-07-03) matched all 500 Nifty 500 yfinance closes to
      ~1e-6% (floating-point noise only).
- [x] **Delivery % ingestion** — `delivery_pct` column comes free from
      `sec_bhavdata_full`; present in `data/bhavcopy_prices.parquet`.
      Not yet threaded into the main indicator panels/DSL — deliberately
      deferred with the `delivery` condition below (only after cutover).
- [x] **Corporate-actions pipeline** — `fetch_corporate_actions()` (NSE's
      `/api/corporates-corporateActions`, needs a cookie warm-up GET
      first) + `parse_adjustment_factor()`, regexes built from real
      fetched subject lines ("Bonus X:Y", "Face Value Split ... From
      Rs.../ To Rs..."), dividends excluded by construction.
      `build_adjustment_factors()`/`apply_adjustments()` compound
      correctly backward in time (regression-tested — an oldest-first
      application order was caught as a bug during implementation and
      fixed before it shipped). CGCL's two 2024 actions (1:1 bonus ×
      2→1 split = 0.25 combined factor) reproduce the real jump ratio
      found in Item 0's jump-bar investigation almost exactly.
- [x] **Cross-source consistency check in `verify`** — `verify.
      check_cross_source`, folded into `python -m screener.cli verify`
      automatically. First real day of side-by-side data (2026-07-03,
      only one overlapping date so far): 45/3,000 bars over 0.5%,
      described as a "constant per-symbol gap" — but with one date per
      symbol that claim was untestable, not verified. **Corrected
      2026-07-20** after resuming `bhavcopy-update` (16 real trading
      days now overlapping, 7,986 bars): 382 over threshold across 39
      symbols, but the true shape is a smooth decay from 39 symbols on
      the oldest date to 0 on the most recent — the signature of
      yfinance's dividend-inclusive `auto_adjust` vs. bhavcopy's raw +
      splits/bonuses-only pipeline (§4a), **directly confirmed** by
      cross-referencing `fetch_corporate_actions()` (all 8
      largest-divergence symbols have a real dividend ex-date inside
      the window, lining up exactly with each one's decay pattern) —
      not a new problem, a known convention difference finally measured
      with enough real data to see its actual shape.
- [ ] **DSL: `delivery` condition** + vocabulary ("high delivery",
      "delivery spike") + preset ("accumulation: volume spike + delivery
      > 60%") — only after cutover.
- [ ] **Cutover** — config flag flips primary source; yfinance demoted
      to fallback; README/runbook updated. Blocked on a real
      continuous evidence window accumulating from the 2026-07-20
      restart (the reassuring dividend-decay finding above informs the
      eventual verdict but doesn't substitute for it) — **verdict is
      Rohit's call**, not to be made unilaterally on this analysis.
- [x] **Risk log** — NSE format changes are the known recurring hazard;
      keep parser tolerant and fail loud with the file snippet in the
      error. Written up in TECHNICAL_DESIGN.md §4a 2026-07-20: existing
      mitigations (loud-fail on non-404 fetch errors, `None`-not-guessed
      on unparseable corporate-action subject lines, full audit record
      of every action including unparsed ones) plus the still-open gap
      (no dedicated format-*change* alert — today a break would surface
      as a fetch exception or `verify`'s existing jump-bar smell test,
      not a purpose-built signal).
- [x] **Nightly cron** — `bhavcopy-update` is still a manual *command*
      (no code change needed to run it), but is now documented alongside
      `update` in the nightly cron line (README, TECHNICAL_DESIGN §11)
      — closing the exact gap that let evidence collection lapse
      silently for two weeks once already. Actually adding it to a real
      crontab is an operational step for whoever runs the nightly job,
      same as the rest of that line.

## 3. Screen backtesting — UNPARKED 2026-07-06 → spec in Item 14

Unpark criteria met: Items 1–2 done, and v0.10's allocation engine means
real capital is sized off these screens — historical edge is now the
blocking question. Full specification with acceptance criteria: Item 14.

## 4. Small backlog (slip in anywhere, one commit each) — cleared 2026-07-05

- [x] UI: "Recent screens" panel fed by `/api/log` (replay any past run
      with one click — spec + as-of restore). Caches the fetched log and
      refetches only when a new screen completes — an earlier version
      cached forever and went stale after the first run; caught and
      fixed via live browser testing (Playwright driver against the
      real server, not just unit tests).
- [x] UI: CSV export button on results (server already computes rows).
      Client-side generation from the already-fetched JSON — no
      dedicated export endpoint needed.
- [x] UI: near-miss toggle (hide/show) and match-count cap for huge
      result sets. Matches capped at 100 in the payload (`stats.matched`
      still reports the true total); a note explains the cap when active.
- [x] CLI: `screen --as-of YYYY-MM-DD` flag (parity with the UI picker).
- [x] `verify`: add screen-log integrity check (parseable JSONL,
      required keys present per line).
- [x] Preset ideas parking lot: **weekly squeeze** — reinterpreted as
      combining the *existing* weekly-trend + daily-`bb_squeeze`
      conditions (same pattern as the `weekly_up_daily_dip` preset)
      rather than adding Bollinger Bands to the weekly panel, which
      would have been a bigger change than a one-commit backlog item.
      **Gap-up follow-through** — added a new `gap` DSL condition
      (direction, min_gap_pct, lookback) + parser vocabulary + 1 golden
      fixture + preset (`gap_up_followthrough`: gap up + volume spike +
      uptrend). **Post-earnings drift** — struck, not implemented: it
      needs earnings-announcement dates, which is events/fundamentals
      data explicitly out of scope per TECHNICAL_DESIGN.md §1 ("no
      fundamentals, no intraday, no derivatives data... refuse anything
      outside this scope rather than approximate it"). Revisit only if
      that scope decision itself is revisited.

## 5. UI depth (v0.7 track) — done 2026-07-06

Ordered by daily-use value, not effort.

- [x] **Screen diff ("what changed since last run")** — `dsl.spec_hash()`
      (built on the same `canonicalize_conditions` the golden harness
      now shares, rather than duplicating it) hashes logic+conditions
      with as_of excluded; `/api/screen` looks up the last log entry
      with a matching hash and diffs matched-symbol sets, re-explaining
      each dropped symbol against current data. Badge: NEW tags on
      cards, collapsed "dropped since last run" list with the exact
      failing condition. 4 dedicated tests incl. hash stability under
      key order/default fill and as_of-independence.
- [x] **Full chart modal** — `_referenced_fields`/`_evidence_levels`
      factored out of the spark builder; `POST /api/chart` returns 250
      bars OHLCV lazily per symbol (not embedded in every match, to
      keep the main payload small). Hand-rolled SVG candlesticks +
      volume subpane + drag-to-zoom, zero external libs. Driven live via
      Playwright: candles, EMA overlay, volume, and zoom all confirmed
      rendering correctly against the real 500-symbol store.
- [x] **Watchlist with signal-decay tracking** — `data/watchlist.jsonl`
      (symbol, tagged date, close at tag, the full spec). `GET
      /api/watchlist` re-evaluates the tagged spec against *today's*
      data every time: current close, % move since tag, `still_holds`.
      Acceptance test passed as specified: tag BRKDWN before its
      engineered breakdown (`still_holds=True`), confirm it reads
      `still_holds=False` after — genuine signal-decay detection, not
      just a stored bookmark.
- [x] **Saved custom screens** — `data/user_presets.json`; validated via
      `dsl.validate()` at save time, same as built-ins. Frontend merges
      saved screens into the same `PRESETS` array the dropdown already
      renders (id prefixed `user:`), so the existing selection code
      needed zero changes. Rename/delete via a "manage my screens" panel.
- [x] **Multi-screen dashboard** — `/api/screen` refactored into a
      reusable `_run_screen(spec)` so `POST /api/screen_batch` could run
      N presets (built-in or saved) without duplicating the
      matching/diffing/logging logic. Grid: screen × (matched, top-3,
      new-since-last-run). Live-verified with 3 real presets in one call.
- [x] **Results table ergonomics** — client-side sort (return/RSI/
      price/symbol, asc or desc) and sector filter chips built from the
      current result set, applied without a server round-trip; sticky
      "Matches (N)…" header while scrolling. Near-misses intentionally
      left unsorted/unfiltered (secondary, usually shorter list).

All six driven live against the real server with a temporary
Playwright harness (screenshots + console-error checks), not just
unit-tested — same discipline as v0.6.2. 22 new tests, 137 total.

## 6. Robustness hardening (v0.7 track) — done 2026-07-05

- [x] **P0 — stale-server fix**: `_load_state()` now records the
      store's mtime and rebuilds (under the existing lock, clearing the
      cross-section cache too) whenever it changes — no more silently
      screening yesterday's data after a nightly `update`. Acceptance
      test passes: a monkeypatched store swap mid-session changes
      `as_of` without a restart.
- [x] **Data-quality badges on matches** — `flags: []` per match
      (`jump`, `thin_history`, `stale`), UI shows a small ⚠ with reason
      in a tooltip. Three new demo symbols (JUMPY/THINHIST/STALECO)
      exercise each flag; also live-verified against the real store,
      where it correctly flagged a genuine recently-listed Nifty 500
      name (CPPLUS) as `thin_history`.
- [x] **User config overrides** — `data/config_local.toml` overrides an
      allowlist (liquidity gate, staleness window, the 4 SR
      pivot/clustering constants — moved from `sr.py` into `config.py`
      so overrides actually take effect, `sr.py` keeps aliases for
      compatibility — spark bars, match cap). Unknown keys flagged and
      ignored. `config.config_hash()` logged with every screen-log entry
      and shown in the methodology footer.
- [x] **Parser resilience** — one retry on malformed JSON before giving
      up; failures (post-retry invalid JSON, or DSL validation failure)
      logged to `data/parse_failures.jsonl` — legitimate scope refusals
      are deliberately excluded. `parser.parse_with_assumptions()`
      returns the LLM's optional "assumptions" list; `/api/parse`
      surfaces it, UI renders "interpreted with defaults: …".
      `parser.parse()` stays a backward-compatible thin wrapper.
- [x] **`/api/health`** — mode, as-of, store mtime, panel count,
      benchmark presence, log writability, `git describe --dirty`,
      config hash. Live-curled and confirmed correct.
- [x] **Screen-log rotation** — past 5,000 lines, oldest entries move to
      `data/screen_log.rotated.jsonl`; `verify.check_screen_log` checks
      both files' JSONL integrity together.
- [x] **Golden harness in CI (manual)** — `.github/workflows/
      golden-harness.yml`, `workflow_dispatch` only, needs an
      `ANTHROPIC_API_KEY` repo secret.

All new UI surfaces (data-quality badge, config-hash footer,
assumptions display) driven live against the real server with a
temporary Playwright harness, not just unit-tested — same discipline as
Item 4. 23 new tests, 115 total.

## 9. Evidence-based strategy presets (v0.9) — literature-grounded filters — done 2026-07-06

Goal: every strategy preset traceable to named evidence, with honest
caveats. Deliverable order matters: the literature doc comes FIRST and the
presets implement it — not the reverse.

- [x] **LITERATURE.md** — the review document. One section per strategy
      family; for each: the canonical papers (full citations), the core
      finding, magnitude/robustness, India-specific evidence where it
      exists, known decay/cost caveats, and the exact DSL mapping chosen.
      The vetted family list (implement THESE, do not improvise new ones):
      1. *Cross-sectional momentum* — Jegadeesh & Titman (1993, JF);
         12-1 convention (skip the most recent month — short-term
         reversal, Jegadeesh 1990); Indian confirmation: Sehgal &
         Balakrishnan (2002), and NSE's own NIFTY200 Momentum 30 index
         methodology as practitioner corroboration. Caveat: momentum
         crashes (Daniel & Moskowitz 2016).
      2. *52-week-high anchoring* — George & Hwang (2004, JF): proximity
         to the 52w high predicts returns, distinct from momentum.
      3. *Time-series trend* — Moskowitz, Ooi & Pedersen (2012, JFE);
         Faber (2007) 10-month SMA rule ≈ 200 DMA regime filter.
      4. *MA rules* — Brock, Lakonishok & LeBaron (1992, JF); Han, Yang
         & Zhou (2013): MA timing strongest in high-volatility stocks.
         Honest caveat: BLL profits attenuated after costs in later
         samples (Sullivan, Timmermann & White 1999 data-snooping
         critique) — annotate, don't hide.
      5. *Volume-confirmed momentum* — Lee & Swaminathan (2000, JF):
         momentum interacts with turnover.
      6. *Low-volatility* — Blitz & van Vliet (2007), Ang et al (2006):
         low-vol stocks earn superior risk-adjusted returns; the
         defensive bucket.
      7. *Practitioner trend template* — Minervini/O'Neil stage-2
         criteria; label explicitly as practitioner (weak academic
         support, strong survivor-bias risk in its folklore).
      8. *Consolidation breakouts* — existing flat_base; academic
         evidence weak → labelled "practitioner, unvalidated".
- [x] **Indicator engine additions** — `roc_126`, `roc_252`, `mom_12_1`
      (close.shift(21)/close.shift(252) − 1, i.e. return t−252 → t−21),
      `sma_150` + a slope field for every SMA period (needed for
      "sma_200 rising"), cross-sectional `atr_pct` percentile added to
      the pre-pass alongside RS percentile (ranked ascending: 0 = least
      volatile). `rs_percentile` gained an optional `basis`
      ("return"|"mom_12_1") rather than adding a separate condition type.
- [x] **Preset schema extension** — each preset gains an `evidence`
      object: {basis: "academic"|"practitioner"|"mixed", sources: […],
      finding: str, caveat: str}. UI dropdown shows basis as a small
      color-coded tag; the description panel shows finding + caveat +
      sources BEFORE the user runs it (live-verified via Playwright
      against both an "academic" and a "practitioner" preset). All 19
      pre-existing presets annotated retroactively — several honestly
      say "no dedicated academic study reviewed" rather than a
      manufactured citation. 26 presets total.
- [x] **New strategy presets (7)** — momentum_12_1_leaders, near_52w_
      high_ghw, tsmom_regime, ma_timing_highvol, volume_momentum,
      lowvol_defensive, minervini_stage2, exactly as specced above. Each
      run against the live 500-symbol store (match counts 36-202,
      none 0 or 500) in addition to the engineered synthetic tests below.
- [x] **Golden fixtures + parser vocab** — 4 new fixtures ("12-1
      momentum leaders", "low volatility stocks", "volatile stocks in an
      uptrend", "stage 2 setups" — the full 7-condition conjunction).
      19 → 23 fixtures.
- [x] **Tests** — 20 new: `mom_12_1`'s skip-month construction verified
      against hand-computed values and proven to rank differently from a
      raw-return basis on an engineered spike-in-the-excluded-month case;
      a 4-symbol volatility-dispersion universe (identical drift, only
      `band` intraday-range differs) cleanly separates `atr_pct_
      percentile` into quartiles; each of the 7 new presets checked
      against an engineered match + an engineered rejection case; every
      preset's evidence object schema-checked. 137 → 157 tests. Docs:
      TECHNICAL_DESIGN.md §5/§6a/new §12c + changelog; README test/preset
      counts and vocab table.

## 10. Portfolio allocation engine (v0.10) — done 2026-07-06

Turns a result set + capital + risk tolerance into integer-share position
sizes. It is a *sizing calculator with documented methodology*, not a
recommendation engine — that framing appears in the UI, the docs, and the
API response.

**Methodology (decided now, implement as specced):**
- [x] **Core: fixed-fractional risk sizing** (Van Tharp / Turtle-style):
      per-position risk = capital × risk_per_trade_pct (UI risk presets:
      conservative 0.5% / moderate 1% / aggressive 2%); stop distance =
      2×ATR(14) below entry (consistent with the momentum system's hybrid
      stop); shares = floor(risk ÷ stop_distance); position value capped
      at max_position_pct (default 15%) of capital.
- [x] **Alternative mode: inverse-volatility weights** (naive risk
      parity) over the selected names, same caps.
- [x] **Always-shown baseline: equal weight** — DeMiguel, Garlappi &
      Uppal (2009): 1/N is the honest benchmark no optimiser reliably
      beats out-of-sample on estimated inputs. Returned as a `baseline`
      key alongside risk/inverse_vol results (omitted when the caller
      already requested equal, to avoid a redundant duplicate).
- [x] **Constraints**: max_positions (default 10, caller-ranked — the UI
      uses the current results sort), sector cap (default ≤30% of
      capital per industry), integer shares, min ticket ₹5k (skip
      smaller), explicit cash residual line, **plus an aggregate
      capital cap** — not in the original spec, added after live testing
      found individually-compliant positions summing past 100% of
      capital (see below).
- [x] **Explicit non-goals, documented with reasons in the design doc**:
      NO mean-variance optimisation (estimation error dominates on
      screened subsets — DeMiguel et al), NO Kelly (drawdown profile
      unsuitable for discretionary use), NO return forecasts, NO
      auto-execution. Refusing these is a feature.
- [x] **`screener/allocate.py`** — pure function: (ranked symbols, panels,
      universe, capital, params) → allocation table (symbol, sector,
      entry, shares, value ₹, % of capital, stop level, risk ₹,
      rationale) + summary (deployed, cash, portfolio risk if all stops
      hit, largest sector, n_positions) + excluded-with-reasons list.
      NaN-ATR names excluded with a stated reason, never sized blind.
- [x] **Evidence-trail parity** — per-position sizing rationale string
      ("risk ₹1,000 ÷ stop distance ₹59.65 = 13 shares = ₹14,424
      (14.4%)") in the same ledger style as screen evidence.
- [x] **API + UI** — `POST /api/allocate`; results page gained a
      "💰 allocate" panel (capital input, risk preset, method toggle,
      constraint fields) → position table + equal-weight baseline table
      + CSV export; logged to a new `data/allocation_log.jsonl` (spec
      hash if the originating screen was passed + every sizing param +
      the table) — a sibling to screen_log.jsonl, kept separate since
      the schemas genuinely differ. Live-verified via Playwright against
      the real 500-symbol store (risk method, equal method, custom risk
      preset toggle, CSV export, panel reset on re-run) — this is what
      caught the aggregate-capital-cap bug below; screenshots clean, zero
      console errors.
- [x] **Bug found via live testing, fixed before shipping**: `risk`
      sizes each position independently off the risk budget with no
      built-in awareness of the running total deployed — nine
      individually-15%-capped, distinct-sector positions summed to
      ₹104,193 against a ₹100,000 input in a real screen. Fixed by
      tracking `capital_deployed` the same way `sector_deployed` already
      was; a dedicated regression test (`TestAggregateCapitalCap`)
      reproduces it with 9 synthetic same-drift/distinct-sector symbols
      — the original 4-symbol tests hadn't summed past capital by
      chance, which is why it shipped past unit tests first.
- [x] **Tests (21)** — invariants: Σvalue ≤ capital (incl. the regression
      case above); per-position risk ≤ specified (integer rounding only
      downward); max-position-pct and sector cap enforced; 0-match,
      all-missing-panel, NaN-ATR, tiny-capital degenerate cases; equal
      weight vs risk-sized divergence and inverse-vol favoring calmer
      names on engineered vol dispersion; baseline presence/absence;
      stop-level/risk consistency. 182 tests total.
- [x] **Disclaimer discipline** — allocation responses carry the
      not-investment-advice note (`result["disclaimer"]`); README states
      the scope plainly.

## 11. UI professional redesign (v0.11) — shipped in part 2026-07-06

Keep the audit-desk identity (ink navy, amber, evidence ledgers) — this is
a refinement, not a reskin. Elevate craft, don't chase trends.

- [x] **Split the monolith** — index.html (~960 lines) → served static
      web/app.css, web/app.js, index.html (~85 lines); three new
      `@app.get` routes, no build step, no framework. Verified
      byte-for-byte non-regressive: before/after screenshots pixel-
      identical.
- [x] **Design tokens pass** — every spacing/font-size/radius/state
      value named and documented at the top of app.css. Deliberately
      value-preserving rather than lossily consolidating: the existing
      non-4px spacing values and the ~9-size sans scale were kept as
      named tokens (not force-rounded to "3 mono + 2 sans") since they
      carry real, load-bearing hierarchy across 10+ surfaces —
      collapsing them would have been an unreviewed visual regression
      disguised as a refactor. Mono scale gained one size (12.5/14/
      16/17px) for the match symbol. Rendered output confirmed
      pixel-identical to pre-tokens via screenshot comparison.
- [ ] **Layout architecture** (persistent sidebar + main canvas) —
      **explicitly deferred by decision** (not struck — still planned),
      after weighing it as the highest-risk, highest-effort remaining
      piece (touches every panel: results, stats, watchlist, dashboard,
      allocate, recent screens) against everything else already shipped
      this cycle. Revisit as its own scoped effort.
- [x] **Component polish** (partial, by the same reasoning as above —
      toasts were the highest-value, lowest-risk piece; skeleton
      loaders/iconography/card-hierarchy passes deferred alongside the
      layout work since they're most naturally done together with it):
      a single reused toast (`role="status"`, `aria-live="polite"`) for
      previously-silent actions — watchlist add/remove, save/rename/
      delete a custom screen, CSV export.
- [x] **Accessibility floor** — match cards, recent-run rows, and sector
      chips gained `tabindex`/ARIA roles/keyboard activation (a shared
      `onCardKey()` handler); chart modal gained `role="dialog"`, focus
      moved to its close button on open and restored to the trigger on
      close, Escape-to-close; contrast computed via the WCAG
      relative-luminance formula for every token pair — all already
      clear 4.5:1, no color changes needed. define→run→expand→allocate
      verified fully keyboard-operable via a Playwright script driving
      Tab/Enter/Escape.
- [x] **Report/print mode** — `@media print` redefines the token
      palette's colors for a light ink-on-paper scheme (every component
      already drawing from the tokens adapts for free); interactive
      chrome hidden, match evidence forced open, the allocate panel's
      *results* stay visible (only its input controls hide) so a sized
      allocation prints too, not just the screen. Triggered by a
      "🖨 print report" button. Live-verified: results/controls
      correctly shown/hidden under `page.emulateMedia({media:'print'})`.
- [x] **Visual regression baseline** — `web/visual/` (`@playwright/
      test`, its own package.json, not wired into CI — needs a live
      server, same reason as the golden harness): 6 committed baseline
      screenshots (define, results, modal, dashboard, allocate,
      watchlist). A new `SCREENER_FORCE_DEMO=1` env var
      (`webapp._demo_forced()`) boots demo mode for deterministic
      baselines regardless of a local real store. Verified idempotent
      across repeated runs.

## 14. Screen backtester (v0.12) — event-study engine — done 2026-07-06

**The question it answers**: for any DSL spec, what happened after this
signal fired historically — versus just holding the universe? It is an
*edge detector for filters*, NOT a portfolio simulator: no position
sizing, no compounding, no stops, no execution modelling. (That is the
separate momentum backtester's job; this tool decides which signals
deserve that treatment.)

### Methodology — decided now, implement as specced

- [x] **Event definition** — signal(sym, t) = spec evaluates True at bar
      t. Entry event = signal True at t AND no event for that symbol in
      the prior `cooldown` bars (default 20, configurable). Cooldown
      de-duplicates the "signal stays true for 8 consecutive days"
      problem; without it, event counts inflate and return samples are
      near-duplicates.
- [x] **Entry price convention** — entry at the **open of t+1** (signal
      is computed on bar-t close; assuming close-t entry is look-ahead).
      Forward return at horizon h = close[t+h] / open[t+1] − 1, horizons
      h ∈ {5, 20, 60} by default (caller-configurable). Events within h
      bars of the panel's end are excluded from that horizon only
      (never NaN-polluted into stats — and never a raw JSON `nan`
      either, see the bug note below).
- [x] **Baseline (the part most home-built backtests get wrong)** — for
      each event date, the equal-weight mean forward return over the
      SAME horizon of ALL liquidity-passing universe symbols on that
      date. Excess = event return − same-date baseline. Same-date
      universe baseline is primary (controls for market regime at
      signal time); a separate vs-Nifty comparison was scoped out as
      redundant with this — hand-verified in tests with a 3-symbol
      closed-form universe (`TestBaseline`).
- [x] **Costs** — round-trip haircut applied to net figures (default
      0.30% for NSE cash delivery incl. STT/impact at retail size;
      configurable). Gross AND net always shown side by side.
- [x] **Statistics honesty** — report per-horizon: event count, mean,
      median, hit rate (>0 excess), p5/p95, worst-5%-mean. Overlapping
      same-date events across symbols are cross-correlated → also report
      the **event-date portfolio** series (equal-weight excess per
      signal date) and base any dispersion/CI claims on date-level
      block bootstrap (1k resamples, seeded — reproducible), never
      pooled-event t-stats. If event count at a horizon < 30, print
      "insufficient events" instead of stats.
- [x] **Pre-registered hypothesis field** — the run takes an optional
      free-text `hypothesis` ("I expect +1-2% 20-bar excess, hit rate
      ~55%") logged with the result (API response + `backtest_log.jsonl`
      + CLI `--hypothesis`). UI nudges but doesn't force.
- [x] **Sensitivity grid** — perturb the spec's numeric parameters
      (auto-detected: tolerance_pct, min_ratio, value/value_pct,
      max_range_pct, buffer_pct, min_gap_pct, percentile, and generic
      range.min/max — covers rsi/adx bounds and percentile cutoffs) over
      ±2 steps each, one parameter at a time (no full cartesian). Grid
      cells: event count + 20-bar mean excess (net). Verdict: "robust
      across range" if all four variants keep the base run's sign and
      stay ≥25% of its magnitude, else "edge concentrated at one value —
      treat as curve-fit". Costs roughly (1 + 4×n_params)× the core
      runtime since it fully reruns the signal path per variant —
      documented in TECHNICAL_DESIGN.md, exposed as a UI/API/CLI toggle
      so it isn't forced on for slow specs.
- [x] **Survivorship caveat** — current-constituent universe flatters
      dip-buying setups (the ones that died aren't here). Printed on
      EVERY report, API response, CLI report, and CSV-adjacent UI
      panel — not a docs footnote. Numbers are for *ranking filters
      against each other*, not for absolute return expectations.

### Engineering

- [x] **`screener/backtest.py`** — pure functions: (panels, universe,
      spec, params) → events + results dict. No I/O in the core
      (logging lives in webapp.py, same pattern as allocate.py).
- [x] **Vectorized signal path** — per-condition vectorizers producing
      boolean Series over the whole panel (compare/range/trend/change/
      cross/proximity/support_at_ma/volume_spike/gap/tight_range/
      flat_base/candle/rel_strength/sector/weekly-timeframe vectorize
      exactly; the expensive set — near_support, near_resistance,
      breakout_resistance, bb_squeeze, rs_percentile, sector_rank,
      atr_pct_percentile — computes on a `stride`-bar date grid,
      forward-filled between samples, approximation documented).
      **CRITICAL acceptance test implemented**
      (`verify_vectorizer_consistency`): exact match against
      `evaluate_symbol()` at every sampled date for cheap-only specs;
      at stride-grid dates only for specs touching an expensive type
      (that's the actual guarantee the stride approximation makes, so
      that's what's tested — see TECHNICAL_DESIGN.md).
- [x] **Cross-sectional history** — done via a **simpler path than
      specced**: rather than a new vectorized date-indexed rank
      rebuild in cross_section.py, the existing single-date
      `build_cross_section()` is called once per stride-grid date (not
      once per bar) and results are forward-filled the same way as the
      expensive per-symbol types. Measured fast enough in practice
      (~30-40s for 500 symbols/5y at one window) that the heavier
      vectorized-matrix rewrite wasn't needed — revisit only if a
      future spec needs multiple distinct windows at once and this
      path becomes the bottleneck.
- [x] **Perf gate** — measured live against the real 500-symbol/5y
      store, no sensitivity grid: a cheap-conditions-only spec ~13-19s
      (well under the 60s target); near_support ~91s, bb_squeeze ~34s,
      rs_percentile ~37s (all under the 3min target) — **at
      `stride=20`, not the originally-planned `stride=5`**. stride=5
      measured at ~6-15 minutes for the expensive types — infeasible —
      so the shipped default was raised and documented honestly rather
      than claiming a number that didn't hold up under measurement.
      The sensitivity grid multiplies this by roughly (1 + 4×n_params)
      since it reruns the full signal path per variant; see the
      Methodology section above.
- [x] **API + UI** — `POST /api/backtest` (spec + params + hypothesis) →
      results; UI "🧪 backtest" panel: summary table per horizon
      (gross/net vs. same-date baseline, hit rate, p5/p95, worst-5%),
      excess-return histogram vs. baseline, event-timeline bar chart
      (events per month), sensitivity table with the verdict line,
      survivorship banner, CSV export of the per-event table. Runs
      logged (spec hash + params + hypothesis + per-horizon summary) to
      a new `data/backtest_log.jsonl`, sibling to screen/allocation
      logs. Live-verified via Playwright against the real store: zero
      console errors, correct rendering (cheap spec, sensitivity grid
      on and off), print-mode results visibility.
- [x] **CLI** — `backtest "<query>" [--preset id] [--json] [--horizons
      ...] [--cooldown N] [--cost-pct X] [--stride N] [--min-events N]
      [--hypothesis "..."] [--no-sensitivity]` with a readable text
      report; verified against the real 500-symbol store.
- [ ] **Preset evidence loop-closure** — explicitly deferred, not
      struck: adding each of the 26 presets' own backtest summary to
      its evidence object is a distinct, sizeable follow-up (running
      and curating 26 backtests, deciding a consistent robust/fragile
      wording) rather than a natural extension of shipping the engine
      itself. Revisit as its own scoped effort.

### Tests (27 new — ≥15 target exceeded)

- [x] Engineered edge: universe where signal (volume spike) precedes a
      +5% drift → positive excess found; same construction with the
      trigger shuffled onto unrelated dates → excess ≈ 0 (the null
      works). `TestEngineeredEdgeAndNull`.
- [x] Dedup: 8-consecutive-day signal → exactly 1 event; signal
      recurring after cooldown → 2 events; recurrence *within* cooldown
      → still 1 event. `TestDedupEvents`.
- [x] Entry convention: hand-computed forward return from open[t+1]
      matches; event at panel-end excluded from long horizons only, not
      dropped outright. `TestEntryConvention`.
- [x] Baseline: 3-symbol universe, hand-computed same-date baseline
      (closed-form constant-drift series). `TestBaseline`.
- [x] Vectorizer≡evaluator consistency — cheap-only, expensive-symbol,
      expensive-cross-sectional, and weekly-timeframe specs, each
      against the real evaluator function. `TestVectorizerConsistency`.
- [x] Cost arithmetic: net = gross − round-trip on both sides.
      `TestCosts`.
- [x] <30 events → stats suppressed; bootstrap reproducible via seed
      (same seed ⇒ identical CI, different seed ⇒ different CI).
      `TestInsufficientEvents`, `TestBootstrapDeterminism`.
- [x] Liquidity filtering excludes an illiquid symbol from both events
      and the baseline pool. `TestLiquidityFiltering`.
- [x] Sensitivity grid structure/verdict; hypothesis/survivorship
      metadata always present; event-timeline counts sum to the total.
      `TestSensitivityGrid`, `TestMetadata`, `TestEventTimeline`.
- [x] **Bug found via live testing, fixed before shipping (JSON)**: the
      first real `/api/backtest` call 500'd — `json.dumps` rejects a
      raw Python `nan`, which a horizon-truncated event's row legitimately
      contains. Fixed with an explicit NaN→`None` pass over the events
      table before it leaves `backtest_spec`; regression test
      `TestJSONSerialization` round-trips a truncated-event result
      through `json.dumps`.
- [x] **Bug found via live testing, fixed before shipping (perf)**:
      `near_support`/`breakout_resistance`/`bb_squeeze` at the
      originally-planned `stride=5` took 6-15 minutes for a single
      condition over the real store — see the Perf gate bullet above.
      `stride=20` became the shipped default after live measurement.
- [x] **Pre-existing bug found via this item's own test suite, fixed**:
      `cross_section.py`'s in-process cache keys on `id(panels)` alone
      (a risk its own docstring already flagged). This item's tests
      build and discard many short-lived synthetic `panels` dicts,
      which started intermittently corrupting unrelated preset tests
      elsewhere in the same pytest run via CPython id-reuse after
      garbage collection. Fixed by adding `frozenset(panels)` (the
      symbol set) to the cache key; regression test
      `test_cache_key_not_fooled_by_id_reuse` plants a stale entry
      under the real id() with a different symbol set and confirms the
      genuine computation wins.
- [x] 4 webapp-level `/api/backtest` contract tests (200 shape, 422 on
      an empty spec, 422 on empty horizons, log-file write). Tests:
      185 → 212 (22 in `tests/test_backtest.py` including the JSON
      regression test above, 4 in `TestBacktestEndpoint`, 1
      cross-section cache-safety regression test).

## 15. Equity depth track (v0.13) — universe expansion within equities

~~Multi-asset expansion (FX Phase B, crypto Phase C)~~ — **descoped
2026-07-07**: equity only for now; other asset classes get separate
engines later rather than field-mask/calendar abstractions here. This
removes the field-mask, calendar, and bars_per_year workstreams entirely;
the freed budget goes to equity depth. Cross-universe screens remain
deferred. US equities / MCX / F&O deferrals stand as recorded.

### A. Equity universe registry (registry-lite) — done 2026-07-10

All universes share NSE calendar, INR, and the full field set — so the
registry holds only: {id, name, benchmark, liquidity_gate,
survivorship_note}. No asset-class branching anywhere. Landed in three
slices, all 2026-07-10 except the first: a foundation refactor
(2026-07-09, zero behaviour change, `nifty500` only), onboarding a real
second universe (`nse_full`) once the foundation was proven, and a
same-day follow-ups batch (`nse_etf`, the sector-data-gap warning,
preset `universes` tags) once the sequencing note flagged it as
unblocked and actionable.

- [x] `screener/universes.py` — registry with **three** universes:
      `nifty500` (existing), `nse_full` (all NSE EQ-series symbols,
      2,047 per NSE's own equity listing — vs. nifty500's ~500), and
      `nse_etf` (36 curated broad domestic equity-index ETFs — see the
      dedicated bullet below). `liquidity_gate_cr` is the first field
      that earned its place on `Universe`: nse_full's much longer tail
      of thin names needs a stricter ₹2cr floor vs. nifty500's ₹0.5cr,
      and nse_etf needs a looser ₹0.1cr floor (ETF unit turnover runs
      lower even for large, legitimate funds). `sector_enabled` is
      still not a field — NSE's raw listing carries no sector/industry
      classification (an index-methodology concept, not a raw-listing
      one), so `sector`/`sector_rank` conditions simply find nothing to
      match for nse_full/nse_etf; the existing NaN-industry handling
      already degrades gracefully (now paired with the sector-data-gap
      warning below so this isn't a silent zero-match), no registry
      flag needed.
- [x] Per-universe storage `data/{universe_id}/…` (prices.parquet,
      universe.csv, benchmark.parquet). `config.py` gained
      `price_store()`/`universe_file()`/`benchmark_store()` functions
      (default-universe case still resolves through the existing
      `PRICE_STORE`/`UNIVERSE_FILE`/`BENCHMARK_STORE` attributes so
      `monkeypatch.setattr(config, "PRICE_STORE", …)`-style test
      fixtures kept working unchanged), plus `config.liquidity_gate_cr
      (universe_id)` with the same deferral pattern — nifty500 still
      honours a `config_local.toml` override of `MIN_MEDIAN_TURNOVER_CR`,
      nse_full uses its own fixed registry value. An idempotent
      `_migrate_legacy_nifty500_layout()` runs at import time and moved
      the real dev-machine store from flat `data/` into `data/nifty500/`
      on first run — verified against the actual 500-symbol store, not
      just synthetic data. Screen-log entries gain a `universe` field;
      old entries without it default to `nifty500` on read
      (`GET /api/log`), not backfilled in place.
- [x] `--universe` on `backfill`/`update`/`verify`/`screen`/`backtest`
      (argparse `choices` gives free validation + a helpful error) —
      `python -m screener.cli backfill --universe nse_full` ran the real
      2,047-symbol backfill (2,065,698 rows, 2021-07-12 → 2026-07-10, via
      the existing yfinance pipeline — no new adjustment-correctness
      code, just a bigger symbol list; took ~15 minutes live, well under
      the originally-estimated 30-60 given yfinance's chunked download
      held up fine at this scale).
- [x] **Webapp universe selector** — `GET /api/universes` (registered
      universes + which is active), `POST /api/universe` (switches a
      single process-wide active universe — this is a local, single-user
      tool, not multi-tenant, so a per-request universe field on every
      endpoint would be unused generality; every existing endpoint
      already reads through `_load_state(_ACTIVE_UNIVERSE)`). A header
      `<select>` in the UI, hidden when fewer than 2 universes are
      registered. Switching resets any in-progress screen/allocation/
      backtest (a prior universe's results no longer apply). First load
      of a not-yet-cached universe in a server session is a real,
      honestly-messaged cost (nse_full: ~165s to build 1,956 panels
      cold) — cached per-universe after that, confirmed instant on
      repeat switches via live testing.
- [x] **Memory gate (hard) — PASSES, no architecture change needed**:
      measured live with both universes' panels resident simultaneously
      (500 nifty500 + 1,956 nse_full, 91 nse_full symbols dropped by the
      existing 60-bar-minimum filter) — peak RSS 782 MB, comfortably
      under the 4 GB target. The on-demand LRU panel-build fallback the
      spec allowed for was not needed.
- [x] **Bug found via live testing, fixed before shipping**: the
      backtest survivorship caveat was hardcoded to `backtest.py`'s own
      nifty500-worded constant regardless of which universe was actually
      active — an nse_full backtest printed a caveat claiming "Nifty 500
      constituent list," which is simply wrong for a 2,047-symbol
      universe with even heavier churn. Fixed by threading
      `universes.get(universe_id).survivorship_note` through
      `backtest_spec()`'s new `survivorship_note` parameter (CLI and
      webapp both pass it; the module constant remains the default for
      any caller that doesn't), with a regression test asserting the
      webapp backtest endpoint's caveat text matches the active universe.
- [x] **Zero nifty500 behaviour change, verified**: full suite green
      (252 tests — 225 after the foundation slice, 11 for nse_full/the
      selector/the survivorship fix, 16 more for the follow-ups batch),
      and live-checked against the real 500-symbol store —
      `screen`/`backtest` CLI commands and the webapp both produce
      identical results after migration as before it (same match
      counts, same `as_of`, `panel_count: 500`).
- [x] **Sector-data gap warning (from v0.13.0 review) — done 2026-07-10**:
      `evaluator.sector_data_gap_warning(screen, universe)` — `None` if
      the spec doesn't use `sector`/`sector_rank`, or the universe has
      any non-null `industry` value; otherwise a warning string. Wired
      into `/api/screen` and `/api/backtest` responses (a `warnings`
      list, rendered as a banner in the UI) and both CLI commands
      (printed before the run). Live-verified: a `sector_rank` screen
      against the real `nse_full` store now prints the warning and an
      honest "0 matches," instead of a silent, confusing zero.
- [x] **`nse_etf` onboarded — done 2026-07-10**: a *curated* list of 36
      broad domestic equity-index ETFs (NIFTYBEES, BANKBEES, NIF100BEES,
      etc.), not an automatic fetch-and-classify — NSE's own ETF listing
      (`eq_etfseclist.csv`) has an `Underlying` column too inconsistent
      to classify reliably by keyword (fund names leak into what should
      be index names for a large share of its ~330 rows; checked live
      before deciding this, the same "verify the real data before
      estimating scope" lesson as `nse_full`'s bhavcopy pivot). Each
      curated symbol was cross-checked against a live fetch and tracks a
      well-known broad index; gold/silver/commodity/debt/international-
      index/money-market ETFs excluded per "equity-index ETFs only."
      Real backfill run: 36 symbols, 35,905 rows — under a minute live.
      `liquidity_gate_cr=0.1` (ETF unit turnover runs lower than
      growth-stock turnover even for large, legitimate funds).
- [x] **Preset `universes` tags — done 2026-07-10**: computed from each
      preset's spec (does it use `sector`/`sector_rank`?) at import time
      in `presets.py`, not hand-maintained per preset — a new preset is
      tagged correctly automatically. Today: 2 of 26 presets
      (`sector_leader_pullback`, `lagging_sector_bounce`) are tagged
      `["nifty500"]` only; the rest are tagged for every registered
      universe. `GET /api/presets` exposes the field; the UI preset
      dropdown filters to it and rebuilds on every universe switch —
      live-verified via Playwright (the two sector presets disappear on
      `nse_full`/`nse_etf`, reappear on switching back).
- [ ] **Not built this pass**: the fuller original registry schema
      (`symbol_source` as a stored callable rather than `universe.py`'s
      internal `_FETCHERS` dispatch dict).

### B. Survivorship mitigation — point-in-time index membership

The single biggest robustness upgrade available to the backtester. NSE
publishes semi-annual index reconstitution changes; build a membership
history file (symbol, entry_date, exit_date) for Nifty 500.

- [ ] `data/nifty500/membership.csv` + ingestion helper — sources: NSE
      index press releases / historical constituent archives; accept
      that coverage starts wherever records allow (target: full 5y
      window; document actual coverage achieved).
- [ ] Backtester consumes it: a symbol is eligible for events only
      between entry_date and exit_date — kills the "2025 IPO appears in
      2022 screens" inclusion bias.
- [ ] **Honest limitation, stated in the survivorship note**: delisted/
      dropped names whose PRICE DATA we lack still can't contribute
      losing events — membership dates fix inclusion bias, not data
      absence. The note quantifies it: "N symbols entered/exited the
      index in the window; price history exists for M of them."
- [ ] Screener (as-of replay) gets the same eligibility filter behind a
      flag (default on for backtests, on for historical as-of screens,
      irrelevant for latest).

### C. Equity-native conditions (post-bhavcopy-cutover + breadth)

- [ ] Bhavcopy cutover completes per Item 2 (clock: started 2026-07-05,
      eligible ~2026-07-19) — prerequisite for nse_full symbol list and
      delivery data.
- [ ] `delivery` DSL condition + vocabulary + accumulation preset
      (volume spike + delivery ≥ 60%) — the Item 2 deferred task, lands
      here.
- [x] **Market breadth regime fields** — DONE 2026-07-12, buildable
      independently of the bhavcopy cutover (confirmed: uses the
      existing per-symbol SMA200/high series already computed for every
      registered universe, no bhavcopy/delivery data needed). Computed
      from the universe itself into the cross-sectional pre-pass:
      `pct_above_200dma`, `pct_at_20d_high`; new `breadth` condition
      ("market breadth positive" → pct_above_200dma ≥ 50), wired through
      screening, evidence, and the backtester's vectorized path
      (`compute_breadth_series`/`_vec_breadth`, verified exact against
      the row-by-row evaluator — not stride-approximated). Regime
      context for every equity screen without external data. Golden
      fixture + LITERATURE.md §9 annotation (practitioner basis, regime
      qualifier, weak alone — no fabricated citation for the exact
      two-field construction). 11 new tests.
- [ ] Backtest loop: rerun the strategy-preset evidence closure on
      nse_full vs nifty500 — does the edge strengthen in the broader,
      less-efficient universe (the momentum literature says it should)
      or was it a large-cap artifact? That comparison is the payoff of
      this whole item. **Blocked on Rohit's pre-registered hypotheses**
      per preset per universe (the whole point of pre-registering is
      writing the prediction down before running the analysis) —
      deliberately not run yet; breadth fields above were scoped and
      shipped separately so this gate doesn't block the tooling.

## 16. Cohort tracker (v0.14) — out-of-sample filter validation

Walk-forward complement to the Item-14 backtester: freeze a cohort of
matches at signal time, track forward at the SAME horizons/baseline/
conventions, aggregate per spec into an IS-vs-OOS scorecard. Forward
cohorts are survivorship-free by construction — the one bias the
backtester cannot fully remove. This is a *filter validator*, not a
trade tracker: no fills, no partial exits, no P&L accounting.

### Data model & conventions (decided; implement as specced)

- [x] **Cohort record** (data/{universe}/cohorts.jsonl): {cohort_id,
      created_ts, universe, spec (full) + spec_hash, symbols[], weights
      (equal | allocation payload w/ method+params), entry_date (first
      trading day AFTER creation), status: pending|active|completed,
      notes}. **Dates frozen, never prices** — the store is
      split/bonus-adjusted and adjustment rewrites history; entry price
      is always recomputed as open[entry_date] from the current series.
      A test simulates a retroactive adjustment (halve the series) and
      asserts returns are invariant.
- [x] **Entry convention** — open of entry_date, identical to the
      backtester. Day-0 = pending: no returns displayed, ever, until
      the entry bar exists.
- [x] **Milestone snapshots** — at 5/20/60 bars post-entry, per-symbol
      and cohort-aggregate metrics freeze permanently into the record
      (close[entry+h]/open[entry]−1, gross and net at the universe's
      cost default); a live "current" row keeps drifting for active
      view only. At 60 bars → completed (archived, still in scorecard).
- [x] **Baseline parity** — same-date equal-weight universe forward
      return (liquidity-passing set as of entry_date), computed by the
      SAME code path as the backtester baseline (refactor to a shared
      helper; no reimplementation). Nifty secondary. Excess is the
      headline number everywhere.
- [x] **Symbol lifecycle** — stale/suspended symbol (last bar < store
      latest): flag on the row, carry last available close, cohort
      aggregate notes "N of M symbols stale". Never silently dropped —
      dropping losers is exactly the bias this tool exists to avoid.

### Scorecard (the payoff)

- [x] **Per-spec aggregation** — group cohorts by spec_hash: cohorts n,
      total names, per-horizon mean/median excess (net), hit rate,
      side-by-side with the backtester's IS numbers for the same
      spec_hash (looked up from logged backtest runs). Footnote fixed:
      "IS is survivorship-flattered; OOS is small-sample."
- [x] **Small-N honesty** — < 20 names at a horizon → "insufficient
      sample", no mean printed. No significance claims at any N; this
      is evidence accumulation, not hypothesis testing.
- [x] **Preset evidence loop** — completed-cohort OOS summaries feed
      the same evidence objects the deferred backtest loop-closure
      targets; one mechanism, two sources, clearly labelled IS vs OOS.

### Surfaces

- [x] **UI** — results view: "Track these matches" (implemented as
      track-all, the spec's explicitly named fallback to a per-row
      checkbox subset — the results view has no existing multi-select
      affordance to hang a subset picker off, and every match is
      already a deliberate output of the screen); allocation view:
      "Track this portfolio" (weights derived from position values).
      Cohorts tab: active list (age, current net, next milestone),
      cohort detail (per-symbol table + click-to-chart with an
      entry-date marker line, reusing the existing full-chart modal
      instead of a second small-sparkline renderer), spec scorecard
      view. Survivorship-free note on the scorecard and cohort detail.
- [x] **API** — POST /api/cohorts (create), GET /api/cohorts[?spec_hash],
      GET /api/cohorts/{id}, GET /api/scorecard/{spec_hash}. Cohort
      creation's replay discipline is the cohort record itself
      (permanent, spec+spec_hash+symbols+weights+notes+created_ts,
      never rotated) rather than a duplicate entry in screen_log.jsonl,
      whose entry shape (as_of/stats/matched) doesn't fit a cohort and
      whose 5000-line rotation would eventually drop it.
- [x] **CLI** — `cohort create --from-last-screen [--symbols ...]`,
      `cohort list`, `cohort show <id>`, `scorecard <spec_hash|preset>`.
      Nightly refresh piggybacks `update` (milestones are computed
      lazily from dates, so "refresh" is just viewing — no cron state).

### Tests (≥14)

- [x] Adjustment invariance (the retroactive-halving test above).
- [x] Pending: day-0 cohort shows no returns; entry appears next bar.
- [x] Milestone freeze: metrics at h=5 identical when recomputed later.
- [x] Baseline parity: cohort baseline == backtester baseline on the
      same engineered universe/date (shared-helper equality test).
- [x] Stale symbol flagged and retained in aggregates.
- [x] Scorecard: two engineered cohorts of one spec aggregate
      correctly; IS lookup joins on spec_hash; <20-name suppression.
- [x] Completion at 60 bars; archived cohorts still in scorecard.
- [x] Weights: allocation-weighted cohort return differs correctly
      from equal-weight on engineered dispersion.

## 17. Cohort replay & performance engine (v0.15) — in-sample cohort testing

Create a cohort as of ANY historical date and evaluate it to any later
date up to data availability, with a full performance panel. One metrics
engine serves BOTH modes: replay cohorts get long windows instantly;
forward cohorts get the same panel with end = latest bar. Reuses the
Item-16 record (dates-not-prices, entry at next open, shared baseline
helper) — this is an extension, not a fork.

### Integrity wall (the non-negotiable)

- [x] **`mode: "forward" | "replay"`** on every cohort. Replay = any
      cohort whose as-of date predates its creation timestamp; set
      automatically, never user-editable. Replay cohorts are
      **excluded from the OOS scorecard by default** — the date was
      chosen with the future visible, so they are in-sample by
      construction. Scorecard shows them, if at all, in a separate
      clearly-labelled "replay (in-sample)" grouping. UI badge on every
      replay cohort; survivorship note auto-attached (historical
      constituents problem, same as the backtester; membership filter
      from Item 15-B applies when built). A test asserts a replay
      cohort cannot enter the OOS aggregate.

### Creation

- [x] Natural flow: run a screen with the existing as-of picker →
      "Track these matches" → replay cohort with as_of = the screen's
      date, entry = next trading day's open. API `as_of` param on
      POST /api/cohorts; CLI `cohort create --as-of YYYY-MM-DD`.
      Validation: as_of must exist in the store and leave ≥1 later bar.

### Performance engine (`screener/cohort_perf.py`, pure functions)

- [x] **Window**: open[entry_date] → close[min(end_date, latest bar)];
      `end_date` param on evaluation (API GET /api/cohorts/{id}/
      performance?end=…, CLI `cohort perf <id> [--end]`), default
      latest. end < entry+1 → pending semantics, no metrics.
- [x] **Metric set (locked)** — cohort level, weighted (equal or
      allocation weights): cumulative return gross & net (round-trip
      haircut); excess vs same-entry-date universe baseline over the
      identical window (shared helper — never reimplemented) and vs
      Nifty; annualised volatility of daily cohort returns (√252);
      max drawdown on the cohort equity curve with peak/trough dates;
      hit rates (% names positive, % beating baseline); best/worst
      contributors by weighted contribution. **Sharpe reported only
      when window ≥ 60 bars**, else "window too short" — annualised
      Sharpe on a fortnight is noise and the tool says so.
- [x] **Per-symbol table**: entry px, end px, return g/n, excess,
      own max DD, weight, contribution; stale symbols flagged and
      retained (Item-16 rule unchanged).
- [x] **Equity curve series** (cohort vs baseline vs Nifty, indexed to
      100 at entry) returned for charting; UI cohort-detail view gains
      the curve chart + metrics panel; milestone table (5/20/60)
      retained alongside — milestones remain the cross-cohort
      comparison units, the panel is the deep dive.
- [x] Existing forward cohorts gain the same panel with zero migration
      (mode defaults to "forward" on read for old records).

### Tests (≥12)

- [x] Replay excluded from OOS scorecard (the wall test).
- [x] Mode auto-set; not user-overridable via API payload.
- [x] Hand-computed window return, excess, and max drawdown (incl. DD
      dates) on an engineered path; equity curve indexes to 100.
- [x] end_date clamping to latest bar; end<entry+1 → pending; end on a
      non-trading day resolves to prior bar.
- [x] Sharpe suppressed under 60 bars, present and hand-checked over.
- [x] Weighted vs equal contribution arithmetic on dispersion.
- [x] Adjustment invariance holds for replay windows (reuse the
      halving test at a historical as_of).
- [x] Old cohort records (pre-mode) readable, default forward.

## 18. v1.0 milestone + post-1.0 horizon (planned 2026-07-12)

### The v1.0 gate — "done" for the current arc

Tag v1.0 when ALL of: cutover chain complete (Item 2); the
nifty500-vs-nse_full comparison run executed with pre-registered
hypotheses and preset evidence loop closed (IS numbers in); Item 15-B
membership shipped at whatever coverage the archaeology supports;
v0.11 sidebar landed; and the three v1.0-hardening items below. v1.0
is a statement: the *engine* is finished; what grows after is
evidence, process, and analysis layers on top.

### v1.0 hardening (small, do before the tag)

- [x] **Data backup discipline** (v0.16.1, §12k) — `screener/backup.py`
      snapshots cohorts.jsonl (all universes) + screen/allocation/backtest
      logs + watchlist + saved presets into `data/backups/<ts>/`, rotated
      to 30. Nightly cron documented as `update && verify && backup`; the
      off-machine copy is a documented manual step (rclone/rsync/cloud
      sync), not automated — see README and TECHNICAL_DESIGN §11/§12k. A
      `verify` check confirms the latest backup exists and every file in
      it parses (WARN if missing, FAIL if corrupted). membership.csv does
      not yet exist as a store (Item 15-B is unshipped) — nothing to back
      up there yet; revisit when it lands.
- [x] **Schema versioning** (v0.16.1, §12k) — `SCHEMA_VERSION` +
      `migrate_record()` added to `cohorts.py` (mode/as_of) and to
      `webapp.py`'s screen log and backtest log (`universe` field),
      consolidating scattered inline `.setdefault()` defaulting into one
      migration function per store, applied at every read site that
      consumes the field. `cohorts._load_all()` migrates *and persists*
      the change back to disk (caught in testing: an earlier draft only
      migrated in memory, so old records would have re-migrated forever
      without ever actually landing). `allocation_log.jsonl`/
      `watchlist.jsonl` deliberately left unversioned — neither has ever
      changed shape.
- [x] **Docs completeness pass** (v0.16.1, §12k) — narrower than this
      item assumed: independent re-read found §12h/§12i/§12j (cohorts,
      replay, deletion tiers, breadth) already had substantial dedicated
      prose, not "changelog-only." Added: §12k itself, §11's check-count
      and cron-line refresh, README's nightly-cron/backup-CLI/test-count
      updates. LITERATURE.md's evidence loop-closure numbers correctly
      stay deferred — not actionable until the nifty500-vs-nse_full
      comparison run lands (blocked on pre-registered hypotheses).

### Post-1.0 tracks (directional — full spec written when picked up)

- [ ] **T1. Evidence protocol (process, not code — highest value) —
      DRAFTED 2026-07-21/22, needs Rohit's sign-off, not yet governing
      anything.** `EVIDENCE_PROTOCOL.md` (new) codifies the lifecycle of
      a filter — pre-registration (points at the existing
      `--hypothesis` flag, today a discipline not a code gate);
      retirement rule **[LOCKED]**, decided before results exist: ≥6
      forward cohorts AND ≥90 days → retire if 20-bar OOS mean excess <
      0 OR hit rate < 45% (the 20-bar horizon is this session's own
      judgment call, not specified in this locked text — flagged in the
      doc for sign-off); promotion rule **[PROPOSAL, not built]** —
      mirrors retirement's strictness, deliberately left uncoded
      pending sign-off since this codebase has no execution layer to
      promote a filter *into* anyway; weekly scorecard review ritual
      **[PROPOSAL]**. Small code support shipped: `cohorts.scorecard()`
      gained a `retirement` block (computed automatically, every call,
      diagnostic only — nothing archives a preset automatically, a
      human edits the dict literal by hand per the doc's §2); `presets.py`
      gained `STATUS_ACTIVE`/`STATUS_ARCHIVED`, `active_presets()`
      (excludes archived from discovery, `get()` still resolves any
      preset by id), and import-time validation of a `retirement_record`
      on any archived preset. Surfaced in the CLI `scorecard` command
      and the webapp cohorts→scorecard view. 13 new tests, 408 total,
      green. Live-verified against the real store. See
      TECHNICAL_DESIGN.md §12n. **Promotion rule and the review ritual
      itself still need your read — see EVIDENCE_PROTOCOL.md's
      sign-off checklist at the bottom.**
- [ ] **T2. Regime-conditional backtests.** Breadth fields exist —
      extend the backtester to split every report by regime at signal
      time (breadth positive/negative, optionally trend of Nifty):
      "support-at-50EMA carries +1.4% excess in positive breadth,
      −0.3% in negative" is the analysis layer the regime fields were
      built for. Cheap on existing machinery; sensitivity-grid-style
      output with the same small-N suppression.
- [ ] **T3. Daily signal digest.** Cron-driven: after nightly
      update+verify, diff saved screens (spec-hash machinery exists),
      collect new entrants + cohort milestones reached + breadth
      regime flips, send one Telegram/email digest. Read-only, no
      execution. Prior art exists (funding-arb Telegram alerter).
- [ ] **T4. Order-basket export (bridge, not execution).** From an
      allocation result: broker-importable basket CSV (Zerodha/Upstox
      formats), with the sizing rationale embedded as comments where
      the format allows. Explicitly NOT auto-execution — the tool's
      no-execution line stays.
- [ ] **T5. Cross-screen portfolio lens.** Aggregate view over active
      cohorts/allocations: symbol overlap ("RELIANCE appears in 3 of
      your 5 tracked screens"), combined sector exposure vs caps,
      total capital-if-all-followed. Concentration risk made visible
      before it's taken.
- [ ] **T6 (standing deferrals).** Separate asset-class engines (FX/
      crypto — Rohit's stated later goal), intraday, fundamentals,
      nested boolean logic: unchanged, revisit on concrete need.

Suggested order: hardening → tag v1.0 → T1 (before more evidence
accumulates under undefined rules) → T2 → T3 → T4/T5 by appetite.

## 19. Link (2003) practitioner screens (v0.17) — High Probability Trading — COMPLETE

Source: Marcel Link, *High Probability Trading*, McGraw-Hill 2003 —
reviewed in full 2026-07-13 (chat session; chapter-end rule lists +
Ch. 10 composite). Every preset here is `basis: practitioner`,
`source: Link (2003)` with honest caveats, and earns nothing until it
survives the same backtest + cohort gauntlet as the academic presets.
LITERATURE.md gains a cited section — concepts paraphrased in our own
words, never passages. Shipped whole, including divergence — the
item's own named risk item — with zero vectorizer-consistency
mismatches against the real 500-symbol store. See TECHNICAL_DESIGN.md
§12l, LITERATURE.md §10, changelog v0.17.0.

### Engine additions

- [x] **Stochastics (slow, 14-3-3)** — panel fields `stoch_k`, `stoch_d`:
      raw %K = 100·(close − LL14)/(HH14 − LL14); slow %K = SMA3(raw);
      %D = SMA3(slow %K). HH14=LL14 (zero range) ⇒ NaN, fails closed.
      Hand-checked test against a worked example. `cross` on
      stoch_k/stoch_d works free once the fields exist.
- [x] **`adx_slope`** — 5-bar diff, same pattern as EMA slopes (Link:
      rising ADX = strengthening trend; his thresholds 30/20 vs our
      canonical 25 — presets below use HIS numbers, tagged as such).
- [x] Both fields added to KNOWN_FIELDS, weekly panel excluded (daily
      only, v1), sparkline plottable-set updated for stoch bands? No —
      oscillators don't overlay price; skip.

### New condition types (exact formulas — the DSL contract)

- [x] **`threshold_cross`** —
      {"type":"threshold_cross","field":F,"level":N,
       "direction":"above"|"below","lookback":3}.
      True iff ∃ j in window: field[j−1] ≤ level < field[j] (above;
      mirror below). NaN on either side of a candidate bar ⇒ that bar
      can't cross. Vectorizes trivially (cheap set in the backtester).
- [x] **`persistence`** —
      {"type":"persistence","field":F,"op":OP,"value":N,"bars":M}.
      True iff ALL of the last M bars satisfy field OP value; any NaN
      in the window ⇒ False. Link Ch. 7: an oscillator pinned at an
      extreme for an extended period signals a strong trend, not a
      reversal. Vectorizes trivially.
- [x] **`divergence`** — the fuzziest; ONE strict formula, no options:
      {"type":"divergence","kind":"bullish"|"bearish",
       "oscillator":"rsi"|"stoch_k","lookback":40}.
      Bullish: take the two most recent CONFIRMED price pivot lows
      (fractal k=5, ≥5 bars apart, both inside the lookback window);
      require price: low(P2) < low(P1) strictly, AND oscillator at
      those same two pivot dates: osc(P2) > osc(P1) strictly.
      Bearish is the exact mirror on pivot highs. Fewer than two
      confirmed pivots ⇒ False. Reuses sr.find_pivots — no new pivot
      machinery. Backtester: expensive set (stride grid), consistency-
      gated like near_support. Explainer must show both pivot dates,
      both prices, both oscillator values.

### Presets (5, all practitioner-tagged)

- [x] `link_high_probability_pullback` — the Ch. 10 composite,
      flagship: weekly trend up + daily support_at_ma(ema_50, 1.5, 3)
      + range rsi 35–55 (pulled back, not collapsed, not chasing) +
      range adx min 30 (Link's strong-trend line). Caveat: composite
      of individually-plausible rules; the composite itself is
      untested folklore until our numbers land.
- [x] `link_oscillator_timed_entry` — trend up + threshold_cross rsi
      above 40 (lookback 3) + range adx min 30. Ch. 7's "buy the
      oversold reset in an uptrend, on the turn, never into it".
- [x] `link_trend_breakout` — Ch. 8 refinement of our existing
      breakout preset: tight_range(10, 8) + breakout_resistance(5) +
      volume_spike 1.5 + weekly trend up ("breakouts in the direction
      of the major trend work best").
- [x] `link_persistent_strength` — persistence rsi ≥ 60 for 15 bars +
      trend up. The anti-naive screen: pinned-high RSI as trend
      confirmation, not a sell signal.
- [x] `link_bullish_divergence` — divergence bullish (rsi) +
      near_support 3.0. Reversal screen; tagged lowest-confidence
      (divergence has the weakest evidence basis of the five — say so
      in the evidence object).

- [ ] **Divergence recall follow-up (spec flaw found in 07-17 sync
      review — attribution: the spec, not the implementation).** "Two
      most recent confirmed pivot lows" compares wiggle-to-wiggle: any
      minor pivot low between the two swing lows (the pullback inside
      the bounce creates one on almost any noisy path) displaces the
      earlier swing, so classic swing-to-swing divergences are
      systematically missed. Expected symptom: near-zero event counts
      in the divergence preset's backtest timeline — CHECK THAT FIRST;
      if confirmed, amend the formula (candidate: most recent pivot
      low vs the LOWEST prior pivot low in the window, or a prominence
      filter) as a spec change with the same strictness discipline,
      and re-run its fixtures. Do not soften ad hoc.

### Explicitly NOT implemented, with reasons (LITERATURE.md records them)

- Diagonal trendlines / channels — outside the horizontal-S/R
  framework; a bad approximation is worse than an honest gap.
- Fibonacci retracement levels — no evidence basis; skipped entirely.
- Congestion-measured price targets — target estimation, not
  screening; belongs to a future trade-plan layer if ever.
- Multi-timeframe sync — already shipped (weekly conditions); Ch. 5
  is validation, not work.

### Vocabulary, fixtures, tests

- [x] Parser vocab: "RSI crossing above 40", "stochastics turning up",
      "bullish divergence", "RSI holding above 60", "staying
      overbought" → persistence. Ambiguity rule: bare "divergence"
      without kind ⇒ parser must ask/refuse, not guess bullish.
- [x] Golden fixtures ≥4 incl. one refusal (bare "divergence").
- [x] Tests ≥14: stochastics hand-check; zero-range NaN; threshold
      cross engineered (incl. NaN-adjacent bar); persistence window
      edge; divergence engineered BOTH kinds (construct: decline to a
      lower price low on visibly weaker momentum ⇒ RSI higher low)
      plus a same-direction control that must NOT match; vectorizer≡
      evaluator consistency for all three new types; preset validation
      (import-time, free); evidence objects present.
- [x] LITERATURE.md: Link (2003) section — thesis, chapter mapping,
      what we implemented vs skipped and why, full citation.

Sized at one Claude Code session; divergence is the risk item — if
its tests fight back, ship the other four conditions/presets and
carry divergence to a follow-up rather than weakening the formula.

## 20. Performance plan (v0.18) — measured 2026-07-13

**Measured baselines** (chat sandbox, 500 synthetic symbols × 1250 bars;
nse_full ≈ ×4): `build_panels` **17.7s** (~35ms/sym; nse_full ≈ 71s) —
called on EVERY CLI invocation (6 call sites) and every webapp cold
start; evaluate-only screens 0.11s (cheap) / 0.6s (S/R); webapp
explain-all pattern 0.17s / 1.5s; one sr call ~1ms. Profile of
compute_panel: no dominant hot spot — ~40 pandas ops of fixed overhead
per symbol. **Diagnosis: recomputation, not computation.** The
INDICATOR_STORE cache has been declared in config since v0.1 and never
implemented; nothing interactive should ever rebuild panels.

### P1 — Indicator store cache (the fix; do first) — COMPLETE (v0.18)

- [x] Persist built panels to data/{universe}/indicators.parquet (long
      format + symbol column). Invalidation key stored alongside:
      prices-store mtime + an `INDICATOR_SCHEMA_VERSION` constant in
      indicators.py (bump whenever compute_panel's output changes — a
      cache-equivalence test guards it: cached-loaded panels must
      equal freshly built ones exactly, so a schema change without a
      bump fails CI). Shipped as `screener/panel_store.py`
      (`SCHEMA_VERSION` there, not `indicators.py` — a JSON sidecar
      next to the parquet, not a column, since the invalidation check
      needs to stay a cheap `os.stat()`, never a parquet read).
- [x] Write-through on `update` (the nightly cron builds the cache;
      interactive paths only ever load). Cold-miss fallback:
      build + write, once. Also wired into `backfill` (not just
      `update`) so the very first screen after a fresh backfill is
      warm too.
- [x] All CLI call sites + webapp `_load_state` route through one
      helper; on cache hit the CLI **skips loading the raw prices
      parquet entirely** (panels + universe meta + benchmark are
      sufficient for screen/backtest/cohorts). Shipped as `cli.py`'s
      `_panels()` wrapping `panel_store.load_or_build()` — 7 CLI call
      sites, not 6 as originally estimated (`cmd_verify` was missed in
      the original count; it still loads `prices` for its own raw
      OHLCV integrity checks regardless of the panel cache).
- [x] **Targets.** Live-verified against the real 500-symbol nifty500
      store: cache hit 10–24× faster than a cold rebuild (absolute
      seconds not comparable to the sandbox baseline above — this
      session hit an unrelated, severe local I/O contention episode
      that inflated every disk operation; the relative speedup and the
      zero-mismatch equivalence check across all 500 real symbols are
      the trustworthy signals). nse_full re-verification and the
      literal <5s/<10s/<2s targets are deferred to whenever nse_full
      is next exercised outside this contended environment. **Real
      trade-off found**: the cache is ~10× the raw price store's size
      (nifty500 measured 20.7MB → 210.9MB — `compute_panel`'s ~48
      derived columns, not a compression problem: `zstd` only saved
      ~6% over `snappy`) — worth knowing on a disk-constrained machine.

### P2 — Parallel rebuild (the miss path)

- [ ] ProcessPoolExecutor over symbol chunks for build_panels
      (spawn-safe module-level worker). Target: nse_full rebuild 71s
      → < 25s on 8 cores. Used by cron write-through and cold miss;
      interactive latency no longer depends on it once P1 lands.

### P3 — Webapp evaluate-first (secondary, measured 2-3×)

- [ ] /api/screen currently computes full evidence for EVERY symbol to
      find near-misses (1.5s @500 S/R). Split: cheap per-condition
      pass/fail pass over all symbols (no evidence strings), then
      explain only displayed rows (matches + near-misses ≤ ~100+15).
      Also removes the evaluator+explainer double-sr for hidden
      symbols. Target: S/R screen @nse_full < 3s after P1.

### P4 — Deliberately NOT doing (recorded)

- Per-symbol micro-optimisation of compute_panel (no hot spot to hit);
  long-format cross-symbol vectorisation (large refactor, unnecessary
  once nothing interactive rebuilds); request-scoped sr memoisation
  (1ms/call — noise); rewriting evaluate in numpy (0.11-0.6s already).

### P5 — Regression harness — COMPLETE (v0.18)

- [x] tests/perf_bench.py (pytest -m perf, excluded from default CI):
      re-times the four baseline numbers on the synthetic 500-universe
      and fails on >2× regression vs recorded baselines; run before
      every version tag. TECHNICAL_DESIGN gains the measured table.
      Three of the "four baseline numbers" got dedicated tests
      (build_panels, cheap-condition screen, S/R-condition screen); the
      fourth ("one sr call ~1ms") was folded into the S/R screen test
      rather than a standalone few-millisecond assertion, which would
      be too timing-noise-fragile to assert reliably — a documented
      simplification, not an oversight. A fourth test was added beyond
      the original four, specific to P1: cache-hit-vs-cold-build must
      be dramatically faster, not just "not slower" — the point of the
      whole exercise.

Sized ~one session (P1+P2+P5 together; P3 separable). **P1 and P5
shipped in v0.18 (2026-07-17/18); P2 (parallel rebuild) and P3 (webapp
evaluate-first) are not yet started** — P1 alone already removes the
dominant cost this item exists to fix, so P2/P3 are follow-ups, not
blockers. The v1.0 gate (Item 18) gains P1 as a hardening prerequisite
— shipping 1.0 with a 70-second CLI screen would be embarrassing.

## 21. Pairs discovery engine (v0.19) — sector-restricted pairs screening

**Division of labour (the architecture decision):** the screener
DISCOVERS and MONITORS pairs — formation stats, live z-scores, entry
signals, evidence trails; **pairstrader** (separate repo) owns the
strategy: execution, sizing, stops, P&L. Integration = a CSV export
pairstrader ingests. No pairs P&L backtest here — a light
convergence-rate diagnostic only (below). This is a SEPARATE engine
(screener/pairs.py + its own tab), not a contortion of the
single-symbol DSL: pair conditions don't fit per-symbol evaluation and
forcing them in would damage both.

NOT in the v1.0 gate — first session after the cutover chain.

### Formation (nightly, cached like the panel store)

- [ ] Candidate set: same-sector pairs (universe industry data — so
      nifty500 only until nse_full gets sector mapping), both legs
      pass the liquidity gate, and **short-leg-in-F&O constraint**:
      ingest NSE's F&O-eligible list (new fetcher, cached like the
      universe file); a pair is tradeable only if at least one leg has
      single-stock futures (that leg becomes the designated short —
      matches pairstrader's structure). Constraint toggleable but ON
      by default.
- [ ] Formation window 252 bars. TWO ranking methods, both computed,
      user picks: (a) **distance** — SSD between normalised cumulative
      return series (Gatev, Goetzmann & Rouwenhorst 2006 — the
      academically validated rule; caveat tag: Do & Faff 2010/2012
      show declining profits and cost sensitivity); (b)
      **cointegration** — Engle–Granger: OLS hedge ratio, ADF on the
      residual, p < 0.05 gate (practitioner standard; statsmodels
      becomes a dependency — pin it in requirements).
- [ ] Per surviving pair: hedge ratio, ADF p, SSD rank, **half-life of
      mean reversion** (OU fit on the spread; tradability band 5–40
      bars, outside it flagged), spread mean/σ over formation, and the
      **convergence diagnostic**: of historical |z| ≥ 2 excursions in
      the formation window, the fraction that reverted to |z| ≤ 0.5
      within 20 bars — a screening-quality stat, NOT a strategy
      backtest (no P&L, no costs; pairstrader does that).
- [ ] Formation output cached to data/{universe}/pairs.parquet keyed
      on the panel-cache mtime (same sidecar pattern as v0.18). Perf
      gate: nightly formation < 60s for nifty500 sector pairs on the
      Air; live z refresh < 2s.

### Signal & surfaces

- [ ] Live screen: pairs with |z| ≥ entry threshold (default 2.0,
      configurable), direction resolved (long undervalued leg / short
      the F&O leg — if the divergence direction would require
      shorting the non-F&O leg, the pair is shown but flagged
      UNTRADEABLE-THIS-SIDE), days-in-signal, z trajectory.
- [ ] Evidence trail per pair, same ledger style: formation stats,
      hedge ratio, half-life, convergence rate, current z with the
      formation mean/σ behind it. Spread sparkline (spread + ±2σ
      bands), pair detail view with both legs' charts.
- [ ] Pairs tab in the UI; CLI `pairs form` / `pairs screen`;
      **export**: `pairs export --format pairstrader` → CSV with the
      columns pairstrader ingests (agree the schema in that repo
      first — one line in its README documenting the contract).
- [ ] Screen-log entries for pair screens (same replay discipline);
      survivorship note variant: formation on current constituents.

### Evidence & tests (≥14)

- [ ] LITERATURE.md section: GGR 2006, Do & Faff decline + costs,
      cointegration-vs-distance honestly compared, Indian evidence if
      citable; method tags on every output.
- [ ] Engineered cointegrated pair (shared factor + stationary AR(1)
      spread) must: pass ADF, recover the true hedge ratio ±10%,
      half-life ±30% of construction, signal at an engineered 2.5σ
      divergence. Independent random walks must fail the ADF gate at
      ≈ the nominal rate (seeded, tolerance band). Convergence
      diagnostic hand-computed on a constructed z path. F&O
      constraint: pair with no F&O leg excluded; wrong-side divergence
      flagged. Cache invalidation on panel-store change.

## 12. Recurring operations (not one-time)

- [ ] Nightly: `update && verify` (cron after 18:30 IST) — set up once,
      then recurring.
- [ ] Quarterly: universe refresh + backfill for rebalance adds;
      `verify` coverage check confirms.
- [ ] Before ANY parser prompt change: live golden harness must be
      N/N. Before ANY DSL change: full pytest (presets validate at
      import — they are the canary).
- [ ] After every feature: README + TECHNICAL_DESIGN + this checklist
      updated in the same commit.

## 13. Deferred (decision recorded, revisit only on demand)

- Nested boolean logic (AND-of-ORs) — parser reliability + evidence
  readability cost; waits for a real query that needs it.
- Monthly timeframe — weekly machinery generalises when needed.
- Intraday — out of scope by design.
- Fundamentals — out of scope by design; parser refuses.
- Live golden-query harness run (`python -m tests.golden_harness`) —
  needs `ANTHROPIC_API_KEY`, not set in the current dev environment.
  Revisit when the key is available; still gates any parser-prompt
  change per §5.
