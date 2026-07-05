# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.6 shipped — data layer live-verified (500/500),
19-condition DSL (incl. sector filters & cross-sectional relative
strength), patterns, 17 presets, web UI with evidence trails,
sparklines, as-of replay, screen log. 64 tests green (3 known
local-environment failures unrelated to app code — see Item 0).

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

## 2. Item 3 — NSE bhavcopy migration (data layer v2)

Deliberately after Item 2. Run side-by-side with yfinance; cut over only
on evidence.

- [ ] **`screener/bhavcopy.py`** — daily UDiFF bhavcopy download
      (session headers, retry, holiday calendar), parse to the store
      schema; EQ series filter. Acceptance: one day's file for all 500
      symbols matches yfinance closes within rounding for unadjusted
      names.
- [ ] **Delivery % ingestion** — new store column + panel field
      `delivery_pct`; NaN before migration date.
- [ ] **Corporate-actions pipeline** — ingest NSE CA file; build
      per-symbol adjustment factors (splits/bonuses only, documented:
      dividends NOT adjusted — note the divergence from yfinance
      convention in the design doc); apply on read. `verify --jumps`
      is the regression harness: post-adjustment jump count must be ≤
      the yfinance store's count.
- [ ] **Cross-source consistency check in `verify`** — while both
      sources run: daily close divergence report; investigate >0.5%
      systematic gaps. Acceptance: 2 weeks of side-by-side with no
      unexplained divergence before cutover.
- [ ] **DSL: `delivery` condition** + vocabulary ("high delivery",
      "delivery spike") + preset ("accumulation: volume spike + delivery
      > 60%") — only after cutover.
- [ ] **Cutover** — config flag flips primary source; yfinance demoted
      to fallback; README/runbook updated.
- [ ] **Risk log** — NSE format changes are the known recurring hazard;
      keep parser tolerant and fail loud with the file snippet in the
      error.

## 3. Parked — screen backtesting (unpark criteria, not tasks)

Event-study engine: historical signal dates per spec, de-duplicated
forward returns (5/20/60 bars) vs universe baseline, tolerance
sensitivity grid, survivorship caveat on every report. The as-of
machinery it needs already exists. **Unpark when**: Items 1–2 above are
done AND at least one screen has earned enough trust in live use that
"has this setup historically carried edge?" is the blocking question.

## 4. Small backlog (slip in anywhere, one commit each)

- [ ] UI: "Recent screens" panel fed by `/api/log` (replay any past run
      with one click — spec + as-of restore).
- [ ] UI: CSV export button on results (server already computes rows).
- [ ] UI: near-miss toggle (hide/show) and match-count cap for huge
      result sets.
- [ ] CLI: `screen --as-of YYYY-MM-DD` flag (parity with the UI picker).
- [ ] `verify`: add screen-log integrity check (parseable JSONL).
- [ ] Preset ideas parking lot: weekly squeeze, gap-up follow-through
      (needs gap condition), post-earnings drift (needs events data —
      likely never; note why).

## 5. Recurring operations (not one-time)

- [ ] Nightly: `update && verify` (cron after 18:30 IST) — set up once,
      then recurring.
- [ ] Quarterly: universe refresh + backfill for rebalance adds;
      `verify` coverage check confirms.
- [ ] Before ANY parser prompt change: live golden harness must be
      N/N. Before ANY DSL change: full pytest (presets validate at
      import — they are the canary).
- [ ] After every feature: README + TECHNICAL_DESIGN + this checklist
      updated in the same commit.

## 6. Deferred (decision recorded, revisit only on demand)

- Nested boolean logic (AND-of-ORs) — parser reliability + evidence
  readability cost; waits for a real query that needs it.
- Monthly timeframe — weekly machinery generalises when needed.
- Intraday — out of scope by design.
- Fundamentals — out of scope by design; parser refuses.
- Live golden-query harness run (`python -m tests.golden_harness`) —
  needs `ANTHROPIC_API_KEY`, not set in the current dev environment.
  Revisit when the key is available; still gates any parser-prompt
  change per §5.
