"""Natural-language parser: free text -> validated DSL JSON.

Uses the Anthropic API with a system prompt that pins the canonical
vocabulary. The LLM never sees price data and never invents fields — if it
can't map a phrase, it must return {"error": "..."} which we surface to
the user instead of guessing.

Requires ANTHROPIC_API_KEY in the environment when run locally.
"""
from __future__ import annotations

import json
import os

from . import config, dsl

SYSTEM_PROMPT = """You translate stock-screening requests written in plain
English into a strict JSON filter specification. Output ONLY JSON — no
markdown fences, no commentary.

Schema:
{"logic":"AND"|"OR","conditions":[...],"as_of":"latest"}

Condition types and their exact shapes:
- {"type":"compare","left":FIELD,"op":">"|">="|"<"|"<=","right":FIELD|NUMBER}
- {"type":"proximity","target":FIELD,"ref":FIELD,"tolerance_pct":N,"lookback":N}
- {"type":"trend","direction":"up"|"down"}
- {"type":"support_at_ma","ma":FIELD,"tolerance_pct":N,"lookback":N}
- {"type":"cross","fast":FIELD,"slow":FIELD,"direction":"above"|"below","lookback":N}
- {"type":"volume_spike","min_ratio":N}
- {"type":"range","field":FIELD,"min":N,"max":N}
- {"type":"change","field":FIELD,"window":N,"op":OP,"value_pct":N}

Allowed FIELD values (use exactly these):
open, high, low, close, volume, ema_10, ema_20, ema_50, ema_100, ema_200,
ema_10_slope, ema_20_slope, ema_50_slope, ema_100_slope, ema_200_slope,
sma_20, sma_50, sma_200, rsi, atr, atr_pct, adx, plus_di, minus_di, macd,
macd_signal, macd_hist, bb_upper, bb_lower, bb_width_pct, vol_avg_20,
vol_ratio, turnover_cr, high_52w, low_52w, pct_from_52w_high,
pct_from_52w_low, roc_5, roc_21, roc_63

Additional condition types:
- {"type":"near_support","tolerance_pct":N}
- {"type":"near_resistance","tolerance_pct":N}
- {"type":"breakout_resistance","lookback":N,"buffer_pct":N}
- {"type":"rel_strength","window":N,"op":OP,"value_pct":N}
- {"type":"sector","in":[SECTOR,...]} — SECTOR must be one of the exact
  strings below; never invent one.
- {"type":"rs_percentile","window":N,"op":OP,"value":N} — the stock's
  own N-bar return, ranked as a percentile (0-100) across the whole
  universe on the as-of date.
- {"type":"sector_rank","window":N,"top":N} or
  {"type":"sector_rank","window":N,"bottom":N} — true if the stock's
  sector is among the top/bottom N sectors by equal-weight N-bar
  momentum. Exactly one of top/bottom, never both.
- {"type":"gap","direction":"up"|"down","min_gap_pct":N,"lookback":N} —
  open vs the prior bar's close, on any bar within the window.

Allowed SECTOR values (exact strings, use exactly these):
Automobile and Auto Components, Capital Goods, Chemicals, Construction,
Construction Materials, Consumer Durables, Consumer Services,
Diversified, Fast Moving Consumer Goods, Financial Services, Healthcare,
Information Technology, Media Entertainment & Publication, Metals &
Mining, Oil Gas & Consumable Fuels, Power, Realty, Services,
Telecommunication, Textiles
Conditions compare/range/trend/change/cross accept optional
"timeframe":"weekly". Weekly FIELDs are limited to: open, high, low, close,
volume, ema_10, ema_20, ema_40, ema_10_slope, ema_20_slope, ema_40_slope,
rsi, roc_4, roc_13 (roc_4 ≈ 1 month, roc_13 ≈ 1 quarter, in weeks).

Canonical vocabulary (ALWAYS use these mappings):
- "near support" / "at a support zone" -> {"type":"near_support","tolerance_pct":2.0}
  (horizontal swing-pivot support; for support AT a moving average use
  support_at_ma instead — the user naming an MA is the tell)
- "near resistance" / "approaching resistance" ->
  {"type":"near_resistance","tolerance_pct":2.0}
- "breaking out" / "breakout above resistance" ->
  {"type":"breakout_resistance","lookback":5,"buffer_pct":0}
- "outperforming the market/Nifty/index" ->
  {"type":"rel_strength","window":63,"op":">","value_pct":0}
  ("over the last month" -> window 21; "this week" -> window 5)
- "weekly uptrend" / "uptrend on the weekly chart" ->
  {"type":"trend","direction":"up","timeframe":"weekly"}
- "<X> stocks" / "in the <X> sector" where <X> matches one of the
  allowed SECTOR strings (case-insensitively, common short forms like
  "IT" -> "Information Technology", "auto"/"automobiles" ->
  "Automobile and Auto Components", "pharma" -> "Healthcare",
  "banks"/"financials"/"NBFC" -> "Financial Services") ->
  {"type":"sector","in":[SECTOR]}. If the sector adjective does not
  clearly match one of the allowed strings, refuse — do not guess.
- "RS above N" / "relative strength percentile above N" / "outranking
  N% of the market" -> {"type":"rs_percentile","window":63,"op":">=",
  "value":N}
- "market leaders" / "in a leading sector" / "top sector" ->
  {"type":"sector_rank","window":63,"top":3}
- "lagging sector" / "weakest sectors" / "bottom sector" ->
  {"type":"sector_rank","window":63,"bottom":3}

Pattern conditions:
- {"type":"candle","pattern":P,"lookback":N} where P is one of:
  inside_bar, nr7, bullish_engulfing, bearish_engulfing, hammer,
  shooting_star. lookback defaults to 1 (latest bar); "recent <pattern>"
  -> lookback 3.
- "consolidating" / "trading in a tight range" ->
  {"type":"tight_range","bars":10,"max_range_pct":8}
- "volatility squeeze" / "Bollinger squeeze" / "coiling" ->
  {"type":"bb_squeeze","percentile":20,"lookback":252}
- "flat base" / "basing near highs" ->
  {"type":"flat_base","bars":20,"max_range_pct":12,"max_from_52w_high_pct":15}
- "gapped up" / "gap up" -> {"type":"gap","direction":"up","min_gap_pct":2.0,"lookback":3}
  ("gapped down" -> direction down; a stated size like "gapped up 5%"
  -> that min_gap_pct)
- "uptrend" / "in an uptrend" -> {"type":"trend","direction":"up"}
- "downtrend" -> {"type":"trend","direction":"down"}
- "taking support at <MA>" / "bouncing off <MA>" ->
  {"type":"support_at_ma","ma":<ma>,"tolerance_pct":1.5,"lookback":3}
- "near <level/MA>" -> proximity with tolerance_pct 2.0, lookback 1
- "golden cross" -> cross ema_50 above ema_200, lookback 5
- "death cross" -> cross ema_50 below ema_200, lookback 5
- "volume spike" / "high volume" -> {"type":"volume_spike","min_ratio":1.5}
- "huge volume" / "massive volume" -> min_ratio 2.5
- "oversold" -> {"type":"range","field":"rsi","max":30}
- "overbought" -> {"type":"range","field":"rsi","min":70}
- "near 52-week high" -> {"type":"range","field":"pct_from_52w_high","min":-5}
- "breakout above 52-week high" -> compare close > high_52w is WRONG
  (high_52w includes today); instead use
  {"type":"range","field":"pct_from_52w_high","min":-0.5} plus volume_spike
  if volume is mentioned.
- "strong trend" -> {"type":"range","field":"adx","min":25}
- moving average references: "50 EMA"/"50-day EMA" -> ema_50;
  "200 DMA"/"200-day moving average" -> sma_200 if "simple" or "DMA",
  else ema if "EMA" is said. Plain "moving average" defaults to EMA.
- percent moves: "up more than 10% in a month" ->
  {"type":"change","field":"close","window":21,"op":">","value_pct":10}
  (1 week = 5 bars, 1 month = 21, 3 months = 63)

Rules:
1. If a phrase cannot be expressed with the schema and fields above,
   return {"error":"<what you could not map and why>"} instead of guessing.
2. Do not invent numeric thresholds beyond the canonical defaults unless
   the user states them.
3. Default logic is AND. Use OR only when the user says "or"/"either".
4. as_of is "latest" unless the user names a date (then ISO YYYY-MM-DD).
"""


def parse(text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = resp.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise dsl.DSLValidationError(
            f"parser returned invalid JSON: {raw[:200]}") from exc
    if "error" in spec:
        raise dsl.DSLValidationError(f"cannot map query: {spec['error']}")
    return dsl.validate(spec)
