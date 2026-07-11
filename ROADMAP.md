# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.14 — **Item 16 (cohort tracker, walk-forward
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
filters itself to what's applicable on the active universe. 283 tests
green, no known failures — `tests/conftest.py` makes the suite hermetic
(forces demo mode, and isolates every webapp log/store file to a
per-test tmp path, so it passes identically in CI and on a dev machine
that has already run `backfill`, without ever touching real `data/`).
Next up: breadth fields + the universe comparison (Session 2 of the
2026-07-11 sequencing block, below), point-in-time index membership
(Item 15 Phase B, gated on data-source archaeology), the deferred
sidebar layout restructure, the preset evidence loop-closure (Item
14's own follow-on, now also fed by Item 16's OOS scorecards), or
Item 3's bhavcopy cutover once its evidence window closes.

---

---

## SEQUENCING — week of 2026-07-11 → cutover (updated 2026-07-11)

**Session 1 — DONE 2026-07-11: Item 16 cohort tracker.** Built and
live-verified (CLI + API + UI, Playwright-driven), 283/283 tests green.
Cohorts seeded from all four presets — support_50ema_uptrend,
momentum_12_1_leaders, minervini_stage2, flat_base_52w — on BOTH
nifty500 and nse_full (8 cohorts total, all `pending`: created today,
so entry resolves on the next trading bar in the store). 5-bar
milestones mature before the 07-19 cutover; 20-bar lands with the
first evidence review (~2 weeks out).

**Session 2: breadth fields + the universe comparison (resequenced —
does NOT wait for cutover).** Market-breadth regime fields into the
cross-sectional pre-pass, then the strategy-preset backtests on
nifty500 vs nse_full. Prerequisite from Rohit BEFORE the run:
pre-registered hypotheses per preset per universe, drawn from
LITERATURE.md effect sizes (the momentum-strengthens-in-breadth
prediction is the headline one). This run also produces the IS numbers
that close the deferred backtest evidence loop.

**Rohit's own tasks this week (no Claude Code needed):**
- Nightly cron NOW, not at cutover: `update && verify` after 18:30 IST
  — cohort milestones and the cross-source check both want unattended
  daily data from today.
- Membership archaeology hour (gates Item 15-B's scope decision).
- Write the session-2 hypotheses before session 2 runs.

**2026-07-19 (clock): cutover chain session** — if cross-source check
clean: config flip, yfinance to fallback, `delivery` condition +
accumulation preset, risk log. Delivery-based presets get cohort-seeded
the same day (their OOS clock starts latest, so no reason to add delay).

**AFTER:** Item 15-B membership build (scope per archaeology); v0.11
sidebar anytime; first cohort scorecard review ~2 weeks after seeding.

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

## 2. Item 3 — NSE bhavcopy migration (data layer v2) — build done 2026-07-05, clock started

Deliberately after Item 2. Run side-by-side with yfinance; cut over only
on evidence.

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
      automatically. First real week of side-by-side data (2026-06-25
      → 2026-07-03): 45/3,000 overlapping bars over 0.5%, all in 11
      symbols with a *constant* per-symbol gap — the signature of an
      already-documented past dividend adjustment (§4a), not a new
      problem. **2-week clock started 2026-07-05** — not code-gated,
      calendar-gated; revisit for the actual cutover decision after.
- [ ] **DSL: `delivery` condition** + vocabulary ("high delivery",
      "delivery spike") + preset ("accumulation: volume spike + delivery
      > 60%") — only after cutover.
- [ ] **Cutover** — config flag flips primary source; yfinance demoted
      to fallback; README/runbook updated. Blocked on the 2-week
      evidence window above.
- [ ] **Risk log** — NSE format changes are the known recurring hazard;
      keep parser tolerant and fail loud with the file snippet in the
      error. (The ingestion code already fails loud on fetch errors and
      logs which day/symbol failed; a dedicated risk-log write-up is
      still open.)
- [ ] **Nightly cron** — `bhavcopy-update` is a manual command today;
      add it alongside `update` in the cron job once side-by-side
      collection should run unattended.

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
- [ ] **Market breadth regime fields** — computed from the universe
      itself into the cross-sectional pre-pass: pct_above_200dma,
      pct_at_20d_high; new `breadth` condition ("market breadth
      positive" → pct_above_200dma ≥ 50). Regime context for every
      equity screen without external data. Golden fixtures + preset
      annotation (breadth filters are regime qualifiers, weak alone).
- [ ] Backtest loop: rerun the strategy-preset evidence closure on
      nse_full vs nifty500 — does the edge strengthen in the broader,
      less-efficient universe (the momentum literature says it should)
      or was it a large-cap artifact? That comparison is the payoff of
      this whole item.

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
