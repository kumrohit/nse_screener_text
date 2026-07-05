# NSE Text Screener — Technical Design Document

Version 0.6.2 · July 2026 · Status: live data verified (500/500 coverage); patterns, presets, as-of replay, sparklines, screen log, sector filters & cross-sectional relative strength shipped; NSE bhavcopy data layer v2 running side-by-side, pre-cutover; UI/CLI/verify backlog cleared

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

**Known data risks and mitigations.** (a) NSE symbol ≠ Yahoo ticker for a handful of names post merger/rename; the ingester skips failures silently, so compare the backfilled symbol count against 500 after the first run and maintain an alias map in `universe.py` if needed. (b) yfinance API changes break periodically; the ingest module is the designated swap point — replacing it with NSE bhavcopy ingestion touches nothing else. (c) Survivorship: the store only contains current constituents, so historical as-of screens are biased toward survivors. Acceptable for a discovery tool; documented so nobody mistakes Phase 3 screen-backtests for survivorship-clean research. (d) yfinance adjustment-splice bug: for at least three symbols (CGCL, GPIL, MOTILALOFS), `auto_adjust=True` applies the correct split/bonus ratio but splices it in at the wrong date — a large jump appears on 2024-01-01 for all three, months before their true record dates (Mar/Jun/Oct 2024). Confirmed via `verify --jumps`: `refetch` returns byte-identical data, so this is upstream in Yahoo's own adjusted series, not a local staleness issue. Harmless for live screens once the affected window ages out of the longest lookback (252 bars), but historical as-of replays dated within the corrupted window for those symbols will show wrong EMA/RSI/52-week values. No general fix; flagged here so it isn't mistaken for a fixable ingestion bug.

## 4a. Data layer v2 — NSE bhavcopy (ROADMAP Item 3, pre-cutover)

`screener/bhavcopy.py` runs **side-by-side** with the yfinance store above — its own store (`data/bhavcopy_prices.parquet`), read by nothing in the evaluator/webapp/CLI screen path yet. Cutover happens only after ~2 weeks of evidence from the cross-source check below.

**Source.** NSE's daily `sec_bhavdata_full_DDMMYYYY.csv` — confirmed by fetching a live file (2026-07-03) rather than assumed from training data: one file has OHLCV *and* delivery % together (`SYMBOL, SERIES, DATE1, ..., CLOSE_PRICE, ..., TTL_TRD_QNTY, ..., DELIV_QTY, DELIV_PER`), filtered to `SERIES == "EQ"`. A separate delivery-only file turned out to be unnecessary. `bhavcopy.update_bhavcopy_store()` fetches missing business days since the store's last date (or the last 10 calendar days on first run); a 404 is treated as a weekend/holiday, not a failure — NSE's own calendar is the source of truth. First real run (2026-07-05) pulled 2,431 symbols (all NSE EQ series, not just Nifty 500), 14,403 rows, 2026-06-25 → 2026-07-03.

**Acceptance check, run for real:** bhavcopy closes vs the yfinance store for the same date (2026-07-03), all 500 Nifty 500 symbols — median absolute difference ~2×10⁻⁶%, max ~5×10⁻⁶% (floating-point rounding only), zero symbols over 0.5%. Both sources are unadjusted for that single day (adjustment convention only affects *historical* bars), so this is a clean apples-to-apples check.

**Prices are raw/unadjusted** from both NSE files — same as yfinance without `auto_adjust`. Our own adjustment pipeline: `fetch_corporate_actions()` hits `/api/corporates-corporateActions` (needs a cookie warm-up `GET` to nseindia.com first; that warm-up itself may 403 yet still set the cookies the API call needs, so its status isn't checked). `parse_adjustment_factor(subject)` extracts a multiplicative factor from the free-text `subject` line using two regexes built from **real** NSE text (fetched 2026-07-05, Jan 2024–Jul 2026 window), not guessed formats:
- `"Bonus X:Y"` (X new shares per Y held) → `factor = Y / (X + Y)`
- `"Face Value Split (Sub-Division) - From RsA/- Per Share To RsB/- Per Share"` → `factor = B / A`

Splits/bonuses only — dividends and anything else return `None` and are excluded from adjustment (documented divergence from yfinance's `auto_adjust`, which folds dividends in too). CGCL is a good cross-check: NSE lists *two* separate 2024 actions for it (a 1:1 bonus **and** a 2→1 face-value split), and `0.5 × 0.5 = 0.25` matches the real jump ratio investigated in ROADMAP Item 0 almost exactly. `build_adjustment_factors()` compounds each symbol's factors going backward in time (a bar's cumulative factor is the product of its own action's factor and every *later* action's factor — the same convention `auto_adjust` uses); `apply_adjustments()` must apply actions **latest-ex-date-first**, since an earlier action's `date < ex_date` mask is always a subset of every later action's mask — applying oldest-first would let the later (broader, single-factor) write clobber the earlier (narrower, correctly-larger cumulative-factor) one. Caught and fixed during implementation; regression-tested (`TestAdjustmentCompounding.test_apply_adjustments_ordering`).

**Cross-source consistency check** (`verify.check_cross_source`, folded into `python -m screener.cli verify` automatically): compares overlapping `(symbol, date)` closes between the two stores and WARNs (not FAILs — bhavcopy isn't consumed by any screen yet) above a 0.5% threshold. First real run found 45/3,000 overlapping bars over threshold, concentrated in 11 symbols (e.g. UNIONBANK ~2.95%, TECHM ~2.60%) — but the gap is *constant* across every date for a given symbol, the signature of a past dividend adjustment already folded into yfinance's series (not a bug; exactly the divergence source documented in §4(d) above). A systematic, non-constant gap would be the signal to actually investigate before counting a day toward the 2-week cutover evidence window.

**Not yet done** (explicitly gated): the `delivery` DSL condition, its parser vocabulary, and the "accumulation" preset — ROADMAP marks these "only after cutover," so they aren't wired into `dsl.py`/`evaluator.py`/`parser.py` yet even though `delivery_pct` already exists in the bhavcopy store. Also not done: the config-flag cutover itself, and adding `bhavcopy-update` to the nightly cron (currently a manual command — add it alongside `update` once the side-by-side evidence period is underway).

## 5. Indicator engine

Pure pandas/numpy; every formula lives in `indicators.py` and is auditable. Wilder-style indicators (RSI, ATR, ADX) use `ewm(alpha=1/n)` smoothing, matching standard charting-platform values. EMAs use `span=n, adjust=False` with `min_periods=n` so warm-up rows are NaN rather than misleading — and every condition treats NaN as False.

Daily panel columns: OHLCV; EMA 10/20/50/100/200 each with a 5-bar difference slope; SMA 20/50/200; RSI(14); ATR(14) and ATR%; ADX/+DI/−DI(14); MACD(12,26,9) line/signal/histogram; Bollinger(20, 2σ) upper/lower/width%; 20-day average volume and volume ratio; turnover in ₹ crore; 252-bar rolling 52-week high/low and percentage distances; rate of change over 5/21/63 bars (≈ 1 week / 1 month / 1 quarter).

Weekly panel (computed lazily, only when a screen contains a weekly condition): W-FRI resample of OHLCV; EMA 10/20/40 with 3-week slopes; RSI(14); ROC over 4 and 13 weeks. The in-progress week appears as a partial bar — a deliberate choice so "weekly uptrend" reflects the current state rather than last Friday's, at the cost of the last weekly bar being mutable until Friday's close.

Trend is defined canonically, not left to interpretation. Daily uptrend: close > EMA50 > EMA200 and EMA50 5-bar slope > 0. Weekly uptrend: close > EMA20w > EMA40w and EMA20w 3-week slope > 0. Downtrends are exact mirrors.

## 6. Filter DSL

A screen is `{"logic": "AND"|"OR", "conditions": [...], "as_of": "latest"|ISO-date}`. Validation is strict: unknown condition types, unknown fields, bad operators, and weekly-timeframe use of daily-only fields all raise `DSLValidationError` before any data is touched. Twenty condition types:

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
| `candle` | pattern, lookback (1) | One of 6 exact candlestick formulas (§9a) within the window. |
| `tight_range` | max_range_pct, bars (10) | N-bar high-low span as % of the window low. |
| `bb_squeeze` | percentile (20), lookback (252) | Bollinger bandwidth vs its own trailing distribution. |
| `flat_base` | bars (20), max_range_pct (12), max_from_52w_high_pct (15) | Tight range near the 52-week high (§9a). |
| `sector` | in (list of exact industry strings) | Stock's Nifty 500 industry classification is one of the given sectors. |
| `rs_percentile` | window (63), op, value | Stock's own window-bar return, ranked as a percentile (0–100) across the universe on the as-of date (§6a). |
| `sector_rank` | window (63), top **xor** bottom | Stock's sector is among the top/bottom N sectors by equal-weight window-bar momentum (§6a). |
| `gap` | direction, min_gap_pct (2.0), lookback (3) | Open vs the prior bar's close, on any bar within the window. |

`compare`, `range`, `trend`, `change`, and `cross` accept `"timeframe": "weekly"`, which routes evaluation to the weekly panel with its restricted field set.

### 6a. Cross-sectional pre-pass

`rs_percentile` and `sector_rank` need context beyond a single symbol's panel, so `run_screen` (and webapp's `/api/screen`) compute a **cross-sectional pre-pass** once per screen — not once per symbol — before the per-symbol loop: `cross_section.build_cross_section(panels, universe, as_of, window)` returns a table indexed by symbol with that window's return, its RS percentile, the equal-weight mean return of its sector, and the sector's momentum rank (1 = best). This is the one structural change Item 2 required: `evaluate_symbol`/`explain_symbol` gained `symbol`, `sector_by_symbol`, and `cross_section` parameters (all optional, default `None`, so every existing call site keeps working unchanged).

Design points: pure function, in-process cache only keyed by `(id(panels), as_of, window)` — no disk state, cheap enough (single-digit milliseconds over 500 symbols × 5y; measured well under the 5s budget) that a persistent cache isn't worth the invalidation complexity in v1. Thin-history symbols (fewer than `window` bars up to `as_of`) get `ret_pct = NaN` and are excluded from `rank(pct=True)` rather than defaulted to the 0th/100th percentile — pandas keeps NaN as NaN through ranking, so this falls out of the implementation rather than needing a special case. Historical as-of correctness: every value is recomputed from the row at or before `as_of`, never sliced from "latest" — verified by a dedicated look-ahead test (same spirit as the pivot-confirmation test in §8): two symbols whose relative sector performance flips between an early date and today rank in opposite order at each date.

`sector_rank`'s `bottom` (the mirror of `top`, for "laggard" screens) is a controlled extension beyond the two DSL condition shapes literally spec'd for Item 2 — `top`/`bottom` are mutually exclusive, validated the same way `trend`'s up/down and `cross`'s above/below are.

**"Taking support at a moving average"** — the phrase that motivated the whole design — is defined as: within the lookback window, (a) the low touched the MA (came within tolerance, or pierced below it), (b) the close held at or above MA × (1 − tol) on *every* bar of the window, and (c) the latest close is strictly above the MA. Condition (b) is what separates a bounce from a breakdown: a stock that slices through the MA and closes 3% below it has touched the MA but not taken support. Condition (c) requires the bounce to have actually happened, excluding stocks still sitting on the level.

**Tolerance calibration (2026-07-05):** ran `support_50ema_uptrend` across four as-of dates (2026-04-01, 05-01, 06-01, 07-03), 3 matches each. Decision: keep the 1.5% / 3-bar default. Revisit if a larger sample or the near-miss lists suggest it's too tight or too loose in live use.

The `describe()` function renders any validated spec back to plain English; the CLI prints this before every run and `--dry-run` stops there. This is the primary trust mechanism.

## 7. NL parser

Single Anthropic API call (`claude-sonnet-4-6`, temperature default) with a system prompt containing the full schema, the exhaustive allowed-field list, and the canonical vocabulary. Key mappings: uptrend/downtrend → `trend`; "taking support at / bouncing off <MA>" → `support_at_ma` (the user naming an MA distinguishes this from horizontal `near_support`); "near X" → `proximity` tol 2.0; golden/death cross → EMA50/EMA200 cross, lookback 5; volume spike → ratio 1.5, "huge/massive" → 2.5; oversold/overbought → RSI ≤30 / ≥70; "near 52-week high" → distance ≥ −5%; "strong trend" → ADX ≥ 25; "outperforming the Nifty" → `rel_strength` 63-bar > 0; "weekly uptrend" → weekly trend condition; "<sector> stocks"/"in the <sector> sector" → `sector` (common short forms like "IT" or "pharma" map to the exact industry string — see §6a — but only when the prompt's fixed sector list has an unambiguous match); "RS above N" → `rs_percentile` window 63, op ≥; "market leaders"/"top sector" → `sector_rank` top 3; "lagging sector" → `sector_rank` bottom 3. Time words map as 1 week = 5 bars, 1 month = 21, 3 months = 63 (weekly: 4 and 13).

Two traps are encoded explicitly in the prompt. First, "breakout above 52-week high" must **not** compile to `close > high_52w` — the rolling high includes today, so that comparison is nearly unsatisfiable; it maps to `pct_from_52w_high ≥ −0.5` instead. Second, bare "moving average" defaults to EMA; "DMA" or "simple" selects SMA.

Hard parser rules: output raw JSON only; never invent thresholds beyond canonical defaults unless the user states them; AND unless the user says or/either; return `{"error": "..."}` for anything unmappable (fundamentals, news, intraday, or a sector adjective that doesn't clearly match one of the fixed industry strings). Parser output still passes through `dsl.validate()` — the LLM is never trusted to be well-formed.

## 8. Swing support/resistance methodology

Pivot detection uses the fractal method: bar j is a pivot high when its high is the maximum of bars [j−k, j+k], k = 5 (mirror for lows). The final k bars of any window can never confirm a pivot because the right side of the fractal hasn't printed — this is the standard confirmation lag and doubles as the look-ahead guard, verified by a dedicated test.

Levels are built per symbol, per as-of row, from pivots in the trailing 250 bars: pivot prices are sorted and greedily clustered (a price joins the running cluster if within 1.0% of the cluster's last member); each cluster's mean is a candidate level with a touch count; levels with fewer than 2 touches are discarded as noise. Nearest support is the highest surviving level at or below the close; nearest resistance is the lowest above it.

`breakout_resistance` computes levels **as of `lookback` bars ago** (so the breakout move itself cannot create or erase the level being broken), takes the nearest resistance from that vantage point, and requires the current close to exceed it by the optional buffer. This is intentionally conservative; the documented limitation is that very recent consolidations (younger than pivot confirmation + lookback) can't produce breakout signals yet.

Parameter choices (k=5, 250-bar lookback, 1% cluster tolerance, 2-touch minimum) are defensible defaults, not optimised values — they live in `sr.py` constants and are candidates for sensitivity analysis in Phase 3.

## 9a. Pattern definitions

Candlestick and consolidation conditions use these exact formulas (bar j; body = |close−open|, range = high−low, wicks measured from body edges). **Inside bar**: high < previous high AND low > previous low. **NR7**: range is the strict minimum of the last 7 bars. **Bullish engulfing**: previous bar red, current bar green, current open ≤ previous close and current close ≥ previous open (bodies only; wicks ignored). **Bearish engulfing**: exact mirror. **Hammer**: lower wick ≥ 2× body AND upper wick ≤ 30% of range. **Shooting star**: mirror. `candle` conditions accept a lookback (default 1 = latest bar only). **Tight range**: (max high − min low) / min low over N bars ≤ threshold. **BB squeeze**: current Bollinger bandwidth ≤ the given percentile of its own trailing 252-bar distribution (≥60 observations required, else fails closed). **Flat base**: tight range over 20 bars (≤12%) AND close within 15% of the 52-week high — the pre-breakout base structure. These definitions are deliberately strict and singular; trader phrases map to them via the canonical vocabulary or not at all.

A curated preset library (`presets.py`, 19 named screens grouped by intent — trend continuation, breakouts, reversals, relative strength, bearish) exposes ready-made specs via `GET /api/presets` (web dropdown), `screener presets`, and `screener screen --preset <id>`. Every preset is validated at import time, so DSL changes that break a preset fail the test suite immediately.

## 9. Evaluator semantics

Conditions are pure functions `(panel, condition, row_index) → bool`. NaN anywhere in a condition's inputs yields False (a stock with insufficient history cannot match — it is never accidentally included). `as_of` resolves to the last row at or before the given date, independently for daily and weekly panels. `logic` applies flat AND/OR across conditions; nested boolean trees are explicitly out of scope for v1 (the parser refuses queries requiring them). The runner applies the liquidity gate, evaluates each symbol, and emits a result row of screening context: close, % vs EMA50, RSI, volume ratio, 1M/3M returns, distance from 52-week high, plus name and industry from the universe file, sorted by 3-month return.

## 10. Testing strategy

Three layers. **Synthetic-series unit tests** (90 passing): constructed price paths with known correct answers — an engineered pullback-to-EMA50 must match `support_at_ma` while a breakdown through the same EMA must not; a range-bound series must yield levels at both extremes and match `near_support` while rejecting `near_resistance`; a golden cross is verified at its exact bar; pivots must not exist in the final k bars; relative strength must reject a stock benchmarked against itself; a missing benchmark must raise; a synthetic multi-sector universe with engineered momentum dispersion drives the cross-sectional pre-pass tests, including a dedicated look-ahead test (two sectors whose relative performance flips between an early as-of date and today must rank in opposite order at each date). **Golden-query suite** (19 fixtures): hand-verified query → expected-DSL pairs, including two mandatory refusals (a P/E query, an unmapped sector name). The offline half runs in CI and asserts every fixture still validates as the DSL evolves; the live half (`python -m tests.golden_harness`, needs an API key) scores the actual parser with semantic comparison — defaults filled, conditions sorted — and must be 19/19 before any parser-prompt change ships. **Live-data sanity checks** (manual, first run): backfill symbol count vs 500; spot-check three symbols' EMA50/RSI against a charting platform; run the flagship query and eyeball charts of the matches.

## 11. Operations runbook

First run: create a Python 3.10+ venv (`python3.12 -m venv .venv && source .venv/bin/activate` — both entry points guard against older interpreters and print this fix), `pip install -r requirements.txt`, set `ANTHROPIC_API_KEY`, `python -m screener.cli backfill` (10–15 min), then `python -m screener.cli verify` — an automated 13-check health report (coverage vs index list, freshness, depth, bar integrity, duplicates, corporate-action smell test via >40% single-day move counts, benchmark presence, RSI/EMA spot checks on a 25-symbol sample, the data-layer-v2 cross-source check §4a, and screen-log JSONL integrity) that exits non-zero on FAIL for pipeline gating; `verify --jumps` lists the exact bars behind the adjustment smell test with split-ratio hints, and `refetch SYMBOL` drops and freshly re-downloads one symbol as the unadjusted-data remedy. Nightly: cron `python -m screener.cli update` after 18:30 IST (add `bhavcopy-update` alongside it once side-by-side collection should run unattended — see §4a). Quarterly: refresh the universe and re-backfill to pick up rebalances. Screens: `screen "<text>"`, `--dry-run` to inspect the compiled spec, `--json` to bypass the LLM, `--as-of YYYY-MM-DD` to replay a historical date (CLI parity with the web UI's date picker), `--out file.csv` to export. Failure modes: stale-store error → run update; universe fetch fails → cached list used automatically, manual CSV drop-in as last resort; yfinance thin/missing data for a symbol → excluded by the 60-bar minimum, check the alias problem in §4; parser refusal → the query needs rephrasing within scope, or the concept belongs on the roadmap.


## 12. Web UI and evidence layer

`python -m screener.webapp` serves a single-page interface (FastAPI backend, dependency-free vanilla-JS frontend in `web/index.html`, port 8501) built around full auditability of every screen. The page always shows, together: the original query, the plain-English compiled interpretation, the raw JSON spec (collapsible), the data as-of date and mode, and funnel statistics (universe → liquidity-excluded → evaluated → matched).

**Screen definition** offers three routes: plain-English (LLM parse with mandatory interpretation echo), raw JSON spec, and a grouped **preset dropdown** backed by `presets.py` (§9a) via `GET /api/presets`, which shows each preset's rationale and compiled English before running. An **as-of date picker** replays any historical date; when set, condition evaluation, match metrics, and sparkline windows are all computed at that row (never the latest bar), preserving the reproducibility guarantee for historical screens. Note the survivorship caveat from §4 applies to any historical replay.

The core of the UI is the **evidence trail**, powered by `explain.py`. For every condition on every displayed stock it reports pass/fail plus the observed values behind the decision — e.g. for `support_at_ma`: the date the low touched the MA and the touch distance, the worst close-vs-MA excursion across the window, and the latest close's margin above the MA. Pass/fail is delegated to the same `evaluator.cond_*` functions used by the screen itself, so the explanation can never disagree with the result; `explain.py` only adds observability. Each match card also renders an **evidence sparkline**: an inline SVG of the last 60 bars (up to the as-of row) overlaying exactly the series the spec referenced (EMAs, bands, 52-week levels) and every horizontal level the evidence produced (swing support/resistance, breakout levels) — so verifying a touch or a level does not require leaving the page. A **near-miss** section lists stocks failing exactly one condition with the failing condition marked ✗ — the most useful feedback for tuning tolerances; a toggle button hides/shows it without losing the funnel-stat count. Match cards are capped at `MAX_MATCHES` (100) in the payload — `stats.matched` always reports the true total, so a loose filter's size is never hidden, just not rendered as hundreds of DOM cards; a note above the results explains the cap when it's active. A client-side **CSV export** button downloads the currently-displayed matches (symbol, name, industry, and every snapshot metric) without a dedicated export endpoint, since the data is already in the page.

Every run is appended to `data/screen_log.jsonl` — timestamp, as-of date, full spec, its plain-English description, funnel stats, matched symbols — turning the deterministic-replay guarantee into a browsable history (`GET /api/log`, or `python -m screener.cli log`). Log writes never raise; a failed write cannot break a screen. A **recent screens** panel in the UI fetches this log and lets you replay any past run with one click (restores the spec into the JSON tab and the as-of date), refetching whenever a new screen completes so it never shows stale entries.

Endpoints: `GET /api/status`, `GET /api/presets`, `GET /api/log`, `POST /api/parse` (text → spec via the LLM; structured error without an API key), `POST /api/screen` (spec → matches + near-misses + sparklines + methodology block). When `data/prices.parquet` is absent the backend boots a labelled synthetic 8-stock demo universe (`demo.py`) with engineered behaviours, so the UI, API, and evidence layer are fully testable — in CI and by a fresh clone — without market data.

## 13. Roadmap

Completed from the Phase 3 plan: pattern/consolidation conditions with the preset library (item 1), the workflow layer — as-of replay, evidence sparklines, screen log (item 4) — and **sector and relative-strength extensions (item 2)**: industry as a filter (`sector`), per-stock RS percentile vs the universe (`rs_percentile`), and equal-weight sector momentum ranking (`sector_rank`, top or bottom N), all built on the cross-sectional pre-pass (§6a). **In progress: (3) NSE bhavcopy migration** (§4a) — the ingestion, delivery %, and corporate-action adjustment pipeline are built and validated against live data; a `verify` cross-source consistency check is live and side-by-side data collection has started (2026-07-05). What's left is calendar-gated, not code-gated: ~2 weeks of side-by-side evidence before cutover can even be considered, then the `delivery` DSL condition/vocabulary/preset (deliberately deferred until after cutover), the config-flag cutover itself, and adding `bhavcopy-update` to the nightly cron. **Parked**: screen backtesting (event-study on any spec: historical signal dates, de-duplicated forward-return distributions at 5/20/60 bars vs universe baseline, tolerance sensitivity grids; the as-of machinery it needs is already in place, and the cross-sectional pre-pass is groundwork it will reuse) — revisit after item 3 cuts over. **Deferred indefinitely**: nested boolean logic (until a real query demands it), monthly timeframe (weekly machinery generalises trivially), intraday (out of scope by design).

## 14. Changelog

0.1 — Data layer (Nifty 500, yfinance 5y, Parquet), daily indicator engine, 8-condition DSL with validation and English echo, LLM parser with canonical vocabulary, CLI, 16 synthetic tests.
0.6.2 — ROADMAP Item 4 (small backlog) cleared: `gap` DSL condition + parser vocabulary + golden fixture; 2 new presets (`weekly_squeeze`, `gap_up_followthrough` — 19 total); CLI `screen --as-of` flag; `verify` screen-log JSONL integrity check; web UI — CSV export, near-miss hide/show toggle, match-count cap (100) with an explanatory note, and a "recent screens" panel that replays any logged run with one click (cache-invalidates itself after every new run, caught and fixed during live browser testing). All UI changes driven and screenshotted against the live server via a temporary Playwright harness. 8 new tests. 91 tests.
0.6.1 — Data layer v2 groundwork (ROADMAP Item 3, pre-cutover): `bhavcopy.py` — NSE `sec_bhavdata_full` ingestion (OHLCV + delivery %, unadjusted), corporate-actions fetch + regex-based split/bonus adjustment-factor pipeline (dividends excluded by design), side-by-side store (`data/bhavcopy_prices.parquet`, not read by any screen yet); `verify.check_cross_source` folded into `python -m screener.cli verify`; new `bhavcopy-update` CLI command. Validated against live NSE data: one day's bhavcopy matched all 500 yfinance closes to ~1e-6%; cross-source check on the first week of real side-by-side data found only the already-documented dividend-adjustment divergence. 19 new tests. 83 tests.
0.6 — Sector filters & cross-sectional relative strength: `sector`, `rs_percentile`, `sector_rank` conditions; `cross_section.py` pre-pass (RS percentile, equal-weight sector momentum, NaN-safe ranking, look-ahead-safe as-of); 3 new presets (sector-leader pullback, RS-leader near high, lagging-sector bounce); parser vocabulary + 4 new golden fixtures (14 → 18, incl. a sector-refusal case). 64 tests.
0.5 — As-of date picker with as-of-correct metrics and spark windows, evidence sparklines (spec-referenced series + evidence levels on a 60-bar inline chart), append-only screen log (JSONL + /api/log + CLI `log`). 50 tests.
0.4 — Pattern conditions (candle: inside_bar/nr7/engulfing×2/hammer/shooting_star; tight_range, bb_squeeze, flat_base) with exact documented formulas, explainers, parser vocabulary; 14-screen preset library with grouped web-UI dropdown, /api/presets, CLI presets/--preset; 2 new golden fixtures. 47 tests.
0.3.2 — `verify --jumps` diagnostic (lists smell-test bars with split-ratio classification hints) and `refetch SYMBOL` remedy command. 37 tests.
0.3.1 — `verify` command automating the §10 checklist (verify.py, 3 tests), Python≥3.10 guards on entry points, README first-run checklist and troubleshooting. 36 tests.
0.3 — Web UI: FastAPI backend + single-page evidence-trail frontend, explain.py observability layer, near-miss reporting, synthetic demo mode, 33 tests.
0.2 — Swing-pivot S/R (`near_support`, `near_resistance`, `breakout_resistance`), weekly timeframe on 5 condition types, `rel_strength` vs Nifty with benchmark ingestion, golden-query suite (12 fixtures, offline + live harness), 27 tests total, this document.
