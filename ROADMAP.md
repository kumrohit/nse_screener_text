# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.8.0 — **v0.7 track complete** (Items 5 and 6).
Data layer live-verified (500/500), 20-condition DSL (incl. sector
filters & cross-sectional relative strength, gap), patterns, 19
built-in presets + unlimited saved custom screens, web UI with
evidence trails, sparklines, as-of replay, screen log, CSV export,
recent-screens replay, data-quality badges, config-hash footer,
screen diff, full chart modal, watchlist, multi-screen dashboard,
sortable/filterable results. NSE bhavcopy data layer v2 built and
validated, running side-by-side (2-week evidence clock started
2026-07-05); not cut over, nothing reads from it yet. 137 tests
green, no known failures — `tests/conftest.py` makes the suite
hermetic (forces demo mode so it passes identically in CI and on a
dev machine that has already run `backfill`).

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

## 3. Parked — screen backtesting (unpark criteria, not tasks)

Event-study engine: historical signal dates per spec, de-duplicated
forward returns (5/20/60 bars) vs universe baseline, tolerance
sensitivity grid, survivorship caveat on every report. The as-of
machinery it needs already exists. **Unpark when**: Items 1–2 above are
done AND at least one screen has earned enough trust in live use that
"has this setup historically carried edge?" is the blocking question.

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

## 7. Recurring operations (not one-time)

- [ ] Nightly: `update && verify` (cron after 18:30 IST) — set up once,
      then recurring.
- [ ] Quarterly: universe refresh + backfill for rebalance adds;
      `verify` coverage check confirms.
- [ ] Before ANY parser prompt change: live golden harness must be
      N/N. Before ANY DSL change: full pytest (presets validate at
      import — they are the canary).
- [ ] After every feature: README + TECHNICAL_DESIGN + this checklist
      updated in the same commit.

## 8. Deferred (decision recorded, revisit only on demand)

- Nested boolean logic (AND-of-ORs) — parser reliability + evidence
  readability cost; waits for a real query that needs it.
- Monthly timeframe — weekly machinery generalises when needed.
- Intraday — out of scope by design.
- Fundamentals — out of scope by design; parser refuses.
- Live golden-query harness run (`python -m tests.golden_harness`) —
  needs `ANTHROPIC_API_KEY`, not set in the current dev environment.
  Revisit when the key is available; still gates any parser-prompt
  change per §5.
