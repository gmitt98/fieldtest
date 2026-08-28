"""
tests/test_calibration.py

Tests for the judge panel and its agreement statistics (spec 08).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
    labels = {("uc1", "ev1"): {("f1", 1): "pass", ("f1", 2): "fail", ("f1", 3): "pass"}}

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
    labels = {("uc1", "ev1"): {("f1", 1): 3, ("f1", 2): 3}}

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

    # Panel members run concurrently, so the order side effects land in is not
    # guaranteed — only that each configured judge scored exactly once.
    assert sorted(seen_judges) == sorted([
        ("anthropic", "haiku"), ("anthropic", "sonnet"), ("openai", "gpt-5")
    ])
    # Reported order IS guaranteed: pool.map preserves the panel's order, so the
    # report always lists judges as configured.
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


# ---------------------------------------------------------------------------
# Review findings — regression tests
# ---------------------------------------------------------------------------

def test_panel_calls_count_judge_calls_only():
    """
    len(rows) counted regex, rule and reference rows as judge calls, so the
    panel table contradicted the projection the same command had just printed.
    """
    from fieldtest.calibrate import run_calibration

    rows = _rows([True, False]) + [
        ResultRow(use_case="uc1", eval_id="has_greeting", tag="good", type="rule",
                  fixture_id="f1", run=1, passed=True),
        ResultRow(use_case="uc1", eval_id="no_policy", tag="safe", type="regex",
                  fixture_id="f1", run=1, passed=True, error="rule blew up"),
    ]

    with patch("fieldtest.runner.score", return_value=("r", rows)):
        _, data = run_calibration(_config(), Path("evals/config.yaml"))

    for member in data["panel"]:
        assert member["calls"] == 2      # the two llm rows, not all four
        assert member["errors"] == 0     # the regex error is not a judge error


def test_run_calibration_actually_analyses_the_rows():
    """
    A guard on the plumbing itself: the suite once passed with judge_rows never
    populated, because nothing asserted the analysis reached the output.
    """
    from fieldtest.calibrate import run_calibration

    with patch("fieldtest.runner.score", return_value=("r", _rows([True, False, True]))):
        _, data = run_calibration(_config(), Path("evals/config.yaml"))

    assert data["evals"], "panel rows never reached analyze()"
    assert data["evals"]["ev1"]["judges_participating"] == 3


def test_judge_that_produced_nothing_is_named():
    """A judge that errored on every call used to vanish without a word."""
    config = _config()
    working = _rows([True, False, True])
    dead = [
        ResultRow(use_case="uc1", eval_id="ev1", tag="safe", type="llm",
                  fixture_id="f1", run=i, error="openai package not installed")
        for i in (1, 2, 3)
    ]

    stats = analyze(
        config,
        [("anthropic/haiku", working), ("anthropic/sonnet", working), ("openai/gpt-5", dead)],
        labels={},
    )["evals"]["ev1"]

    assert stats["judges_participating"] == 2
    assert stats["judges_configured"] == 3
    assert stats["judges_absent"] == ["openai/gpt-5"]


def test_eval_without_a_disagreement_score_still_renders():
    """
    Only one judge could rule, so there is no agreement figure — and the eval
    the panel failed to evaluate is precisely the one that must not disappear.
    """
    from fieldtest.calibrate import format_calibration

    report = format_calibration({
        "run_id": "r", "set": "full", "panel": [], "has_labels": False,
        "ranked_by_disagreement": ["ranked_ev"],
        "kappa_threshold": 0.6,
        "evals": {
            "ranked_ev": {"type": "binary", "pairwise": [], "mean_agreement": 1.0,
                          "fleiss_kappa": 1.0, "disagreement": 0.0,
                          "judges_participating": 2, "judges_configured": 2,
                          "judges_absent": []},
            "unranked_ev": {"type": "binary", "pairwise": [], "mean_agreement": None,
                            "fleiss_kappa": None, "disagreement": None,
                            "judges_participating": 1, "judges_configured": 2,
                            "judges_absent": ["openai/gpt-5"]},
        },
    })

    assert "### unranked_ev" in report
    assert "no two judges ruled on a shared output" in report
    assert "produced no verdict here" in report
    # And no empty pairwise table under it.
    assert report.count("| judge pair | raw agreement | Cohen's kappa |") == 1


def test_same_eval_id_in_two_use_cases_stays_separate():
    """Config enforces unique fixture ids, not unique eval ids."""
    shared = Eval(id="tone", tag="good", type="llm", description="d",
                  pass_criteria="p", fail_criteria="f")
    config = Config(
        schema_version=2,
        system=SystemConfig(name="t", domain="t"),
        use_cases=[
            UseCase(id="uc_support", description="d", evals=[shared.model_copy()],
                    fixtures=FixturesConfig(directory="f/", sets={"full": ["a"]})),
            UseCase(id="uc_sales", description="d", evals=[shared.model_copy()],
                    fixtures=FixturesConfig(directory="f/", sets={"full": ["b"]})),
        ],
        defaults=Defaults(),
        calibration=CalibrationConfig(panel=PANEL),
    )

    def rows_for(uc, fixture, verdicts):
        return [
            ResultRow(use_case=uc, eval_id="tone", tag="good", type="llm",
                      fixture_id=fixture, run=i + 1, passed=v)
            for i, v in enumerate(verdicts)
        ]

    # Judges agree in support, split in sales.
    judge_rows = [
        ("j1", rows_for("uc_support", "a", [True, True]) + rows_for("uc_sales", "b", [True, True])),
        ("j2", rows_for("uc_support", "a", [True, True]) + rows_for("uc_sales", "b", [False, False])),
        ("j3", rows_for("uc_support", "a", [True, True]) + rows_for("uc_sales", "b", [True, False])),
    ]

    evals = analyze(config, judge_rows, labels={})["evals"]

    assert set(evals) == {"uc_support/tone", "uc_sales/tone"}
    assert evals["uc_support/tone"]["disagreement"] == 0.0
    assert evals["uc_sales/tone"]["disagreement"] > 0.0


def test_panel_judges_share_the_concurrency_budget():
    """Overlapping judges must not quietly multiply the configured load."""
    from fieldtest.calibrate import run_calibration

    seen = []

    def fake_score(**kwargs):
        seen.append(kwargs["concurrency"])
        return "r", _rows([True])

    with patch("fieldtest.runner.score", side_effect=fake_score):
        run_calibration(_config(), Path("evals/config.yaml"), concurrency=6)

    assert seen == [2, 2, 2]      # 6 split across a 3-judge panel


def test_calibration_write_is_all_or_nothing(tmp_path):
    """A panel run is already paid for; a formatting error must not half-write it."""
    from fieldtest.calibrate import write_calibration

    with patch(
        "fieldtest.calibrate.format_calibration", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError):
            write_calibration({"run_id": "r1", "set": "full"}, tmp_path, "r1")

    assert list(tmp_path.glob("*")) == []


def test_projection_multiplier_is_the_panel_size(tmp_path):
    """per_judge already carries judge_runs; folding it in again overstated cost."""
    config = _config(judge_runs=3)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "f1.yaml").write_text("id: f1\ninputs:\n  q: x\n")

    projection = project_calls(config, tmp_path, "full")
    assert projection["multiplier"] == 3          # three judges, not nine
    assert projection["per_judge"] == 15          # 1 fixture × 5 runs × 3 judge_runs


def test_write_artifacts_false_writes_nothing_through_the_real_score_path(tmp_path):
    """
    The suppression tests above patch runner.score, so they verify the mock. This
    one calls the real thing: delete the early return in runner.score() and this
    fails, which is the guarantee 'a panel member must not reach find_baseline()'
    actually rests on.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import find_baseline
    from fieldtest.runner import score

    evals_dir = tmp_path / "evals"
    (evals_dir / "fixtures").mkdir(parents=True)
    (evals_dir / "outputs" / "fix1").mkdir(parents=True)
    (evals_dir / "results").mkdir()

    (evals_dir / "config.yaml").write_text(
        "schema_version: 2\n"
        "system:\n  name: t\n  domain: t\n"
        "defaults:\n  runs: 1\n"
        "use_cases:\n"
        "  - id: uc1\n"
        "    description: d\n"
        "    evals:\n"
        "      - id: has_hello\n"
        "        tag: right\n"
        "        type: regex\n"
        "        description: says hello\n"
        '        pattern: "hello"\n'
        "        match: true\n"
        "    fixtures:\n"
        "      directory: fixtures/\n"
        "      sets:\n"
        "        full: [fix1]\n"
    )
    (evals_dir / "fixtures" / "fix1.yaml").write_text("id: fix1\ninputs:\n  q: x\n")
    (evals_dir / "outputs" / "fix1" / "run-1.txt").write_text("hello there")

    config = parse_and_validate(evals_dir / "config.yaml")
    results_dir = evals_dir / "results"

    _, rows = score(
        config=config, config_path=evals_dir / "config.yaml",
        set_name="full", write_artifacts=False,
    )

    assert rows and rows[0].passed is True
    assert list(results_dir.iterdir()) == []
    assert find_baseline(results_dir, "current", "full") is None

    # And the same run with artifacts on does write, so the test is not vacuous.
    score(
        config=config, config_path=evals_dir / "config.yaml",
        set_name="full", write_artifacts=True,
    )
    assert list(results_dir.glob("*-data.json"))
