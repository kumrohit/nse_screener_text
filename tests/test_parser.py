"""Tests for parser resilience (ROADMAP Item 6) — no live API calls.

The Anthropic client is mocked at `anthropic.Anthropic` so these run
without ANTHROPIC_API_KEY or network access; live parser behaviour is
covered separately by tests/golden_harness.py.
"""
from __future__ import annotations

import types

import anthropic
import pytest

from screener import dsl, parser


class _FakeResp:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(text=text)]


class _FakeMessages:
    def __init__(self, responses):
        self._responses = iter(responses)

    def create(self, **kwargs):
        return _FakeResp(next(self._responses))


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _mock_client(monkeypatch, responses):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda api_key: _FakeClient(responses))


class TestRetryOnMalformedJSON:
    def test_recovers_on_retry(self, monkeypatch):
        _mock_client(monkeypatch, [
            "that is not json",
            '{"logic":"AND","conditions":'
            '[{"type":"trend","direction":"up"}]}',
        ])
        spec, assumptions = parser.parse_with_assumptions("uptrend stocks")
        assert spec["conditions"][0]["type"] == "trend"
        assert assumptions == []

    def test_gives_up_after_one_retry_and_logs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parser, "PARSE_FAILURES_FILE",
                            tmp_path / "parse_failures.jsonl")
        _mock_client(monkeypatch, ["still not json", "nope, also not json"])
        with pytest.raises(dsl.DSLValidationError):
            parser.parse_with_assumptions("a weird query")
        log = (tmp_path / "parse_failures.jsonl").read_text()
        assert "a weird query" in log
        assert "invalid JSON after retry" in log

    def test_first_try_success_makes_no_retry_call(self, monkeypatch):
        # a single-element iterator: a second .create() call would raise
        # StopIteration, failing the test if a retry is attempted
        _mock_client(monkeypatch, [
            '{"logic":"AND","conditions":'
            '[{"type":"trend","direction":"up"}]}',
        ])
        spec, _ = parser.parse_with_assumptions("uptrend stocks")
        assert spec["conditions"][0]["type"] == "trend"


class TestAssumptions:
    def test_extracted_and_stripped_from_spec(self, monkeypatch):
        _mock_client(monkeypatch, [
            '{"logic":"AND","conditions":'
            '[{"type":"volume_spike","min_ratio":1.5}],'
            '"assumptions":["no multiplier stated — used default 1.5x"]}',
        ])
        spec, assumptions = parser.parse_with_assumptions("volume spike")
        assert "assumptions" not in spec
        assert len(assumptions) == 1

    def test_absent_assumptions_key_yields_empty_list(self, monkeypatch):
        _mock_client(monkeypatch, [
            '{"logic":"AND","conditions":'
            '[{"type":"range","field":"rsi","max":30}]}',
        ])
        _, assumptions = parser.parse_with_assumptions("oversold stocks")
        assert assumptions == []


class TestDSLValidationFailureLogging:
    def test_logged_on_validation_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parser, "PARSE_FAILURES_FILE",
                            tmp_path / "parse_failures.jsonl")
        _mock_client(monkeypatch, [
            '{"logic":"AND","conditions":'
            '[{"type":"compare","left":"pe_ratio","op":">","right":5}]}',
        ])
        with pytest.raises(dsl.DSLValidationError):
            parser.parse_with_assumptions("pe ratio below 5")
        log = (tmp_path / "parse_failures.jsonl").read_text()
        assert "pe ratio below 5" in log
        assert "DSL validation failed" in log

    def test_scope_refusal_not_logged_as_failure(self, tmp_path, monkeypatch):
        # {"error": ...} is the parser working as intended, not a
        # vocabulary gap — must not pollute the improvement backlog
        monkeypatch.setattr(parser, "PARSE_FAILURES_FILE",
                            tmp_path / "parse_failures.jsonl")
        _mock_client(monkeypatch,
                    ['{"error": "PE ratio is a fundamental, out of scope"}'])
        with pytest.raises(dsl.DSLValidationError):
            parser.parse_with_assumptions("pe ratio below 5")
        assert not (tmp_path / "parse_failures.jsonl").exists()


class TestBackwardCompatibleParse:
    def test_parse_returns_just_the_spec(self, monkeypatch):
        _mock_client(monkeypatch, [
            '{"logic":"AND","conditions":'
            '[{"type":"trend","direction":"up"}]}',
        ])
        spec = parser.parse("uptrend stocks")
        assert isinstance(spec, dict) and spec["conditions"]
