# NSE Text Screener

**Screen Nifty 500 stocks in plain English.**

```
$ python -m screener.cli screen "stocks taking support at 50 EMA and in an uptrend"

Screening for: took support at EMA 50 within last 3 bars (low touched within 1.5%,
close held above) AND uptrend (close > EMA50 > EMA200, EMA50 rising)

As of 2026-07-03 — 14 matches

symbol   close  pct_vs_ema50   rsi  vol_ratio  ret_1m_pct  ret_3m_pct  ...
...
```

You describe the setup the way you'd say it to another trader; the tool compiles it into a strict, deterministic filter, echoes its interpretation back so you can verify it understood you, and screens five years of daily NSE data. Price and volume technicals only — it will refuse fundamentals rather than guess.

Use it from the **web UI** (recommended — every match comes with a per-condition evidence trail showing the exact values behind the decision) or the CLI shown above.

## How it works

```
your text ──► LLM parser ──► JSON filter spec ──► validator ──► evaluator ──► results
              (canonical        (the "DSL")        fails loud     pure pandas,
               vocabulary)                         on anything    fully unit-tested
                                                   ambiguous
```

The one rule that makes this trustworthy: **natural language never touches the computation.** The LLM only translates text into a validated spec. Every screen is reproducible from that spec plus the data date, "support" and "uptrend" have exact documented definitions, and you can bypass the LLM entirely with `--json`. Full rationale, formulas, and semantics: [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md).

## Setup

Runs on your local machine (NSE and Yahoo block datacenter IPs). Python 3.10+.

```bash
git clone <your-repo-url> && cd nse-text-screener
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # only needed for plain-English queries

python -m screener.cli backfill            # one-time: Nifty 500 list + 5y history (~10-15 min)
```

No data yet? `python -m screener.webapp` works immediately — it boots into a
labelled 11-stock demo universe so you can explore the interface first.

Keep it fresh with a nightly job (after 18:30 IST, once NSE close data settles):

```bash
python -m screener.cli update
```

## First live run (checklist)

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # once
pip install -r requirements.txt
python -m screener.cli backfill     # ~10-15 min
python -m screener.cli verify       # automated data health report
python -m screener.webapp           # then open http://127.0.0.1:8501
```

`verify` runs 11 checks — symbol coverage vs the index list, freshness,
history depth, bar integrity, duplicates, a corporate-action "smell test",
benchmark presence, and indicator spot checks — and exits non-zero on any
FAIL so it can gate a cron pipeline (`... update && ... verify && ...`).

## Web UI

```bash
python -m screener.webapp        # http://127.0.0.1:8501
```

Three ways to define a screen: type it in **plain English** and hit
**Interpret query** to see exactly how it was understood (plain English +
the compiled JSON spec, plus any canonical defaults the parser filled in
that you didn't state explicitly) before running; paste a raw **JSON
spec**; or pick from the **preset dropdown** — 19 curated screens grouped
by intent (trend continuation, breakouts, reversals, relative strength,
bearish), each shown with its rationale and compiled English, no API
key needed.

An **as-of date picker** replays any historical date — evaluation, metrics,
and charts all reflect that date, so you can see exactly what a screen
would have shown last month (current-constituent universe, so historical
replays carry survivorship bias — see the design doc).

Every match expands into an **evidence trail**: each condition with a ✓/✗
and the observed values behind it — which bar touched the EMA and how
close, the pattern date and its OHLC, the cross date, the stock-vs-Nifty
return gap — plus an **evidence sparkline**: a 60-bar mini-chart overlaying
exactly the series the spec referenced and the support/resistance levels
the evidence found, so verifying a setup doesn't require switching to your
charting platform. A **near-misses** section (with a hide/show toggle)
shows stocks that failed exactly one condition, so you can see the
boundary of your filter. Large result sets are capped at 100 displayed
matches with a note showing the true total — **export matches to CSV**
downloads whatever's currently displayed. Match cards also carry a
small **⚠ data-quality badge** when something's worth a second look —
an unadjusted-looking jump within the chart window, thin history, or a
symbol that's stopped updating (possibly suspended) — with the reason
in a tooltip.

Every run is logged to `data/screen_log.jsonl` (spec + as-of date + matches
— the full replay trail; rotates to `screen_log.rotated.jsonl` past 5,000
entries); browse it with `python -m screener.cli log`, or from the UI's
**recent screens** panel, which replays any past run with one click
(restores the spec and as-of date). The methodology footer shows a short
config hash — a screen is only truly reproducible together with the
tunables that produced it (see [Configuration](#configuration) below),
not the spec alone.

A **"since last run" badge** shows what changed for the *same* screen
criteria (new matches, dropped ones — with the exact condition that now
fails), regardless of key order or as-of date. Click **full chart ⤢** on
any match for a large candlestick modal (250 bars, volume, all
spec-referenced overlays, drag-to-zoom). **☆ watch** a match to track it
on the **watchlist** — current price, % move since you tagged it, and
whether the original conditions still hold today. **💾 save as my
screen** persists any spec you've compiled as a named, reusable preset
(validated the same way built-ins are, rejected on save not on run) —
manage or delete them from **manage my screens**. Run several presets
at once from the **📊 dashboard** — one table of match counts, top-3
symbols, and new-since-last-run per screen, the morning view. Results
support client-side **sort** and **sector filter chips** with a sticky
header, so browsing a hundred matches doesn't mean losing your place.

With no price store yet, the app boots into a labelled 11-stock demo
universe so everything above is explorable immediately after clone.

## CLI usage

```bash
# plain English
python -m screener.cli screen "oversold stocks near their 52 week low"
python -m screener.cli screen "golden cross with a volume spike"
python -m screener.cli screen "uptrend on the weekly chart but oversold on the daily"
python -m screener.cli screen "breaking out above resistance and outperforming the Nifty"
python -m screener.cli screen "IT stocks with RS above 80 near their 52 week high"

# see how a query was interpreted without running it
python -m screener.cli screen --dry-run "strong trend stocks pulling back to the 20 EMA"

# save results
python -m screener.cli presets                  # list pre-configured screens
python -m screener.cli screen --preset flat_base_52w
python -m screener.cli screen --out hits.csv "near support with huge volume"
python -m screener.cli screen --as-of 2026-06-01 "gapped up on volume"
python -m screener.cli log                      # recent runs (replay trail)

# power users: raw spec, no LLM, no API key
python -m screener.cli screen --json '{"logic":"AND","conditions":[
  {"type":"trend","direction":"up"},
  {"type":"range","field":"rsi","max":40}]}'
```

## What it understands

| You say | It screens for |
|---|---|
| "taking support at 50 EMA" | low touched EMA50 (±1.5%) in last 3 bars, close held above, latest close above |
| "uptrend" | close > EMA50 > EMA200 with EMA50 rising |
| "golden cross" / "death cross" | EMA50/EMA200 cross within 5 bars |
| "oversold" / "overbought" | RSI ≤ 30 / ≥ 70 |
| "volume spike" / "huge volume" | volume ≥ 1.5× / 2.5× its 20-day average |
| "near 52-week high" | within 5% of the 52-week high |
| "strong trend" | ADX ≥ 25 |
| "near support" / "near resistance" | close within 2% of the nearest swing-pivot level |
| "breaking out above resistance" | close crossed a prior multi-touch resistance level |
| "outperforming the Nifty" | 3-month return above the Nifty 50's |
| "on the weekly chart" | condition evaluated on weekly bars |
| "inside bar", "NR7", "hammer", "bullish engulfing"… | exact candlestick formulas (see design doc §9a) |
| "consolidating" / "tight range" | 10-bar range ≤ 8% |
| "volatility squeeze" / "coiling" | Bollinger bandwidth in bottom 20% of its year |
| "flat base" | 20-bar range ≤ 12% within 15% of the 52-week high |
| "gapped up" / "gapped down" | open ≥2% away from the prior close, within last 3 bars |
| "IT stocks" / "in the Healthcare sector" | exact Nifty 500 industry classification match |
| "RS above 80" | stock's 3-month return ranks in the 80th percentile of the universe |
| "market leaders" / "top sector" | stock's sector is in the top 3 by 3-month equal-weight momentum |
| "lagging sector" | stock's sector is in the bottom 3 by 3-month equal-weight momentum |
| "up 10% in a month", "between 40 and 60" | explicit numbers pass straight through |

Anything it can't map to the documented vocabulary — P/E ratios, news, "good management" — returns an explicit error instead of a silently wrong screen.

## Data

Nifty 500 constituents from NSE's official index CSV; daily OHLCV from Yahoo Finance (`.NS`), split/bonus-adjusted; Nifty 50 index for relative strength. Before any screen runs the tool checks the store isn't stale and applies a liquidity gate. Known caveats (yfinance reliability, a few NSE↔Yahoo ticker mismatches, survivorship in historical screens) are documented in [TECHNICAL_DESIGN.md §4](TECHNICAL_DESIGN.md).

An NSE bhavcopy-based data layer (official OHLCV + delivery %, our own corporate-action adjustment) is being built and validated **side by side** with the store above via `python -m screener.cli bhavcopy-update` — it isn't used by any screen yet. See [TECHNICAL_DESIGN.md §4a](TECHNICAL_DESIGN.md).

## Configuration

Every tunable (liquidity gate, staleness window, support/resistance pivot and clustering parameters, spark-chart bar count, match-display cap) lives in `screener/config.py` and can be overridden without code edits by dropping a `data/config_local.toml`:

```toml
MIN_MEDIAN_TURNOVER_CR = 1.0   # stricter liquidity gate
SR_CLUSTER_TOL_PCT = 1.5       # wider support/resistance clustering
```

Unknown keys are flagged and ignored rather than silently doing nothing. The effective config is hashed and recorded with every screen (log entry + methodology footer), so a result is only ever reproducible together with the config that produced it.

## Tests

```bash
python -m pytest tests/                    # 137 tests: synthetic series with known answers,
                                           # evidence-layer agreement, web API contract
python -m tests.golden_harness             # live parser scoring vs 19 hand-verified queries
```

CI runs the offline suite on every push. The live harness gates any change to the parser prompt: 19/19 or it doesn't ship (also runnable as a manual GitHub Actions job — `.github/workflows/golden-harness.yml`).

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the current execution checklist; design rationale for upcoming work lives in [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md).

## Troubleshooting

**`ModuleNotFoundError: No module named 'fastapi'` or a traceback showing Python 3.9**
You're running macOS's Command Line Tools Python; this project needs 3.10+
(the entry points now detect this and print the fix). Create a venv with a
modern interpreter: `brew install python@3.12`, then
`python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
Remember to activate the venv in every new terminal session.

**`Price store stale` error** — run `python -m screener.cli update`.

**`verify` warns about single-day moves >40%** — run
`python -m screener.cli verify --jumps` to see the exact bars. Rows hinted
"split-like ratio" are unadjusted corporate actions: fix with
`python -m screener.cli refetch SYMBOL` (a fresh fetch usually comes back
adjusted). Rows with no clean ratio are usually genuine events — verify
against the news and leave them.

**Backfill finished but far fewer than 500 symbols** — run
`python -m screener.cli verify`; the coverage check names the missing
symbols (usually NSE↔Yahoo ticker mismatches — see TECHNICAL_DESIGN.md §4).

## Disclaimer

This is a research and screening tool, not investment advice. Free data sources carry no guarantees; verify anything before trading on it.

## License

MIT
