# ROADMAP — execution checklist

Working checklist for all remaining work. Items get checked off in the
commits that complete them; anything descoped gets struck through with a
one-line reason, not silently deleted. Design rationale lives in
TECHNICAL_DESIGN.md; this file is the *what and in which order*.

Status snapshot: v0.5 shipped — data layer live-verified (500/500),
16-condition DSL, patterns, 14 presets, web UI with evidence trails,
sparklines, as-of replay, screen log. 50 tests green.

---

## 0. One-time setup & validation (do before/alongside Item 2)

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

## 1. Item 2 — Sector filters & cross-sectional relative strength

The one structural change: a **cross-sectional pre-pass** computed over
the whole universe per date, cached like the benchmark. Everything else
hangs off it.

- [ ] **`screener/cross_section.py`** — per-date universe-wide table:
      RS percentile of each stock (63-bar return rank, configurable
      window), equal-weight sector aggregate returns, sector momentum
      ranks. Computed lazily from panels, cached in-process keyed by
      (as_of, window); no disk state in v1. Acceptance: pure function
      panels→DataFrame; deterministic; NaN-safe (thin-history symbols
      excluded from ranks, never defaulted to 0th/100th percentile).
- [ ] **DSL: `sector` condition** — `{"type":"sector","in":[…]}` matching
      the universe file's industry column (exact strings; validation
      rejects unknown sector names with the list of valid ones).
      Evaluator needs access to universe metadata — thread it through
      like benchmark.
- [ ] **DSL: `rs_percentile` condition** —
      `{"type":"rs_percentile","window":63,"op":">=","value":80}`.
      Semantics: percentile among symbols with sufficient history on the
      as-of date.
- [ ] **DSL: `sector_rank` condition** —
      `{"type":"sector_rank","window":63,"top":3}` — stock's sector is in
      the top-N by equal-weight momentum. Document equal-weight
      construction explicitly (no cap weights available).
- [ ] **Historical as-of correctness** — cross-sectional values at an
      as-of date must use only data ≤ that date (ranks recomputed at the
      as-of row, not sliced from today's ranks). Dedicated look-ahead
      test, same spirit as the pivot test.
- [ ] **Explainers** — rs_percentile: rank, N, actual return vs cutoff
      return. sector_rank: sector, its rank, top-3 list with returns.
      sector: stock's industry string.
- [ ] **Parser vocabulary** — "IT stocks / in the IT sector",
      "RS above 80", "market leaders", "in a leading/top sector".
      Ambiguity rule: bare sector adjectives map to `sector` only when
      they match a known industry string; otherwise refuse.
- [ ] **Presets** — add 2–3: sector-leader pullback (top-3 sector +
      support_at_ma), RS>80 near 52w high, lagging-sector bounce
      (bearish/contrarian, clearly labelled).
- [ ] **Golden fixtures** — ≥3 new queries incl. one refusal (unknown
      sector name).
- [ ] **Tests** — synthetic multi-symbol universe with engineered sector
      dispersion; percentile edge cases (ties, thin history); ≥8 new
      tests. Suite target: ~60.
- [ ] **Docs** — DSL table rows, §"cross-sectional pre-pass" section,
      README vocab rows, changelog 0.6.
- [ ] **Perf check** — pre-pass over 500 symbols × 5y must add <5s to a
      screen on the MacBook Air; if not, memoise harder before shipping.

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
