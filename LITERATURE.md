# Literature review — evidence behind the strategy presets

This document is the source of truth for ROADMAP Item 9. It is written
*before* the presets it justifies — a preset that can't point to a section
here doesn't ship as "evidence-based." Each section states: the canonical
finding, its magnitude/robustness, India-specific evidence where it exists,
known decay/cost caveats, and the exact DSL mapping chosen. The `evidence`
object on each preset (`screener/presets.py`) points back here by strategy
name.

Scope discipline: this reviews *price/volume* anomalies only, consistent
with the project's no-fundamentals-no-events constraint
(TECHNICAL_DESIGN.md §1). It is not exhaustive finance literature — it is
the eight families the presets actually implement, chosen because each has
a specific, replicable price/volume construction and (mostly) India-market
corroboration. Basis labels used throughout: **academic** (peer-reviewed,
replicated), **practitioner** (documented methodology, weak/no independent
academic validation), **mixed** (an academic phenomenon operationalized via
a practitioner rule-of-thumb).

---

## 1. Cross-sectional momentum

**Basis:** academic.

**Core finding.** Jegadeesh & Titman (1993, *Journal of Finance*, "Returns
to Buying Winners and Selling Losers") — stocks ranked by trailing 3–12
month returns and rebalanced into winner/loser deciles show winners
continuing to outperform losers by ~1% per month over the following
3–12 months, in US data 1965–1989. One of the most replicated anomalies in
finance, though not without regime-dependent failures (see caveat below).

**The 12-1 convention.** The lookback used is 12 months, but the *most
recent* month is excluded. This isn't arbitrary: Jegadeesh (1990, "Evidence
of Predictable Behavior of Security Returns") found strong short-term
*reversal* at the 1-month horizon — stocks with the best/worst return over
the last calendar month tend to partially revert the next month. Skipping
it separates momentum (persists) from short-term reversal (mean-reverts) so
they don't cancel each other out in the ranking.

**India evidence.** Sehgal & Balakrishnan (2002, "Contrarian and Momentum
Strategies in the Indian Capital Market") find momentum profits in Indian
equities over 3–12 month formation/holding periods, consistent with the US
result — the anomaly isn't a US-only artifact. As practitioner
corroboration (not academic, but a real-money signal that the classification
has traction in this market): NSE's own **NIFTY200 Momentum 30 Index**
methodology ranks constituents on a similar risk-adjusted 6/12-month
momentum score and rebalances semi-annually — evidence the construction is
taken seriously by the exchange itself, not just academics.

**Caveat — momentum crashes.** Daniel & Moskowitz (2016, "Momentum
Crashes") document that momentum strategies suffer large, clustered
crashes following market downturns, when past losers rebound sharply
("bear market rebound" effect) — the strategy's biggest losses come exactly
when a long-only screener applying it would least expect them, right after
a selloff. This screener is long-only and doesn't include the volatility-
scaling or dynamic exposure control the crash-mitigation literature uses,
so the caveat is surfaced verbatim in the preset's evidence panel, not
engineered around.

**DSL mapping.** `mom_12_1` field (t−252 to t−21 close return, i.e. the
skip-month construction) ranked cross-sectionally via
`{"type":"rs_percentile","basis":"mom_12_1","op":">=","value":80}` —
top-quintile momentum, combined with a liquidity floor so the ranking isn't
contaminated by illiquid names with noisy returns.

---

## 2. 52-week-high anchoring

**Basis:** academic.

**Core finding.** George & Hwang (2004, *Journal of Finance*, "The 52-Week
High and Momentum Investing") show that a stock's *nearness to its 52-week
high* predicts future returns, and does so *better* than trailing-return
momentum (family #1) in their tests — stocks near their 52-week high
continue to outperform, consistent with an anchoring-and-adjustment
explanation: investors underreact to news that pushes a stock toward a new
high, treating the old high as a reference point they're reluctant to
revise past.

**Why it's a distinct family, not a duplicate of momentum.** George & Hwang
explicitly show the 52-week-high signal subsumes trailing-return momentum
in explanatory power in their sample — a stock can be near its 52-week high
without having the highest trailing return (e.g., a steady grinder vs. a
recent sharp mover), so the two rankings select different names in
practice even though both are trend-following in spirit.

**India evidence.** No India-specific replication of George & Hwang located
for this review — flagged honestly in the preset's evidence panel as
"academic finding, developed-market sample; not independently confirmed in
Indian data at time of writing" rather than implied to be locally verified.

**Caveat.** Like all trend/anchoring signals, performance is regime
dependent — the effect is weaker or reverses in range-bound/bearish
regimes; the original paper's sample is 1963–2001 NYSE/AMEX/NASDAQ, a
different market structure and liquidity regime than modern NSE.

**DSL mapping.** `{"type":"range","field":"pct_from_52w_high","min":-5}`
(within 5% of the 52-week high) combined with a relative-strength floor
(`rs_percentile >= 60`) so proximity to the high isn't confused with a
stock that merely hasn't fallen as far as the market — the existing
`high_momentum_52w` preset already implements this combination and is
annotated retroactively rather than duplicated.

---

## 3. Time-series (trend-following) momentum

**Basis:** academic.

**Core finding.** Moskowitz, Ooi & Pedersen (2012, *Journal of Financial
Economics*, "Time Series Momentum") — unlike cross-sectional momentum
(ranking stocks against each other), time-series momentum asks whether *an
asset's own* trailing 12-month return predicts its own future return,
independent of how it ranks against peers. Documented across 58 futures
and equity index contracts, 1965–2009: positive trailing 12-month return →
positive expected future return, and vice versa.

**The practitioner long-form.** Faber (2007, "A Quantitative Approach to
Tactical Asset Allocation") popularized a simple implementation: hold an
asset when its price is above its 10-month simple moving average, exit
below. This is functionally the same regime filter as the "close above
200-day EMA" trend definition already used throughout this screener's
`trend` condition (10 months ≈ 200 trading days) — time-series momentum is
the academic grounding for a rule this codebase already implements, not a
new mechanism.

**India evidence.** Not independently reviewed for this document — the
underlying construction (price vs. long moving average as a regime filter)
is standard enough globally that it's treated as a generic, not
India-specific, mechanism; flagged as such in the evidence panel.

**Caveat.** Time-series momentum, like cross-sectional momentum, has
crash risk around sharp reversals and whipsaws in choppy/range-bound
markets — a 200-day filter is slow to react by construction, which is the
tradeoff for fewer false signals.

**DSL mapping.** `{"type":"compare","left":"close","op":">","right":"sma_200"}`
combined with a positive 12-month return
(`{"type":"range","field":"roc_252","min":0}`) — the regime filter *and*
the raw time-series momentum signal it's grounded in, both required.

---

## 4. Moving-average trading rules

**Basis:** mixed — real anomaly, contested after-cost profitability, and
a documented regime dependency worth taking seriously.

**Core finding.** Brock, Lakonishok & LeBaron (1992, *Journal of Finance*,
"Simple Technical Trading Rules and the Stochastic Properties of Stock
Returns") tested variable-length moving-average and trading-range-breakout
rules on the Dow Jones Industrial Average, 1897–1986, and found buy signals
followed by higher, lower-volatility returns than sell signals — a result
that survived bootstrap tests against several null models of the return
process (random walk, AR(1), GARCH-M, EGARCH).

**Where it's strongest.** Han, Yang & Zhou (2013, "A New Anomaly: The
Cross-Sectional Profitability of Technical Analysis") extend this and find
MA-rule profitability concentrated in **high-idiosyncratic-volatility**
stocks — the effect that's weak-to-absent in low-volatility names is
strong in high-volatility ones, consistent with slower information
diffusion in noisier names. This is why the corresponding preset in this
codebase gates on an ATR-percentile floor rather than applying an MA rule
universe-wide.

**Honest caveat — the after-cost critique.** Sullivan, Timmermann & White
(1999, "Data-Snooping, Technical Trading Rule Performance, and the
Bootstrap") re-examined the BLL universe of rules using White's Reality
Check for data snooping and found the *best* rule from BLL's own sample no
longer beat a buy-and-hold benchmark on a fresh out-of-sample period
(1987–1996) once the multiple-testing problem (thousands of rule variants
tried, best one reported) was accounted for. This is the single most
important caveat in this entire document: a naïve reading of BLL
overstates what MA rules deliver once transaction costs and data-snooping
are priced in. It's stated in the preset's evidence panel, not hidden.

**India evidence.** Not independently reviewed for this document.

**DSL mapping.** `{"type":"trend","direction":"up"}` (the existing
close/EMA50/EMA200-slope construction) combined with
`{"type":"atr_pct_percentile","op":">=","value":70}` — MA trend-following,
restricted to the top-volatility tercile where Han-Yang-Zhou found the
effect concentrated.

---

## 5. Volume-confirmed momentum

**Basis:** academic.

**Core finding.** Lee & Swaminathan (2000, *Journal of Finance*, "Price
Momentum and Trading Volume") show trading volume predicts both the
magnitude and duration of future momentum profits, and — the more
distinctive result — **high-volume winners** show faster momentum reversal
than low-volume winners, while **low-volume losers** ("neglected firms")
earn higher future returns than high-volume losers. Volume acts as a proxy
for investor attention/information diffusion speed. For a long-only
momentum screen, the actionable slice of this is: momentum names that are
also seeing rising volume (not fading, "neglected" continuation, but
attention-confirmed continuation) is the safer long entry within the
momentum universe — this is deliberately the *simpler, more conservative*
half of Lee-Swaminathan's result to operationalize; the paper's full
volume/momentum interaction matrix has cells this preset does not attempt
to encode.

**India evidence.** Not independently reviewed for this document.

**Caveat.** The relationship between volume and momentum duration in Lee &
Swaminathan is a moderating (not a stand-alone) signal — high volume alone
predicts nothing; it's the interaction with existing relative strength that
matters, so the preset requires both.

**DSL mapping.** `{"type":"rs_percentile","basis":"return","window":63,
"op":">=","value":70}` combined with `{"type":"volume_spike",
"min_ratio":1.3}` — an elevated (not necessarily extreme) recent volume
ratio confirming an already-strong relative-strength name, rather than
volume as a stand-alone trigger.

---

## 6. Low-volatility anomaly

**Basis:** academic.

**Core finding.** Two independent, converging literatures: Blitz & van
Vliet (2007, "The Volatility Effect: Lower Risk Without Lower Return")
show globally that low-volatility stock portfolios deliver *higher*
risk-adjusted returns than high-volatility ones — the opposite of what
CAPM predicts (higher risk should earn a higher return, not a lower one).
Ang, Hodrick, Xing & Zhang (2006, *Journal of Finance*, "The Cross-Section
of Volatility and Expected Returns") independently find US stocks with
high idiosyncratic volatility earn *abysmally low* average returns — a
"volatility puzzle" that has held up across decades and markets since.

**Why it belongs in this screener.** It's the one family here that
functions as a *defensive/quality* filter rather than a momentum or
breakout signal — useful as a bucket for capital that shouldn't be chasing
the highest-momentum names, and a natural complement (not a substitute) for
the momentum families above.

**India evidence.** Not independently reviewed for this document — the
anomaly's robustness across dozens of international markets in the
original studies is treated as reasonable (not certain) grounds to expect
it generalizes, flagged as inferred rather than locally confirmed.

**Caveat.** Low-volatility strategies can lag badly in strong bull-market
momentum regimes (by construction — they avoid the highest-beta movers) and
carry their own crowding risk after a decade of "low-vol" becoming a
mainstream factor allocation globally; this is a defensive tilt, not a
higher-expected-return bet in every regime.

**DSL mapping.** `atr_pct` (ATR as % of price — this screener's existing
volatility measure) ranked cross-sectionally via
`{"type":"atr_pct_percentile","op":"<=","value":30}` — bottom-tercile
realized volatility — combined with `{"type":"compare","left":"close",
"op":">","right":"sma_200"}` so the "defensive" bucket still requires an
intact long-term trend, rather than surfacing low-volatility stocks that
are simply comatose or in a slow bleed.

---

## 7. Practitioner trend template (Minervini / O'Neil "Stage 2")

**Basis:** practitioner — explicitly labeled as weak academic support,
real survivorship-bias risk in its supporting case-study folklore (the
"market wizards" style books this template comes from select for
after-the-fact winners; that is not the same evidentiary standard as
families 1–6 above).

**What it is.** A multi-condition trend-template popularized by Mark
Minervini and, before him, William O'Neil's CAN SLIM methodology: a stock
is in a qualifying "Stage 2" uptrend when (a) price is above both its
150-day and 200-day moving averages, (b) the 150-day MA is above the
200-day MA, (c) the 200-day MA has been trending up for at least ~1 month,
(d) price is above its 50-day MA, (e) price is meaningfully above its
52-week low (≥30%, guards against a name that only just stopped falling),
and (f) price is within a reasonable distance of its 52-week high
(commonly ≤25%). It is a *stricter, more specific* trend filter than the
academic trend-following construction in family #3 — closer to a
"institutional accumulation" heuristic than a tested statistical model.

**Why include it despite the weak academic basis.** It is widely used in
practice, has a precise, replicable, fully price/volume-based definition
(no discretion required to check it), and the honesty of labeling it
"practitioner" rather than dressing it up as "backed by research" is
itself the point of this document — the alternative (leaving it out
entirely) would remove a screen many users will specifically look for by
name ("stage 2", "Minervini template").

**India evidence.** None claimed; not applicable — this is not an academic
finding to begin with.

**Caveat.** No peer-reviewed out-of-sample test of this exact multi-part
conjunction is known to the author of this document. Anecdotal
track-records built on this template are subject to survivorship bias
(the failures don't get written up) and hindsight case selection.

**DSL mapping.** `minervini_stage2` preset: `close > sma_50 > sma_150 >
sma_200`, `sma_200` rising (reuses `ema_200_slope`-style logic against
`sma_200` via `compare`... — implemented as the existing `trend` shape
generalized with explicit SMA compares), `pct_from_52w_low >= 30`
(i.e. `{"type":"range","field":"pct_from_52w_low","min":30}` — note the
field is *already negative-anchored* the same way `pct_from_52w_high` is,
so "≥30% above the low" is `pct_from_52w_low >= 30`), `pct_from_52w_high
>= -25`, and `rs_percentile >= 70` (Minervini's template is commonly paired
with an RS-rating floor in practice).

---

## 8. Consolidation breakouts (flat base)

**Basis:** practitioner, unvalidated — carried over from the existing
`flat_base_52w` preset (ROADMAP Item 4), annotated here rather than
reimplemented.

**What it is.** A tight multi-week trading range (small high/low spread)
sitting near the 52-week high, on the theory that it represents supply
being absorbed before a breakout — classic technical-analysis pattern
recognition (Darvas boxes, O'Neil "bases," and generically "flags/
pennants" all describe variants of the same idea: consolidation after a
prior advance, resolved by a breakout in the direction of the prior trend).

**Why it's here rather than in an academic family.** No controlled academic
study establishing that this specific range/proximity construction predicts
forward returns better than the trend or momentum families above was
located for this review. It is included because it's an extremely commonly
requested screen by name ("flat base", "consolidation", "tight range near
highs") — the same honesty principle as family #7: label it plainly rather
than omit a real user need or overstate its evidence.

**Caveat.** Pattern-based technical setups like this are especially
vulnerable to look-elsewhere/multiple-comparisons bias when back-tested
informally (a chart with enough bars will show *some* tight range near
*some* high eventually) — no forward-return backtest of this screener's
specific `flat_base` construction has been run (screen backtesting is
parked, see ROADMAP §3, pending enough live trust to make "did this
historically carry edge" the blocking question).

**DSL mapping.** Unchanged — the existing `flat_base` condition
(`bars`/`max_range_pct`/`max_from_52w_high_pct`) as used by
`flat_base_52w`.

---

## 9. Market breadth (regime context)

**Basis:** practitioner, weak/unvalidated as a standalone signal — a
market-internals gauge, not a per-stock predictor.

**What it is.** The percentage of the universe trading above its own
200-day moving average, and the percentage making a new 20-trading-day
high on a given date — standard "market internals" constructions long
used by technical practitioners to gauge whether an index's advance is
broad (many stocks participating) or narrow (a handful of large names
carrying the index while the median stock lags). The specific idea that
a sharp, broad expansion in the number of stocks above their long-term
average moving average ("breadth thrust") can mark the start of a durable
rally is closely associated with Martin Zweig's technical-analysis work;
"percentage of stocks above the 200-day moving average" is itself a
widely published market-internals series (tracked by most major index
providers and charting platforms) rather than a proprietary construction.

**Why this is "regime context," not a screening edge.** This project
found no controlled academic study establishing that this screener's
specific two-field construction (`pct_above_200dma`, `pct_at_20d_high`)
predicts forward index or cross-sectional stock returns with a
quantified effect size — unlike family #1's momentum or family #2's
52-week-high anchoring, both of which cite replicated academic results.
Breadth here is offered as a **regime qualifier that conditions other
conditions** ("uptrend AND positive breadth" reads as "this stock's own
setup, in a market environment where the average stock agrees"), not as
a standalone filter with its own claimed edge — weak alone, by design,
and the `breadth` condition's own plain-English description says so.

**No India-specific evidence located.** This review did not locate a
published India-market breadth study; the construction is applied here
on the (unverified) assumption that a universe-relative internals gauge
generalises across markets, same caution already applied to family #6's
low-volatility anomaly.

**DSL mapping.** `{"type":"breadth","direction":"positive"|"negative"}`
— `screener.cross_section.compute_breadth()` (as-of screens) /
`screener.backtest.compute_breadth_series()` (backtests), fixed 50%
threshold on `pct_above_200dma` (not a tunable parameter — the whole
family is scoped to this one canonical definition, matching the
project's existing `trend` condition's fixed-canonical-definition
precedent rather than adding a configurable threshold this review can't
justify with evidence either way).

---

## 10. Practitioner discretionary trading rules (Link 2003)

**Basis:** practitioner — the same evidentiary standing as family #7
(Minervini): a specific, replicable, fully price/volume-based rule set
with no dedicated academic study behind it, honestly labeled rather than
dressed up.

**What it is.** Marcel Link, *High Probability Trading* (McGraw-Hill,
2003) — a full-book review (2026-07-13). The book's core thesis is entry
*timing* within an already-favorable trend/setup, built from three
recurring constructions used across several of its chapters: (a) the slow
stochastic oscillator (14-3-3 convention) and a rising/falling ADX as
trend-strength gauges (Ch. 6); (b) "buy the oversold reset on the turn,
never while it's still falling" — an oscillator crossing a threshold
level, not merely being past it (Ch. 7); (c) an oscillator pinned at an
extreme for an extended stretch read as trend confirmation, the opposite
of a naive "overbought means sell" reading (Ch. 7); (d) breakouts filtered
to fire only in the direction of the higher-timeframe trend (Ch. 8); and
(e) price/oscillator divergence at confirmed swing pivots as a reversal
signal (Ch. 9). Chapter 10's own composite setup —
`link_high_probability_pullback` — stacks (a) and Item 16's existing
support-at-MA construction together, using Link's own ADX-30 "strong
trend" convention rather than this codebase's canonical ADX-25 bar used
elsewhere (a deliberate, documented divergence from the existing default,
not an oversight).

**Why include it despite the weak academic basis.** Same reasoning as
family #7: it is a widely-read practitioner text with precise, fully
mechanical rules (no discretion required to check any of them), and
several of its ideas — oscillator persistence-as-confirmation, entry
timed to the turn rather than the level, trend-filtered breakouts — are
genuinely different constructions from anything already in this
codebase's condition set, not restatements of the existing trend/RSI/
breakout conditions under a new name.

**India evidence.** None claimed; not applicable — this is not an
academic finding to begin with, same posture as family #7.

**Caveat.** No peer-reviewed out-of-sample test of any of these
constructions, individually or combined, is known to the author of this
document. Every preset built from this review earns nothing until it
survives this codebase's own backtest (`screen backtest`) and cohort
(`cohort create`/`scorecard`) gauntlet — the same bar every other
practitioner-basis preset in this document is held to. Divergence in
particular carries the weakest evidence basis of the five: pivot-based
divergence detection is itself a discretionary, much-disputed technique
even within technical-analysis practice, on top of the general
practitioner caveats the rest of this family already carries.

**Engine additions.** `stoch_k`/`stoch_d` (slow stochastic, fixed 14-3-3
— not a tunable, same fixed-canonical-definition precedent as family #9's
breadth threshold) and `adx_slope` (5-bar diff, the same slope convention
already used for every EMA/SMA field) — see `screener/indicators.py`.
Three new condition types: `threshold_cross` (a field crossing a constant
level within a lookback window — construction (b) above),
`persistence` (every bar in a window satisfies a comparison —
construction (c)), and `divergence` (two confirmed fractal pivots via the
existing `sr.find_pivots`, strict price/oscillator inequalities on both
legs — construction (e)). `divergence` is in the backtester's expensive
set (stride-grid approximated, like `near_support`) since it re-runs a
pivot search over its own lookback on every bar; `threshold_cross` and
`persistence` vectorize exactly like every other cheap condition.

**Explicitly not implemented, with reasons.**
- *Diagonal trendlines / channels* — this codebase's support/resistance
  machinery (TECHNICAL_DESIGN.md §8) is deliberately horizontal-only
  (fractal pivot clustering); a diagonal trendline has no single
  "level," and an approximated one would be a worse, silently-wrong
  version of a real technique rather than an honest gap.
- *Fibonacci retracement levels* — no evidence basis located for this
  review, and the levels are a fixed ratio convention with no
  price/volume construction to validate against; skipped entirely
  rather than implemented as decoration.
- *Congestion-measured price targets* — target estimation is a different
  problem from screening (this codebase's `allocate.py` handles position
  *sizing*, not price *targets*); belongs to a future trade-plan layer,
  if one is ever built, not the screener.
- *Multi-timeframe confirmation* — already shipped (the `timeframe:
  "weekly"` mechanism, §12b's UI depth work); Link's Ch. 5 treatment of
  this idea is validation of an existing capability, not new work.

**DSL mapping.** `link_high_probability_pullback` (weekly `trend` up +
daily `support_at_ma` ema_50 + `range` rsi 35-55 + `range` adx min 30),
`link_oscillator_timed_entry` (`trend` up + `threshold_cross` rsi above
40 + `range` adx min 30), `link_trend_breakout` (`tight_range` +
`breakout_resistance` + `volume_spike` + weekly `trend` up),
`link_persistent_strength` (`persistence` rsi >= 60 for 15 bars +
`trend` up), `link_bullish_divergence` (`divergence` bullish rsi +
`near_support`).

---

## Annotation policy for the 11 remaining existing presets

Not every existing preset maps to one of the eight families above by
design — several are direct condition-level constructions (candlestick
patterns, cross-sectional sector rank, multi-timeframe combinations) with
no dedicated academic literature reviewed here. These are annotated
`"basis": "practitioner"` or, where the construction is a mechanical
combination of already-evidenced pieces (e.g. sector momentum + an MA
pullback), `"basis": "mixed"` with a `"finding"` that states plainly: *"no
dedicated academic study of this exact combination; each component
condition is a standard, widely used technical construction"* rather than
inventing a citation that doesn't exist. See `screener/presets.py` for the
per-preset `evidence` objects — this document doesn't duplicate them, it's
the source they cite back to.

## Sources cited

- Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling
  Losers: Implications for Stock Market Efficiency." *Journal of Finance*
  48(1).
- Jegadeesh, N. (1990). "Evidence of Predictable Behavior of Security
  Returns." *Journal of Finance* 45(3).
- Sehgal, S. & Balakrishnan, I. (2002). "Contrarian and Momentum Strategies
  in the Indian Capital Market." *Vikalpa* 27(1).
- Daniel, K. & Moskowitz, T. (2016). "Momentum Crashes." *Journal of
  Financial Economics* 122(2).
- George, T. & Hwang, C-Y. (2004). "The 52-Week High and Momentum
  Investing." *Journal of Finance* 59(5).
- Moskowitz, T., Ooi, Y-H. & Pedersen, L. (2012). "Time Series Momentum."
  *Journal of Financial Economics* 104(2).
- Faber, M. (2007). "A Quantitative Approach to Tactical Asset Allocation."
  *Journal of Wealth Management* 9(4).
- Brock, W., Lakonishok, J. & LeBaron, B. (1992). "Simple Technical Trading
  Rules and the Stochastic Properties of Stock Returns." *Journal of
  Finance* 47(5).
- Han, Y., Yang, K. & Zhou, G. (2013). "A New Anomaly: The Cross-Sectional
  Profitability of Technical Analysis." *Journal of Financial and
  Quantitative Analysis* 48(4).
- Sullivan, R., Timmermann, A. & White, H. (1999). "Data-Snooping,
  Technical Trading Rule Performance, and the Bootstrap." *Journal of
  Finance* 54(5).
- Lee, C. & Swaminathan, B. (2000). "Price Momentum and Trading Volume."
  *Journal of Finance* 55(5).
- Blitz, D. & van Vliet, P. (2007). "The Volatility Effect: Lower Risk
  Without Lower Return." *Journal of Portfolio Management* 34(1).
- Ang, A., Hodrick, R., Xing, Y. & Zhang, X. (2006). "The Cross-Section of
  Volatility and Expected Returns." *Journal of Finance* 61(1).
- DeMiguel, V., Garlappi, L. & Uppal, R. (2009). "Optimal Versus Naive
  Diversification: How Inefficient is the 1/N Portfolio Strategy?" *Review
  of Financial Studies* 22(5). — cited here for cross-reference; used
  directly in ROADMAP Item 10 (portfolio allocation engine), not a preset.
- Link, M. (2003). *High Probability Trading*. McGraw-Hill. — practitioner
  source for family #10; not peer-reviewed, same evidentiary standing as
  the Minervini/O'Neil template in family #7.

## Non-goals stated plainly

This review does not cover: fundamentals-based factors (value, quality,
earnings momentum — out of scope, no fundamentals data per TECHNICAL_
DESIGN.md §1), options/derivatives-based signals (out of scope, no
derivatives data), or intraday microstructure effects (out of scope, no
intraday data). It also does not claim these eight families are the only
price/volume anomalies with academic support — they are the ones chosen
for implementation, vetted before building rather than backfilling
citations onto screens built first.
