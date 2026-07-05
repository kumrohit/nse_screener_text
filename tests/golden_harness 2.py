"""Golden-query harness for the NL parser.

Two modes:

1. OFFLINE (runs in CI / no API key): every `expected` spec in the fixture
   file must pass DSL validation and render via describe(). This catches
   fixture rot when the DSL evolves. Run via pytest (test_golden_offline).

2. LIVE (needs ANTHROPIC_API_KEY): sends each query through the real
   parser and compares against expected after normalisation. Reports a
   score and per-query diffs. Run:

       python -m tests.golden_harness

Comparison is semantic, not textual: conditions are canonicalised (missing
optional keys filled with documented defaults, condition list sorted) so
an equivalent spec in different order still passes. Any change to the
parser prompt should keep this at 12/12 before shipping.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "golden_queries.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screener import dsl  # noqa: E402

DEFAULTS = {
    "support_at_ma": {"tolerance_pct": 1.5, "lookback": 3},
    "proximity": {"lookback": 3},
    "cross": {"lookback": 3},
    "breakout_resistance": {"lookback": 5, "buffer_pct": 0},
}


def canon(spec: dict) -> dict:
    out = {"logic": spec.get("logic", "AND"),
           "as_of": spec.get("as_of", "latest")}
    conds = []
    for c in spec["conditions"]:
        c = {**DEFAULTS.get(c["type"], {}), **c}
        c.setdefault("timeframe", "daily")
        conds.append(c)
    out["conditions"] = sorted(
        conds, key=lambda c: json.dumps(c, sort_keys=True))
    return out


def load_fixtures() -> list[dict]:
    return json.loads(FIXTURES.read_text())


def run_live() -> None:
    from screener import parser

    cases = load_fixtures()
    passed = 0
    for case in cases:
        q = case["query"]
        expect_error = case["expected"] == {"error": True}
        try:
            got = parser.parse(q)
            if expect_error:
                print(f"FAIL  {q!r}\n      expected refusal, got: {got}")
                continue
            if canon(got) == canon(case["expected"]):
                passed += 1
                print(f"PASS  {q!r}")
            else:
                print(f"FAIL  {q!r}")
                print(f"      expected: {json.dumps(canon(case['expected']))}")
                print(f"      got:      {json.dumps(canon(got))}")
        except dsl.DSLValidationError as exc:
            if expect_error:
                passed += 1
                print(f"PASS  {q!r} (correctly refused: {exc})")
            else:
                print(f"FAIL  {q!r} raised {exc}")
    print(f"\n{passed}/{len(cases)} golden queries passed")
    if passed < len(cases):
        sys.exit(1)


if __name__ == "__main__":
    run_live()
