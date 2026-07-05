# NSE Text Screener — Technical Design Document

Version 0.3 · July 2026 · Status: Phase 2 + web UI complete, tested on synthetic data, pending first live run

---

## 1. Purpose and scope

A screener over the Nifty 500 universe that accepts filters written in plain English ("stocks taking support at 50 EMA and in an uptrend"), compiles them into a deterministic filter specification, and evaluates that specification against five years of daily price and volume history. Version scope is deliberately limited to price/volume technical analysis: no fundamentals, no intraday, no derivatives data. The parser is required to refuse anything outside this scope rather than approximate it.

## 2. Design principles

The system is built around one central rule: **natural language never touches computation.** The LLM's sole responsibility is translating text into a validated JSON specification (the DSL). Everything downstream — indicators, condition evaluation, result assembly — is deterministic, pure, and unit-testable. Consequences of this rule:

1. Every screen is reproducible: the compiled DSL plus the data snapshot date fully determine the output.
2. Ambiguity is resolved once, in a documented canonical vocabulary (§7), not per-query by model whim.
3. The user always sees the compiled interpretation in plain English before results, and can bypass the LLM entirely with `--json`.
4. The parser must return an explicit error for unmappable phrases. Guessing is a defect, and the golden-query suite (§10) treats it as one.

Secondary principles: fail loud (stale data, unknown fields, and missing benchmarks raise, never silently return empty results); no look-ahead anywhere (pivots need confirmation bars, historical as-of screens only use data up to the as-of row); prefer auditable pure-pandas formulas over indicator libraries.

## 3. Architecture

```
 user text ──► parser.py (LLM, canonical vocab) ──► DSL JSON
                                                       │
                                             dsl.py validate + describe
                                                       │ echoed to user
 yfinance/NSE ──► data_ingest.py ──► prices.parquet   │
                                          │            ▼
                                   indicators.py ──► evaluator.py ──► results table
                                   (daily panels,     (condition        (CLI print /
                                    weekly panels,     semantics)        CSV export)
                                    sr.py levels)
```

Module inventory: `config.py` (all tunables), `universe.py` (constituents), `data_ingest.py` (prices + benchmark), `indicators.py` (daily and weekly panels), `sr.py` (swing support/resistance), `dsl.py` (schema, validation, English echo), `evaluator.py` (condition semantics, screen runner), `parser.py` (LLM translation), `cli.py` (backfill/update/screen commands).

## 4. Data layer

**Universe.** Official Nifty 500 constituent CSV from NSE archives, fetched with browser-like headers (NSE blocks default user agents and most datacenter IPs), cached to `data/nifty500.csv`. On fetch failure the cached copy is used with a warning; with no cache the system refuses to run and tells the user how to obtain the file manually. The universe drifts with semi-annual index rebalances — re-fetch with `force_refresh=True` quarterly.

**Prices.** yfinance daily bars with `.NS` suffix, five years of history, `auto_adjust=True`. Auto-adjustment folds in splits and bonuses (essential — unadjusted data destroys every moving average at each corporate action) and also dividends (a minor distortion of raw levels, acceptable for technical work and strictly better than the alternative). Downloads run in chunks of 50 tickers with three retries and exponential-ish backoff, plus a two-second inter-chunk sleep, because yfinance throttling is the dominant operational risk of the free-data route. Storage is long-format Parquet (`symbol, date, open, high, low, close, volume`), roughly 600k rows for the full universe.

**Cleaning.** Rows are dropped when any OHLC value is non-positive or high < low. Timestamps are normalised to tz-naive dates.

**Incremental updates.** `update` re-fetches a ten-day tail for all symbols and merges with `keep="last"`, absorbing Yahoo's occasional late corrections. Schedule after ~18:30 IST when NSE close data settles on Yahoo.

**Benchmark.** Nifty 50 (`^NSEI`) close series stored separately in `benchmark.parquet`, refreshed on every backfill/update, consumed only by the `rel_strength` condition (which fails loud if the benchmark is absent).

**Integrity gates before any screen runs:** staleness check (latest bar within 5 calendar days, else refuse with instructions), minimum history (symbols with fewer than 60 bars are excluded from panels), and a liquidity gate (20-day median turnover ≥ ₹0.5 cr — mostly redundant for Nifty 500 but guards against data glitches surfacing dead tickers).

**Known data risks and mitigations.** (a) NSE symbol ≠ Yahoo ticker for a handful of names post merger/rename; the ingester skips failures silently, so compare the backfilled symbol count against 500 after the first run and maintain an alias map in `universe.py` if needed. (b) yfinance API changes break periodically; the ingest module is the designated swap point — replacing it with NSE bhavcopy ingestion touches nothing else. (c) Survivorship: the store only contains current constituents, so historical as-of screens are biased toward survivors. Acceptable for a discovery tool; documented so nobody mistakes Phase 3 screen-backtests for survivorship-clean research.

## 5. Indicator engine

Pure pandas/numpy; every formula lives in `indicators.py` and is auditable. Wilder-style indicators (RSI, ATR, ADX) use `ewm(alpha=1/n)` smoothing, matching standard charting-platform values. EMAs use `span=n, adjust=False` with `min_periods=n` so warm-up rows are NaN rather than misleading — and every condition treats NaN as False.

Daily panel columns: OHLCV; EMA 10/20/50/100/200 each with a 5-bar difference slope; SMA 20/50/200; RSI(14); ATR(14) and ATR%; ADX/+DI/−DI(14); MACD(12,26,9) line/signal/histogram; Bollinger(20, 2σ) upper/lower/width%; 20-day average volume and volume ratio; turnover in ₹ crore; 252-bar rolling 52-week high/low and percentage distances; rate of change over 5/21/63 bars (≈ 1 week / 1 month / 1 quarter).

Weekly panel (computed lazily, only when a screen contains a weekly condition): W-FRI resample of OHLCV; EMA 10/20/40 with 3-week slopes; RSI(14); ROC over 4 and 13 weeks. The in-progress week appears as a partial bar — a deliberate choice so "weekly uptrend" reflects the current state rather than last Friday's, at the cost of the last weekly bar being mutable until Friday's close.

Trend is defined canonically, not left to interpretation. Daily uptrend: close > EMA50 > EMA200 and EMA50 5-bar slope > 0. Weekly uptrend: close > EMA20w > EMA40w and EMA20w 3-week slope > 0. Downtrends are exact mirrors.

## 6. Filter DSL

A screen is `{"logic": "AND"|"OR", "conditions": [...], "as_of": "latest"|ISO-date}`. Validation is strict: unknown condition types, unknown fields, bad operators, and weekly-timeframe use of daily-only fields all raise `DSLValidationError` before any data is touched. Twelve condition types:

| Type | Keys (defaults) | Semantics |
|---|---|---|
| `compare` | left, op, right | Field vs field-or-number at the as-of row. |
| `proximity` | target, ref, tolerance_pct, lookback (3) | \|target−ref\|/ref ≤ tol on any bar in the window. |
| `trend` | direction | Canonical trend definition (§5). |
| `support_at_ma` | ma, tolerance_pct (1.5), lookback (3) | See below. |
| `cross` | fast, slow, direction, lookback (3) | Sign change of fast−slow within the window. |
| `volume_spike` | min_ratio | volume / 20-day average ≥ ratio. |
| `range` | field, min and/or max | Inclusive band check. |
| `change` | field, window, op, value_pct | Percent change over window bars. |
| `near_support` | tolerance_pct | Close within tol above nearest swing support (§8). |
| `near_resistance` | tolerance_pct | Close within tol below nearest swing resistance. |
| `breakout_resistance` | lookback (5), buffer_pct (0) | See §8. |
| `rel_strength` | window, op, value_pct | Stock return − Nifty return over window, in pct points. |

`compare`, `range`, `trend`, `change`, and `cross` accept `"timeframe": "weekly"`, which routes evaluation to the weekly panel with its restricted field set.

**"Taking support at a moving average"** — the phrase that motivated the whole design — is defined as: within the lookback window, (a) the low touched the MA (came within tolerance, or pierced below it), (b) the close held at or above MA × (1 − tol) on *every* bar of the window, and (c) the latest close is strictly above the MA. Condition (b) is what separates a bounce from a breakdown: a stock that slices through the MA and closes 3% below it has touched the MA but not taken support. Condition (c) requires the bounce to have actually happened, excluding stocks still sitting on the level.

The `describe()` function renders any validated spec back to plain English; the CLI prints this before every run and `--dry-run` stops there. This is the primary trust mechanism.

## 7. NL parser

Single Anthropic API call (`claude-sonnet-4-6`, temperature default) with a system prompt containing the full schema, the exhaustive allowed-field list, and the canonical vocabulary. Key mappings: uptrend/downtrend → `trend`; "taking support at / bouncing off <MA>" → `support_at_ma` (the user naming an MA distinguishes this from horizontal `near_support`); "near X" → `proximity` tol 2.0; golden/death cross → EMA50/EMA200 cross, lookback 5; volume spike → ratio 1.5, "huge/massive" → 2.5; oversold/overbought → RSI ≤30 / ≥70; "near 52-week high" → distance ≥ −5%; "strong trend" → ADX ≥ 25; "outperforming the Nifty" → `rel_strength` 63-bar > 0; "weekly uptrend" → weekly trend condition. Time words map as 1 week = 5 bars, 1 month = 21, 3 months = 63 (weekly: 4 and 13).

Two traps are encoded explicitly in the prompt. First, "breakout above 52-week high" must **not** compile to `close > high_52w` — the rolling high includes today, so that comparison is nearly unsatisfiable; it maps to `pct_from_52w_high ≥ −0.5` instead. Second, bare "moving average" defaults to EMA; "DMA" or "simple" selects SMA.

Hard parser rules: output raw JSON only; never invent thresholds beyond canonical defaults unless the user states them; AND unless the user says or/either; return `{"error": "..."}` for anything unmappable (fundamentals, news, sectors-as-filters, intraday). Parser output still passes through `dsl.validate()` — the LLM is never trusted to be well-formed.

## 8. Swing support/resistance methodology

Pivot detection uses the fractal method: bar j is a pivot high when its high is the maximum of bars [j−k, j+k], k = 5 (mirror for lows). The final k bars of any window can never confirm a pivot because the right side of the fractal hasn't printed — this is the standard confirmation lag and doubles as the look-ahead guard, verified by a dedicated test.

Levels are built per symbol, per as-of row, from pivots in the trailing 250 bars: pivot prices are sorted and greedily clustered (a price joins the running cluster if within 1.0% of the cluster's last member); each cluster's mean is a candidate level with a touch count; levels with fewer than 2 touches are discarded as noise. Nearest support is the highest surviving level at or below the close; nearest resistance is the lowest above it.

`breakout_resistance` computes levels **as of `lookback` bars ago** (so the breakout move itself cannot create or erase the level being broken), takes the nearest resistance from that vantage point, and requires the current close to exceed it by the optional buffer. This is intentionally conservative; the documented limitation is that very recent consolidations (younger than pivot confirmation + lookback) can't produce breakout signals yet.

Parameter choices (k=5, 250-bar lookback, 1% cluster tolerance, 2-touch minimum) are defensible defaults, not optimised values — they live in `sr.py` constants and are candidates for sensitivity analysis in Phase 3.

## 9. Evaluator semantics

Conditions are pure functions `(panel, condition, row_index) → bool`. NaN anywhere in a condition's inputs yields False (a stock with insufficient history cannot match — it is never accidentally included). `as_of` resolves to the last row at or before the given date, independently for daily and weekly panels. `logic` applies flat AND/OR across conditions; nested boolean trees are explicitly out of scope for v1 (the parser refuses queries requiring them). The runner applies the liquidity gate, evaluates each symbol, and emits a result row of screening context: close, % vs EMA50, RSI, volume ratio, 1M/3M returns, distance from 52-week high, plus name and industry from the universe file, sorted by 3-month return.

## 10. Testing strategy

Three layers. **Synthetic-series unit tests** (27 passing): constructed price paths with known correct answers — an engineered pullback-to-EMA50 must match `support_at_ma` while a breakdown through the same EMA must not; a range-bound series must yield levels at both extremes and match `near_support` while rejecting `near_resistance`; a golden cross is verified at its exact bar; pivots must not exist in the final k bars; relative strength must reject a stock benchmarked against itself; a missing benchmark must raise. **Golden-query suite** (12 fixtures): hand-verified query → expected-DSL pairs, including one mandatory refusal (a P/E query). The offline half runs in CI and asserts every fixture still validates as the DSL evolves; the live half (`python -m tests.golden_harness`, needs an API key) scores the actual parser with semantic comparison — defaults filled, conditions sorted — and must be 12/12 before any parser-prompt change ships. **Live-data sanity checks** (manual, first run): backfill symbol count vs 500; spot-check three symbols' EMA50/RSI against a charting platform; run the flagship query and eyeball charts of the matches.

## 11. Operations runbook

First run: create a Python 3.10+ venv (`python3.12 -m venv .venv && source .venv/bin/activate` — both entry points guard against older interpreters and print this fix), `pip install -r requirements.txt`, set `ANTHROPIC_API_KEY`, `python -m screener.cli backfill` (10–15 min), then `python -m screener.cli verify` — an automated 11-check health report (coverage vs index list, freshness, depth, bar integrity, duplicates, corporate-action smell test via >40% single-day move counts, benchmark presence, RSI/EMA spot checks on a 25-symbol sample) that exits non-zero on FAIL for pipeline gating; `verify --jumps` lists the exact bars behind the adjustment smell test with split-ratio hints, and `refetch SYMBOL` drops and freshly re-downloads one symbol as the unadjusted-data remedy. Nightly: cron `python -m screener.cli update` after 18:30 IST. Quarterly: refresh the universe and re-backfill to pick up rebalances. Screens: `screen "<text>"`, `--dry-run` to inspect the compiled spec, `--json` to bypass the LLM, `--out file.csv` to export. Failure modes: stale-store error → run update; universe fetch fails → cached list used automatically, manual CSV drop-in as last resort; yfinance thin/missing data for a symbol → excluded by the 60-bar minimum, check the alias problem in §4; parser refusal → the query needs rephrasing within scope, or the concept belongs on the roadmap.


## 12. Web UI and evidence layer

`python -m screener.webapp` serves a single-page interface (FastAPI backend, dependency-free vanilla-JS frontend in `web/index.html`, port 8501) built around full auditability of every screen. The page always shows, together: the original query, the plain-English compiled interpretation, the raw JSON spec (collapsible), the data as-of date and mode, and funnel statistics (universe → liquidity-excluded → evaluated → matched).

The core of the UI is the **evidence trail**, powered by `explain.py`. For every condition on every displayed stock it reports pass/fail plus the observed values behind the decision — e.g. for `support_at_ma`: the date the low touched the MA and the touch distance, the worst close-vs-MA excursion across the window, and the latest close's margin above the MA. Pass/fail is delegated to the same `evaluator.cond_*` functions used by the screen itself, so the explanation can never disagree with the result; `explain.py` only adds observability. A **near-miss** section lists stocks failing exactly one condition with the failing condition marked ✗ — the most useful feedback for tuning tolerances.

Endpoints: `GET /api/status`, `POST /api/parse` (text → spec via the LLM; returns a structured error without an API key), `POST /api/screen` (spec → matches + near-misses + methodology block). Specs can bypass the parser entirely via the JSON tab. When `data/prices.parquet` is absent the backend boots a labelled synthetic 8-stock demo universe (`demo.py`) with engineered behaviours (EMA pullback, breakdown, range-bound at support, resistance breakout, oversold, golden cross), so the UI, API, and evidence layer are fully testable — in CI and by a fresh clone — without market data.

## 13. Roadmap

Phase 3 (next): screen backtesting — for any DSL spec, compute historical hit dates per symbol and forward return distributions (5/20/60 bars) vs universe baseline, turning the screener into an edge-validation tool; reuse the existing as-of machinery (already built for this) and the Indian transaction-cost model from the momentum backtester. Then: candlestick and consolidation patterns (inside bars, NR7, flat bases) as DSL conditions; sector/industry relative strength using the universe's industry column; nested boolean logic if real queries demand it; delivery-percentage data (requires the bhavcopy migration — yfinance doesn't carry it); optional React front-end reusing the terminal-style screener UI.

## 14. Changelog

0.1 — Data layer (Nifty 500, yfinance 5y, Parquet), daily indicator engine, 8-condition DSL with validation and English echo, LLM parser with canonical vocabulary, CLI, 16 synthetic tests.
0.3.2 — `verify --jumps` diagnostic (lists smell-test bars with split-ratio classification hints) and `refetch SYMBOL` remedy command. 37 tests.
0.3.1 — `verify` command automating the §10 checklist (verify.py, 3 tests), Python≥3.10 guards on entry points, README first-run checklist and troubleshooting. 36 tests.
0.3 — Web UI: FastAPI backend + single-page evidence-trail frontend, explain.py observability layer, near-miss reporting, synthetic demo mode, 33 tests.
0.2 — Swing-pivot S/R (`near_support`, `near_resistance`, `breakout_resistance`), weekly timeframe on 5 condition types, `rel_strength` vs Nifty with benchmark ingestion, golden-query suite (12 fixtures, offline + live harness), 27 tests total, this document.
