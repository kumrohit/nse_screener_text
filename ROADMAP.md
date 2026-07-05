# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.6.3 — data layer live-verified (500/500),
20-condition DSL (incl. sector filters & cross-sectional relative
strength, gap), patterns, 19 presets, web UI with evidence trails,
sparklines, as-of replay, screen log, CSV export, recent-screens
replay. NSE bhavcopy data layer v2 built and validated, running
side-by-side (2-week evidence clock started 2026-07-05); not cut
over, nothing reads from it yet. 92 tests green, no known
failures — `tests/conftest.py` makes the suite hermetic (forces
demo mode so it passes identically in CI and on a dev machine
that has already run `backfill`).

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

## 5. UI depth (v0.7 track) — make it a daily-driver

Ordered by daily-use value, not effort.

- [ ] **Screen diff ("what changed since last run")** — for any spec run,
      look up the previous run of the *same spec* (hash the canonical
      spec; screen log already stores everything needed) and badge
      results: NEW entrants, plus a collapsed "dropped since last run"
      list with the condition that now fails (reuse explain on the
      dropouts). Acceptance: run preset → update data → rerun → diff
      correct; hash stable under key order/default fill.
- [ ] **Full chart modal** — click a sparkline → large modal chart:
      candlesticks (patterns need candles, not a close line), volume
      subpane, all spec-referenced overlays + evidence levels, ~250 bars,
      drag-to-zoom. Zero external libs (offline constraint stands);
      hand-rolled SVG. Acceptance: driven via Playwright like v0.6.2.
- [ ] **Watchlist with signal-decay tracking** — star a match →
      data/watchlist.jsonl (symbol, date, spec hash, close at tag).
      Watchlist view: % move since tag, and whether the original
      conditions still hold today (re-evaluate spec). Acceptance:
      tag → advance as-of → status updates correctly.
- [ ] **Saved custom screens** — persist user-authored specs
      (data/user_presets.json) with name/notes; appear in the dropdown
      under "My screens"; save-from-current-spec button; delete/rename.
      Validation identical to built-ins (reject on save, not on run).
- [ ] **Multi-screen dashboard** — run N selected presets in one call
      (`POST /api/screen_batch`); compact grid: preset × (match count,
      top-3 symbols, new-since-last-run count). The morning view.
- [ ] **Results table ergonomics** — client-side column sort, sector
      filter chips built from the result set, sticky header. No pagination
      (cap already exists).

## 6. Robustness hardening (v0.7 track)

- [ ] **P0 — stale-server fix**: webapp loads panels once at startup;
      after nightly `update`, a long-running server screens yesterday's
      data. Fix: record store mtime in `_load_state`; on each /api/screen
      and /api/status, if mtime changed → rebuild state (under the
      existing lock) and clear cross-section cache. Acceptance test:
      monkeypatched store swap mid-session changes as_of without restart.
- [ ] **Data-quality badges on matches** — per-symbol flags surfaced in
      results, not buried in verify: recent >40% jump within the spark
      window (adjustment/demerger risk — "levels may straddle a gap"),
      thin history (<250 bars), symbol-stale (last bar < store's latest,
      e.g. suspended names). Backend adds `flags: []` per match; UI shows
      a small ⚠ with reason. Acceptance: demo symbol engineered per flag.
- [ ] **User config overrides** — optional data/config_local.toml
      overriding tunables (tolerances, liquidity gate, SR params, spark
      bars) without code edits; effective config hash logged with every
      screen-log entry and shown in the methodology footer (a screen is
      only reproducible if its config is part of the record).
- [ ] **Parser resilience** — one retry on malformed JSON; failed parses
      appended to data/parse_failures.jsonl (query + raw output) as the
      vocabulary-improvement backlog; /api/parse returns the canonical
      "assumptions" list when the LLM filled a default so the UI can
      render "interpreted with defaults: …".
- [ ] **`/api/health`** — cheap JSON for cron/uptime monitoring: store
      mtime + as-of, panel count, benchmark present, log writable,
      version (git describe). Nightly pipeline curls it after update.
- [ ] **Screen-log rotation** — size-capped rotation (keep last ~5k
      runs); verify's integrity check learns about rotated files.
- [ ] **Golden harness in CI (manual)** — workflow_dispatch job using an
      ANTHROPIC_API_KEY repo secret, so parser-prompt PRs can be gated
      without a local run.

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
