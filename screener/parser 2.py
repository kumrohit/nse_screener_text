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
