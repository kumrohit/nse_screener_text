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
labelled 8-stock demo universe so you can explore the interface first.

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
the compiled JSON spec) before running; paste a raw **JSON spec**; or pick
from the **preset dropdown** — 14 curated screens grouped by intent (trend
continuation, breakouts, reversals, relative strength, bearish), each shown
with its rationale and compiled English, no API key needed.

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
charting platform. A **near-misses** section shows stocks that failed
exactly one condition, so you can see the boundary of your filter.

Every run is logged to `data/screen_log.jsonl` (spec + as-of date + matches
— the full replay trail); browse it with `python -m screener.cli log`.

With no price store yet, the app boots into a labelled 8-stock demo
universe so everything above is explorable immediately after clone.

## CLI usage

```bash
# plain English
python -m screener.cli screen "oversold stocks near their 52 week low"
python -m screener.cli screen "golden cross with a volume spike"
python -m screener.cli screen "uptrend on the weekly chart but oversold on the daily"
python -m screener.cli screen "breaking out above resistance and outperforming the Nifty"

# see how a query was interpreted without running it
python -m screener.cli screen --dry-run "strong trend stocks pulling back to the 20 EMA"

# save results
python -m screener.cli presets                  # list pre-configured screens
python -m screener.cli screen --preset flat_base_52w
python -m screener.cli screen --out hits.csv "near support with huge volume"
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
| "up 10% in a month", "between 40 and 60" | explicit numbers pass straight through |

Anything it can't map to the documented vocabulary — P/E ratios, news, "good management" — returns an explicit error instead of a silently wrong screen.

## Data

Nifty 500 constituents from NSE's official index CSV; daily OHLCV from Yahoo Finance (`.NS`), split/bonus-adjusted; Nifty 50 index for relative strength. Before any screen runs the tool checks the store isn't stale and applies a liquidity gate. Known caveats (yfinance reliability, a few NSE↔Yahoo ticker mismatches, survivorship in historical screens) are documented in [TECHNICAL_DESIGN.md §4](TECHNICAL_DESIGN.md).

## Tests

```bash
python -m pytest tests/                    # 50 tests: synthetic series with known answers,
                                           # evidence-layer agreement, web API contract
python -m tests.golden_harness             # live parser scoring vs 14 hand-verified queries
```

CI runs the offline suite on every push. The live harness gates any change to the parser prompt: 14/14 or it doesn't ship.

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
