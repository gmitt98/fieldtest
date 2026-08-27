"""
tests/test_calibration.py

Tests for the judge panel and its agreement statistics (spec 08).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fieldtest.calibrate import (
    analyze,
    config_for_judge,
    project_calls,
    require_panel,
)
from fieldtest.config import (
    CalibrationConfig,
    Config,
    Defaults,
    Eval,
    FixturesConfig,
    PanelJudge,
    ResultRow,
    SystemConfig,
    UseCase,
)
from fieldtest.errors import ConfigError
from fieldtest.results.calibration import (
    cohens_kappa,
    fleiss_kappa,
    mean_absolute_deviation,
    raw_agreement,
    signed_bias,
    spearman,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PANEL = [
    PanelJudge(provider="anthropic", model="haiku"),
    PanelJudge(provider="anthropic", model="sonnet"),
    PanelJudge(provider="openai",    model="gpt-5"),
]


def _config(evals=None, panel=PANEL, judge_runs=1) -> Config:
    evals = evals or [
        Eval(id="ev1", tag="safe", type="llm", description="d",
             pass_criteria="p", fail_criteria="f")
    ]
    return Config(
        schema_version=2,
        system=SystemConfig(name="t", domain="t"),
        use_cases=[UseCase(
            id="uc1", description="d", evals=evals,
            fixtures=FixturesConfig(
                directory="fixtures/", sets={"full": ["f1"]}, judge_runs=judge_runs
            ),
        )],
        defaults=Defaults(),
        calibration=CalibrationConfig(panel=panel) if panel else None,
    )


def _rows(verdicts: list[bool], eval_id="ev1", tag="safe") -> list[ResultRow]:
    return [
        ResultRow(use_case="uc1", eval_id=eval_id, tag=tag, type="llm",
                  fixture_id="f1", run=i + 1, passed=v)
        for i, v in enumerate(verdicts)
    ]


def _scored_rows(scores: list[int], eval_id="ev1") -> list[ResultRow]:
    return [
        ResultRow(use_case="uc1", eval_id=eval_id, tag="good", type="llm",
                  fixture_id="f1", run=i + 1, score=s)
        for i, s in enumerate(scores)
    ]


# ---------------------------------------------------------------------------
# Config gate
# ---------------------------------------------------------------------------

def test_calibrate_requires_panel_in_config():
    """A missing block names itself and shows the shape, per the error contract."""
    with pytest.raises(ConfigError) as exc:
        require_panel(_config(panel=None))

    message = str(exc.value)
    assert "calibration" in message
    assert "panel:" in message
    assert "at least two judges" in message


def test_panel_requires_two_judges():
    with pytest.raises(Exception, match="at least two"):
        CalibrationConfig(panel=[PanelJudge(provider="anthropic", model="haiku")])


def test_config_for_judge_swaps_the_judge_and_clears_overrides():
    """A per-eval override would pin an eval to one model and defeat the panel."""
    evals = [Eval(id="ev1", tag="safe", type="llm", description="d",
                  pass_criteria="p", fail_criteria="f",
                  provider="openai", model="pinned")]
    original = _config(evals)
    swapped  = config_for_judge(original, PANEL[1])

    assert swapped.defaults.provider == "anthropic"
    assert swapped.defaults.model    == "sonnet"
    assert swapped.use_cases[0].evals[0].model is None
    # The original is untouched.
    assert original.use_cases[0].evals[0].model == "pinned"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def test_pairwise_agreement_computed_per_eval():
    a = {("f1", 1): True, ("f1", 2): False, ("f1", 3): True}
    b = {("f1", 1): True, ("f1", 2): True,  ("f1", 3): True}
    assert raw_agreement(a, b) == round(2 / 3, 6)


def test_cohens_kappa_low_when_judges_agree_by_chance():
    """
    Two judges that always answer pass show near-total raw agreement on an eval
    whose true failure rate is low, and have demonstrated nothing.
    """
    always_pass_a = {("f1", i): True for i in range(1, 21)}
    always_pass_b = {("f1", i): True for i in range(1, 21)}

    assert raw_agreement(always_pass_a, always_pass_b) == 1.0
    assert cohens_kappa(always_pass_a, always_pass_b) == 0.0


def test_cohens_kappa_high_when_judges_genuinely_track_each_other():
    a = {("f1", 1): True, ("f1", 2): False, ("f1", 3): True, ("f1", 4): False}
    b = {("f1", 1): True, ("f1", 2): False, ("f1", 3): True, ("f1", 4): False}
    assert cohens_kappa(a, b) == 1.0


def test_fleiss_kappa_across_full_panel():
    a = {("f1", 1): True,  ("f1", 2): False, ("f1", 3): True,  ("f1", 4): False}
    b = {("f1", 1): True,  ("f1", 2): False, ("f1", 3): True,  ("f1", 4): False}
    c = {("f1", 1): True,  ("f1", 2): False, ("f1", 3): True,  ("f1", 4): False}
    assert fleiss_kappa([a, b, c]) == 1.0

    # Unanimous-but-constant panel: perfect raw agreement, no demonstrated skill.
    flat = [{("f1", i): True for i in range(1, 5)} for _ in range(3)]
    assert fleiss_kappa(flat) == 0.0


def test_fleiss_kappa_needs_two_judges():
    assert fleiss_kappa([{("f1", 1): True}]) is None


def test_scored_panel_reports_mad_and_correlation():
    a = {("f1", 1): 1.0, ("f1", 2): 2.0, ("f1", 3): 3.0}
    b = {("f1", 1): 2.0, ("f1", 2): 3.0, ("f1", 3): 4.0}

    assert mean_absolute_deviation(a, b) == 1.0
    assert spearman(a, b) == 1.0          # perfectly rank-correlated but biased
    assert signed_bias(b, a) == 1.0


def test_spearman_handles_ties():
    a = {("f1", 1): 3.0, ("f1", 2): 3.0, ("f1", 3): 1.0}
    b = {("f1", 1): 5.0, ("f1", 2): 5.0, ("f1", 3): 2.0}
    assert spearman(a, b) == 1.0


def test_spearman_none_when_a_judge_is_constant():
    a = {("f1", 1): 3.0, ("f1", 2): 3.0}
    b = {("f1", 1): 1.0, ("f1", 2): 5.0}
    assert spearman(a, b) is None


def test_statistics_return_none_without_shared_outputs():
    assert raw_agreement({("f1", 1): True}, {("f1", 2): True}) is None
    assert cohens_kappa({("f1", 1): True}, {("f1", 2): True}) is None
    assert mean_absolute_deviation({("f1", 1): 1.0}, {("f1", 2): 1.0}) is None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def test_evals_ranked_by_disagreement():
    """The actionable output: the contested eval is the one to rewrite."""
    evals = [
        Eval(id="sharp", tag="safe", type="llm", description="d",
             pass_criteria="p", fail_criteria="f"),
        Eval(id="vague", tag="good", type="llm", description="d",
             pass_criteria="p", fail_criteria="f"),
    ]
    config = _config(evals)

    # Every judge agrees on `sharp` and splits on `vague`.
    judge_rows = [
        ("j1", _rows([True, False, True], eval_id="sharp")
             + _rows([True, True, True],  eval_id="vague", tag="good")),
        ("j2", _rows([True, False, True], eval_id="sharp")
             + _rows([False, False, True], eval_id="vague", tag="good")),
        ("j3", _rows([True, False, True], eval_id="sharp")
             + _rows([True, False, False], eval_id="vague", tag="good")),
    ]

    result = analyze(config, judge_rows, labels={})

    assert result["ranked_by_disagreement"][0] == "vague"
    assert result["evals"]["sharp"]["disagreement"] == 0.0
    assert result["evals"]["vague"]["disagreement"] > 0


def test_human_agreement_reported_when_labels_present():
    config = _config()
    judge_rows = [
        ("accurate", _rows([True, False, True])),
        ("lenient",  _rows([True, True,  True])),
    ]
    labels = {"ev1": {("f1", 1): "pass", ("f1", 2): "fail", ("f1", 3): "pass"}}

    stats = analyze(config, judge_rows, labels)["evals"]["ev1"]

    assert "human" in stats
    # Ranked by accuracy, best first.
    assert stats["human"][0]["judge"] == "accurate"
    assert stats["human"][0]["agreement"] == 1.0
    lenient = next(h for h in stats["human"] if h["judge"] == "lenient")
    assert lenient["judge_false_pass"] == 1
    assert lenient["judge_false_fail"] == 0


def test_human_agreement_absent_when_no_labels():
    config = _config()
    judge_rows = [("j1", _rows([True, False])), ("j2", _rows([True, True]))]

    stats = analyze(config, judge_rows, labels={})["evals"]["ev1"]
    assert "human" not in stats


def test_scored_analysis_reports_signed_bias_against_human():
    evals = [Eval(id="ev1", tag="good", type="llm", binary=False, description="d",
                  scale=[1, 5], anchors={1: "bad", 5: "great"})]
    config = _config(evals)
    judge_rows = [
        ("high", _scored_rows([5, 5])),
        ("low",  _scored_rows([2, 2])),
    ]
    labels = {"ev1": {("f1", 1): 3, ("f1", 2): 3}}

    stats = analyze(config, judge_rows, labels)["evals"]["ev1"]

    assert stats["type"] == "scored"
    assert stats["mean_mad"] == 3.0
    by_judge = {h["judge"]: h for h in stats["human"]}
    assert by_judge["high"]["signed_bias"] == 2.0     # lenient
    assert by_judge["low"]["signed_bias"] == -1.0     # harsh


def test_kappa_below_threshold_is_flagged():
    config = _config()
    always_pass = _rows([True] * 6)
    judge_rows = [("j1", always_pass), ("j2", always_pass)]

    stats = analyze(config, judge_rows, labels={})["evals"]["ev1"]
    pair = stats["pairwise"][0]

    assert pair["agreement"] == 1.0      # raw agreement would certify them
    assert pair["kappa"] == 0.0
    assert pair["below_threshold"] is True


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------

def test_calibrate_runs_each_panel_judge_over_same_outputs(tmp_path):
    """N scoring runs over one output set, differing only in judge config."""
    from fieldtest.calibrate import run_calibration

    config = _config()
    seen_judges = []

    def fake_score(**kwargs):
        seen_judges.append(
            (kwargs["config"].defaults.provider, kwargs["config"].defaults.model)
        )
        return "run-x", _rows([True, False])

    with patch("fieldtest.runner.score", side_effect=fake_score):
        run_id, data = run_calibration(config, tmp_path / "config.yaml")

    assert seen_judges == [
        ("anthropic", "haiku"), ("anthropic", "sonnet"), ("openai", "gpt-5")
    ]
    assert [m["judge"] for m in data["panel"]] == [
        "anthropic/haiku", "anthropic/sonnet", "openai/gpt-5"
    ]
    assert data["kind"] == "calibration"


def test_calibrate_reuses_score_path(tmp_path):
    """Not a forked scoring path — and it must not write result artifacts."""
    from fieldtest.calibrate import run_calibration

    with patch("fieldtest.runner.score", return_value=("r", _rows([True]))) as mock_score:
        run_calibration(_config(), tmp_path / "config.yaml")

    assert mock_score.call_count == 3
    for call in mock_score.call_args_list:
        assert call.kwargs["write_artifacts"] is False


def test_calibration_panel_records_errors_per_judge(tmp_path):
    from fieldtest.calibrate import run_calibration

    rows = _rows([True, False])
    rows.append(ResultRow(use_case="uc1", eval_id="ev1", tag="safe", type="llm",
                          fixture_id="f1", run=3, error="overloaded"))

    with patch("fieldtest.runner.score", return_value=("r", rows)):
        _, data = run_calibration(_config(), tmp_path / "config.yaml")

    assert all(m["errors"] == 1 for m in data["panel"])


def test_calibration_artifacts_excluded_from_find_baseline(tmp_path):
    """A calibration run is not a measurement of the system."""
    from fieldtest.calibrate import write_calibration
    from fieldtest.results.aggregator import find_baseline

    write_calibration(
        {"run_id": "r1", "set": "full", "panel": [], "evals": {},
         "ranked_by_disagreement": [], "has_labels": False},
        tmp_path, "r1",
    )

    assert (tmp_path / "r1-calibration.json").exists()
    assert (tmp_path / "r1-calibration.md").exists()
    assert find_baseline(tmp_path, "current", "full") is None


def test_project_calls_states_the_multiplier(tmp_path):
    config = _config(judge_runs=3)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "f1.yaml").write_text("id: f1\ninputs:\n  q: x\n")

    projection = project_calls(config, tmp_path, "full")

    # 1 fixture × 5 runs × 3 judge_runs × 1 llm eval = 15 per judge, 3 judges.
    assert projection["per_judge"] == 15
    assert projection["total"] == 45
    assert projection["judges"] == 3


def test_calibration_report_warns_when_no_labels():
    from fieldtest.calibrate import format_calibration

    report = format_calibration({
        "run_id": "r", "set": "full", "panel": [], "evals": {},
        "ranked_by_disagreement": [], "has_labels": False,
    })
    assert "shared bias as readily as shared accuracy" in report
