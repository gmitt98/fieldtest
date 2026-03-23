"""
tests/test_judges.py

Tests for judge dispatch and individual judge types.
Test names match spec §8 exactly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fieldtest.config import Config, Defaults, Eval, FixturesConfig, ResultRow, SystemConfig, UseCase
from fieldtest.errors import ConfigError
from fieldtest.judges.dispatch import dispatch_judge
from fieldtest.judges.llm import build_binary_judge_prompt, build_scored_judge_prompt
from fieldtest.judges.registry import _rule_registry, get_rule, rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config() -> Config:
    return Config(
        schema_version=1,
        system=SystemConfig(name="test", domain="test"),
        use_cases=[],
        defaults=Defaults(),
    )


def _make_eval(**kwargs) -> Eval:
    """Build an Eval with minimal required fields, overriding with kwargs."""
    defaults = dict(
        id="ev1",
        tag="right",
        type="regex",
        description="test eval",
        pattern="Go",
        match=True,
    )
    defaults.update(kwargs)
    return Eval(**defaults)


def _make_fixture(fixture_id: str = "fix1", with_expected: bool = False) -> dict:
    f: dict = {"id": fixture_id, "inputs": {"key": "value"}}
    if with_expected:
        f["expected"] = {"contains": ["X"], "not_contains": ["forbidden"]}
    return f


# ---------------------------------------------------------------------------
# Regex judge tests
# ---------------------------------------------------------------------------

def test_regex_match_true_passes():
    ev = _make_eval(pattern="Go", match=True)
    row = dispatch_judge("uc1", ev, "I love Go programming", _make_fixture(), 1, _make_config())
    assert row.passed is True


def test_regex_match_true_fails():
    ev = _make_eval(pattern="Go", match=True)
    row = dispatch_judge("uc1", ev, "I love Python programming", _make_fixture(), 1, _make_config())
    assert row.passed is False


def test_regex_match_false_inverts():
    ev = _make_eval(pattern="Go", match=False)
    row = dispatch_judge("uc1", ev, "I love Go programming", _make_fixture(), 1, _make_config())
    assert row.passed is False


def test_regex_match_false_passes():
    ev = _make_eval(pattern="Go", match=False)
    row = dispatch_judge("uc1", ev, "I love Python programming", _make_fixture(), 1, _make_config())
    assert row.passed is True


# ---------------------------------------------------------------------------
# Reference judge tests
# ---------------------------------------------------------------------------

def test_reference_contains_passes():
    ev = _make_eval(type="reference", pattern=None, match=None)
    fixture = {"id": "fix1", "inputs": {}, "expected": {"contains": ["X"]}}
    row = dispatch_judge("uc1", ev, "output with X inside", fixture, 1, _make_config())
    assert row.passed is True


def test_reference_contains_fails():
    ev = _make_eval(type="reference", pattern=None, match=None)
    fixture = {"id": "fix1", "inputs": {}, "expected": {"contains": ["X"]}}
    row = dispatch_judge("uc1", ev, "output without it", fixture, 1, _make_config())
    assert row.passed is False
    assert "X" in row.detail


def test_reference_not_contains_fails():
    ev = _make_eval(type="reference", pattern=None, match=None)
    fixture = {"id": "fix1", "inputs": {}, "expected": {"not_contains": ["X"]}}
    row = dispatch_judge("uc1", ev, "output with X inside", fixture, 1, _make_config())
    assert row.passed is False


def test_reference_no_expected_skips():
    ev = _make_eval(type="reference", pattern=None, match=None)
    fixture = {"id": "fix1", "inputs": {}}  # no expected block
    row = dispatch_judge("uc1", ev, "any output", fixture, 1, _make_config())
    assert row.skipped is True
    assert row.error is None
    assert row.passed is None


# ---------------------------------------------------------------------------
# Rule judge tests
# ---------------------------------------------------------------------------

def test_rule_registered_passes():
    # Register a passing rule temporarily
    ev = _make_eval(type="rule", id="test_rule_pass", pattern=None, match=None)

    @rule("test_rule_pass")
    def _check_pass(output: str, inputs: dict) -> dict:
        return {"passed": True, "detail": "ok"}

    row = dispatch_judge("uc1", ev, "some output", _make_fixture(), 1, _make_config())
    assert row.passed is True
    # clean up
    _rule_registry.pop("test_rule_pass", None)


def test_rule_registered_fails():
    ev = _make_eval(type="rule", id="test_rule_fail", pattern=None, match=None)

    @rule("test_rule_fail")
    def _check_fail(output: str, inputs: dict) -> dict:
        return {"passed": False, "detail": "bad output"}

    row = dispatch_judge("uc1", ev, "some output", _make_fixture(), 1, _make_config())
    assert row.passed is False
    _rule_registry.pop("test_rule_fail", None)


def test_rule_not_registered_raises():
    ev = _make_eval(type="rule", id="nonexistent_rule", pattern=None, match=None)
    with pytest.raises(ConfigError) as exc:
        dispatch_judge("uc1", ev, "output", _make_fixture(), 1, _make_config())
    assert "nonexistent_rule" in str(exc.value)


# ---------------------------------------------------------------------------
# LLM judge tests
# ---------------------------------------------------------------------------

def _make_llm_binary_eval(**kwargs) -> Eval:
    defaults = dict(
        id="ev_llm",
        tag="right",
        type="llm",
        description="check something",
        binary=True,
        pass_criteria="output is good",
        fail_criteria="output is bad",
        pattern=None,
        match=None,
    )
    defaults.update(kwargs)
    return Eval(**defaults)


def _make_llm_scored_eval(**kwargs) -> Eval:
    defaults = dict(
        id="ev_scored",
        tag="good",
        type="llm",
        description="rate quality",
        binary=False,
        scale=[1, 5],
        anchors={1: "terrible", 5: "excellent"},
        pattern=None,
        match=None,
    )
    defaults.update(kwargs)
    return Eval(**defaults)


def test_llm_api_error_marks_row():
    ev = _make_llm_binary_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"error": "timeout"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.error == "timeout"
    assert row.passed is None


def test_llm_binary_pass():
    ev = _make_llm_binary_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"answer": "Pass", "reasoning": "good"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.passed is True


def test_llm_binary_fail():
    ev = _make_llm_binary_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"answer": "Fail", "reasoning": "bad"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.passed is False


def test_llm_scored_floor_hit():
    ev = _make_llm_scored_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"score": 1, "reasoning": "poor"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.floor_hit is True
    assert row.passed is None
    assert row.score == 1


def test_llm_scored_not_floor():
    ev = _make_llm_scored_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"score": 3, "reasoning": "ok"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.floor_hit is False
    assert row.score == 3


def test_llm_scored_no_passed_field():
    ev = _make_llm_scored_eval()
    config = _make_config()
    with patch("fieldtest.judges.llm.call_judge_llm", return_value={"score": 4, "reasoning": "good"}):
        row = dispatch_judge("uc1", ev, "output", _make_fixture(), 1, config)
    assert row.passed is None  # scored evals have no binary passed


def test_unknown_type_raises():
    # Can't create invalid Eval via Pydantic, so test dispatch directly
    from fieldtest.config import Eval as RealEval
    ev = _make_eval(type="regex", pattern="x", match=True)
    # Monkey-patch type on the eval object to bypass Pydantic
    object.__setattr__(ev, "type", "custom_unknown")
    with pytest.raises(ConfigError) as exc:
        dispatch_judge("uc1", ev, "output", _make_fixture(), 1, _make_config())
    assert "custom_unknown" in str(exc.value)


def test_result_row_always_populated():
    """All core fields populated regardless of judge type."""
    ev = _make_eval(pattern="Go", match=True)
    row = dispatch_judge("uc1", ev, "output", {"id": "my_fixture", "inputs": {}}, 7, _make_config())
    assert row.use_case == "uc1"
    assert row.eval_id == "ev1"
    assert row.tag == "right"
    assert row.type == "regex"
    assert row.fixture_id == "my_fixture"
    assert row.run == 7


# ---------------------------------------------------------------------------
# Prompt template tests
# ---------------------------------------------------------------------------

def test_binary_prompt_format():
    ev = _make_llm_binary_eval()
    prompt = build_binary_judge_prompt(ev, "my output")
    assert "You are evaluating the output of an AI system." in prompt
    assert "Pass if: output is good" in prompt
    assert "Fail if: output is bad" in prompt
    assert "---\nmy output\n---" in prompt
    assert 'Respond with this JSON and nothing else:' in prompt


def test_scored_prompt_anchors_sorted():
    ev = _make_llm_scored_eval(
        anchors={5: "excellent", 1: "terrible", 3: "mediocre"}
    )
    prompt = build_scored_judge_prompt(ev, "output")
    # Anchors must appear in ascending order
    pos_1 = prompt.index("1 —")
    pos_3 = prompt.index("3 —")
    pos_5 = prompt.index("5 —")
    assert pos_1 < pos_3 < pos_5


def test_binary_prompt_no_examples_no_examples_block():
    ev = _make_llm_binary_eval(examples=[])
    prompt = build_binary_judge_prompt(ev, "output")
    assert "Examples:" not in prompt


def test_binary_prompt_with_examples():
    from fieldtest.config import LLMExample
    ev = _make_llm_binary_eval(examples=[
        LLMExample(output="good output", label="pass", reasoning="looks good")
    ])
    prompt = build_binary_judge_prompt(ev, "output")
    assert "Examples:" in prompt
    assert "Label: Pass" in prompt  # title() applied
    assert "Reasoning: looks good" in prompt
