"""
tests/test_aggregator.py

Tests for aggregator.py — build_summary() and build_delta().
Test names match spec §9 exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fieldtest.config import Config, Defaults, Eval, FixturesConfig, ResultRow, SystemConfig, UseCase
from fieldtest.results.aggregator import build_delta, build_summary, find_baseline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(evals: list[Eval] | None = None, judge_runs: int = 1) -> Config:
    if evals is None:
        evals = [_make_eval_def("ev1", is_scored=False)]
    return Config(
        schema_version=1,
        system=SystemConfig(name="test", domain="test"),
        use_cases=[
            UseCase(
                id="uc1",
                description="test",
                evals=evals,
                fixtures=FixturesConfig(
                    directory="fixtures/", sets={"full": []}, judge_runs=judge_runs
                ),
            )
        ],
        defaults=Defaults(),
    )


def _make_eval_def(eval_id: str, is_scored: bool = False) -> Eval:
    if is_scored:
        return Eval(
            id=eval_id, tag="good", type="llm", binary=False,
            description="rate it",
            scale=[1, 5],
            anchors={1: "bad", 5: "great"},
        )
    return Eval(
        id=eval_id, tag="right", type="regex",
        description="check it",
        pattern="x", match=True,
    )


def _row(passed: bool | None = True, error: str | None = None,
         skipped: bool = False, score: int | None = None,
         eval_id: str = "ev1", tag: str = "right", ev_type: str = "regex",
         floor_hit: bool = False, fixture_id: str = "fix1", run: int = 1,
         judge_run: int = 1) -> ResultRow:
    return ResultRow(
        use_case="uc1", eval_id=eval_id, tag=tag, type=ev_type,
        fixture_id=fixture_id, run=run, judge_run=judge_run,
        passed=passed, error=error, skipped=skipped,
        score=score, floor_hit=floor_hit,
    )


# ---------------------------------------------------------------------------
# build_summary tests
# ---------------------------------------------------------------------------

def test_failure_rate_basic():
    rows = [_row(passed=True)] * 7 + [_row(passed=False)] * 3
    summary = build_summary(rows, _make_config())
    stats = summary["uc1"]["right"]["ev1"]
    assert stats["failure_rate"] == pytest.approx(0.3, abs=1e-6)
    assert stats["total_runs"] == 10


def test_error_rows_excluded():
    rows = [_row(passed=True)] * 5 + [_row(passed=False)] * 3 + [_row(error="boom")] * 2
    summary = build_summary(rows, _make_config())
    stats = summary["uc1"]["right"]["ev1"]
    # total_runs = 8 (excludes 2 error rows)
    assert stats["total_runs"] == 8
    assert stats["failure_rate"] == pytest.approx(3 / 8, abs=1e-6)
    assert stats["error_count"] == 2


def test_skipped_rows_excluded():
    rows = (
        [_row(passed=True)] * 5
        + [_row(passed=False)] * 2
        + [_row(passed=None, skipped=True)] * 3
    )
    summary = build_summary(rows, _make_config())
    stats = summary["uc1"]["right"]["ev1"]
    assert stats["total_runs"] == 7
    assert stats["failure_rate"] == pytest.approx(2 / 7, abs=1e-4)


def test_scored_stats():
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals)
    rows = [
        _row(score=1, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=2, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=3, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=4, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=5, eval_id="ev1", tag="good", ev_type="llm", passed=None),
    ]
    summary = build_summary(rows, config)
    stats = summary["uc1"]["good"]["ev1"]
    assert stats["failure_rate"] is None
    assert stats["mean"] == pytest.approx(3.0)
    assert stats["min"] == 1
    assert stats["max"] == 5


def test_floor_hits():
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals)
    rows = [
        _row(score=1, eval_id="ev1", tag="good", ev_type="llm", passed=None, floor_hit=True),
        _row(score=1, eval_id="ev1", tag="good", ev_type="llm", passed=None, floor_hit=True),
        _row(score=3, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=4, eval_id="ev1", tag="good", ev_type="llm", passed=None),
        _row(score=5, eval_id="ev1", tag="good", ev_type="llm", passed=None),
    ]
    summary = build_summary(rows, config)
    stats = summary["uc1"]["good"]["ev1"]
    assert stats["floor_hits"] == 2


def test_all_errors_zero_total():
    rows = [_row(error="api down")] * 10
    summary = build_summary(rows, _make_config())
    stats = summary["uc1"]["right"]["ev1"]
    assert stats["total_runs"] == 0
    assert stats["failure_rate"] is None
    assert stats["error_count"] == 10


def test_grouped_by_use_case_tag_eval():
    """Summary is keyed use_case → tag → eval_id."""
    rows = [_row(passed=True, tag="right")] * 3
    summary = build_summary(rows, _make_config())
    assert "uc1" in summary
    assert "right" in summary["uc1"]
    assert "ev1" in summary["uc1"]["right"]


# ---------------------------------------------------------------------------
# build_delta tests
# ---------------------------------------------------------------------------

def _write_baseline(tmp_path: Path, run_id: str, failure_rate: float | None = None,
                    mean: float | None = None) -> Path:
    stats: dict = {"total_runs": 10, "error_count": 0, "floor_hits": 0}
    if mean is not None:
        stats.update({"failure_rate": None, "mean": mean, "min": 1, "max": 5})
    else:
        stats["failure_rate"] = failure_rate

    data = {
        "run_id": run_id,
        "summary": {
            "uc1": {
                "right": {
                    "ev1": stats
                }
            }
        }
    }
    p = tmp_path / f"{run_id}-data.json"
    p.write_text(json.dumps(data))
    return p


def _make_current_summary(failure_rate: float | None = None, mean: float | None = None) -> dict:
    stats: dict = {"total_runs": 10, "error_count": 0, "floor_hits": 0}
    if mean is not None:
        stats.update({"failure_rate": None, "mean": mean, "min": 1, "max": 5})
    else:
        stats["failure_rate"] = failure_rate
    return {"uc1": {"right": {"ev1": stats}}}


def test_delta_no_baseline():
    delta = build_delta(_make_current_summary(0.1), None)
    assert delta["baseline_run_id"] is None
    assert delta["increased"] == []
    assert delta["decreased"] == []
    assert delta["unchanged"] == []


def test_delta_increase(tmp_path):
    baseline = _write_baseline(tmp_path, "old-run", failure_rate=0.10)
    current  = _make_current_summary(failure_rate=0.20)
    delta    = build_delta(current, baseline)
    assert len(delta["increased"]) == 1
    assert delta["increased"][0]["eval_id"] == "ev1"
    assert delta["increased"][0]["delta"] == pytest.approx(0.10, abs=1e-6)


def test_delta_decrease(tmp_path):
    baseline = _write_baseline(tmp_path, "old-run", failure_rate=0.20)
    current  = _make_current_summary(failure_rate=0.10)
    delta    = build_delta(current, baseline)
    assert len(delta["decreased"]) == 1
    assert delta["decreased"][0]["delta"] == pytest.approx(-0.10, abs=1e-6)


def test_delta_unchanged_epsilon(tmp_path):
    baseline = _write_baseline(tmp_path, "old-run", failure_rate=0.1000)
    current  = _make_current_summary(failure_rate=0.1005)
    delta    = build_delta(current, baseline)
    assert "ev1" in delta["unchanged"]
    assert delta["increased"] == []


def test_delta_scored_compares_mean(tmp_path):
    baseline = _write_baseline(tmp_path, "old-run", mean=3.0)
    current  = _make_current_summary(mean=3.5)
    delta    = build_delta(current, baseline)
    assert len(delta["increased"]) == 1
    assert delta["increased"][0]["delta"] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# find_baseline tests — set + dataset_version filtering
# ---------------------------------------------------------------------------

def _write_data_json(tmp_path: Path, run_id: str, set_name: str,
                     dataset_version: str | None = None,
                     judge: dict | None = None) -> Path:
    data: dict = {"run_id": run_id, "set": set_name, "summary": {}}
    if dataset_version is not None:
        data["dataset_version"] = dataset_version
    if judge is not None:
        data["judge"] = judge
    p = tmp_path / f"{run_id}-data.json"
    p.write_text(json.dumps(data))
    return p


def test_find_baseline_returns_none_when_dir_missing(tmp_path):
    assert find_baseline(tmp_path / "nope", "current", "smoke") is None


def test_find_baseline_skips_other_sets(tmp_path):
    _write_data_json(tmp_path, "old-1", "full")
    _write_data_json(tmp_path, "old-2", "smoke")
    result = find_baseline(tmp_path, "current", "smoke")
    assert result is not None and result.stem == "old-2-data"


def test_find_baseline_skips_self(tmp_path):
    _write_data_json(tmp_path, "current", "smoke")
    assert find_baseline(tmp_path, "current", "smoke") is None


def test_find_baseline_unversioned_current_matches_any(tmp_path):
    """Backwards compatibility: if current run has no dataset_version, accept any baseline."""
    _write_data_json(tmp_path, "old-1", "smoke", dataset_version="v2")
    _write_data_json(tmp_path, "old-2", "smoke")  # unversioned
    result = find_baseline(tmp_path, "current", "smoke", dataset_version=None)
    # Most recent matching set wins — sort is reverse alpha, "old-2" > "old-1"
    assert result is not None and result.stem == "old-2-data"


def test_find_baseline_versioned_current_filters_to_same_version(tmp_path):
    _write_data_json(tmp_path, "old-1", "smoke", dataset_version="v1")
    _write_data_json(tmp_path, "old-2", "smoke", dataset_version="v2")
    _write_data_json(tmp_path, "old-3", "smoke")  # unversioned — should NOT match versioned current
    result = find_baseline(tmp_path, "current", "smoke", dataset_version="v2")
    assert result is not None and result.stem == "old-2-data"


def test_find_baseline_versioned_current_with_no_match_returns_none(tmp_path):
    _write_data_json(tmp_path, "old-1", "smoke", dataset_version="v1")
    _write_data_json(tmp_path, "old-2", "smoke")  # unversioned
    assert find_baseline(tmp_path, "current", "smoke", dataset_version="v2") is None


# ---------------------------------------------------------------------------
# Judge error surfacing (spec 05)
# ---------------------------------------------------------------------------

def test_summarize_judge_errors_returns_none_when_clean():
    from fieldtest.results.aggregator import summarize_judge_errors

    summary = build_summary([_row(passed=True), _row(passed=False)], _make_config())
    assert summarize_judge_errors(summary) is None


def test_summarize_judge_errors_counts_calls_and_affected_evals():
    from fieldtest.results.aggregator import summarize_judge_errors

    rows = [
        _row(passed=True),
        _row(passed=None, error="overloaded"),
        _row(passed=None, error="overloaded"),
    ]
    result = summarize_judge_errors(build_summary(rows, _make_config()))

    assert result["failed"] == 2
    assert result["total"] == 3
    assert result["affected"] == [("ev1", 1, 3)]


def test_report_header_shows_error_count_when_nonzero():
    """An overloaded provider shrinks the sample; the header must say so."""
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [
        _row(passed=True),
        _row(passed=None, error="overloaded"),
        _row(passed=None, error="overloaded"),
    ]
    report = format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )

    assert "judge errors: 2 of 3 calls failed after retry." in report
    assert "ev1 (1 of 3 runs scored)" in report


def test_report_header_omits_error_count_when_zero():
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [_row(passed=True), _row(passed=False)]
    report = format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )
    assert "judge errors:" not in report


def test_eval_marked_when_total_runs_below_configured():
    """The per-eval row itself must show the shrunken sample, not just the header."""
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [
        _row(passed=True),
        _row(passed=True),
        _row(passed=None, error="overloaded"),
    ]
    report = format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )

    assert "| 1 ⚠ 2/3 scored |" in report


def test_eval_not_marked_when_fully_scored():
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [_row(passed=True), _row(passed=False)]
    report = format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )
    assert "scored |" not in report
    assert "| 0 |" in report


# ---------------------------------------------------------------------------
# Judge provenance (spec 01)
# ---------------------------------------------------------------------------

def test_data_json_includes_judge_block(tmp_path):
    from fieldtest.results.writer import write_results

    config = _make_config()
    rows = [_row(passed=True)]
    write_results(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="run-1", output_dir=tmp_path, set_name="full",
    )
    data = json.loads((tmp_path / "run-1-data.json").read_text())

    assert data["schema_version"] == 2
    judge = data["judge"]
    assert judge["provider"] == "anthropic"
    assert judge["model"] == config.defaults.model
    assert judge["temperature"] == 0.0
    assert judge["overrides"] == {}      # never omitted — consumers index unconditionally
    assert len(judge["fingerprint"]) == 8


def test_judge_block_includes_per_eval_overrides():
    from fieldtest.results.provenance import build_judge_block

    evals = [
        Eval(id="ev1", tag="right", type="llm", description="d",
             pass_criteria="p", fail_criteria="f"),
        Eval(id="ev2", tag="safe", type="llm", description="d",
             pass_criteria="p", fail_criteria="f",
             provider="openai", model="gpt-5"),
    ]
    judge = build_judge_block(_make_config(evals))

    assert judge["overrides"] == {"ev2": {"provider": "openai", "model": "gpt-5"}}
    assert "ev1" not in judge["overrides"]


def test_judge_fingerprint_stable_across_identical_configs():
    from fieldtest.results.provenance import build_judge_block

    assert (
        build_judge_block(_make_config())["fingerprint"]
        == build_judge_block(_make_config())["fingerprint"]
    )


def test_judge_fingerprint_changes_when_override_added():
    from fieldtest.results.provenance import build_judge_block

    plain = [Eval(id="ev1", tag="right", type="llm", description="d",
                  pass_criteria="p", fail_criteria="f")]
    overridden = [Eval(id="ev1", tag="right", type="llm", description="d",
                       pass_criteria="p", fail_criteria="f", model="gpt-5")]

    assert (
        build_judge_block(_make_config(plain))["fingerprint"]
        != build_judge_block(_make_config(overridden))["fingerprint"]
    )


def test_judge_fingerprint_changes_when_generation_config_changes():
    """Spec 02 §2.7: temperature and seed join the fingerprint payload."""
    from fieldtest.results.provenance import build_judge_block

    base = _make_config()
    hot  = _make_config()
    hot.defaults.judge_temperature = 1.0

    assert build_judge_block(base)["fingerprint"] != build_judge_block(hot)["fingerprint"]


def test_find_baseline_skips_different_judge_fingerprint(tmp_path):
    """Rescoring with a different judge must not read as a system regression."""
    _write_data_json(tmp_path, "old-1", "smoke", judge={"fingerprint": "aaaaaaaa"})
    assert find_baseline(tmp_path, "current", "smoke", judge_fingerprint="bbbbbbbb") is None


def test_find_baseline_accepts_matching_judge_fingerprint(tmp_path):
    _write_data_json(tmp_path, "old-1", "smoke", judge={"fingerprint": "aaaaaaaa"})
    result = find_baseline(tmp_path, "current", "smoke", judge_fingerprint="aaaaaaaa")
    assert result is not None and result.stem == "old-1-data"


def test_find_baseline_accepts_pre_judge_baseline_with_note(tmp_path):
    """
    A baseline written before judge tracking is unknown, not mismatched.
    Rejecting every historical baseline would blank out the delta on upgrade.
    """
    baseline = _write_data_json(tmp_path, "old-1", "smoke")  # no judge key
    result = find_baseline(tmp_path, "current", "smoke", judge_fingerprint="bbbbbbbb")
    assert result is not None and result.stem == "old-1-data"

    delta = build_delta({}, baseline)
    assert delta["baseline_pre_judge"] is True


def test_delta_not_flagged_pre_judge_when_baseline_has_judge(tmp_path):
    baseline = _write_data_json(tmp_path, "old-1", "smoke", judge={"fingerprint": "aaaaaaaa"})
    assert build_delta({}, baseline)["baseline_pre_judge"] is False


# ---------------------------------------------------------------------------
# Failure rate intervals (spec 04)
# ---------------------------------------------------------------------------

def test_wilson_interval_computed_for_binary_eval():
    config = _make_config()
    rows = [_row(passed=True) for _ in range(4)] + [_row(passed=False)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["failure_rate"] == 0.2
    assert stats["failure_rate_ci"] == [0.0362, 0.6245]
    assert stats["confidence"] == 0.95
    assert stats["total_runs"] == 5


def test_wilson_interval_nondegenerate_at_zero_failures():
    """The normal approximation collapses to [0, 0] here and claims certainty."""
    config = _make_config()
    rows = [_row(passed=True) for _ in range(5)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    low, high = stats["failure_rate_ci"]
    assert stats["failure_rate"] == 0.0
    assert low == 0.0
    assert high > 0.4          # five clean runs is not proof of a zero rate


def test_wilson_interval_narrows_with_sample_size():
    """A five-run rate and a hundred-run rate must not read with equal weight."""
    config = _make_config()
    small = [_row(passed=True) for _ in range(4)] + [_row(passed=False)]
    large = [_row(passed=True) for _ in range(80)] + [_row(passed=False) for _ in range(20)]

    small_ci = build_summary(small, config)["uc1"]["right"]["ev1"]["failure_rate_ci"]
    large_ci = build_summary(large, config)["uc1"]["right"]["ev1"]["failure_rate_ci"]

    assert (small_ci[1] - small_ci[0]) > (large_ci[1] - large_ci[0]) * 3


def test_failure_rate_ci_null_when_rate_null():
    config = _make_config()
    rows = [_row(passed=None, error="boom"), _row(passed=None, error="boom")]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["failure_rate"] is None
    assert stats["failure_rate_ci"] is None


def test_confidence_level_configurable():
    evals = [_make_eval_def("ev1", is_scored=False)]
    config = _make_config(evals)
    config.defaults.confidence = 0.80

    rows = [_row(passed=True) for _ in range(4)] + [_row(passed=False)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["confidence"] == 0.80
    # A less demanding level is a narrower interval.
    wide = build_summary(rows, _make_config(evals))["uc1"]["right"]["ev1"]
    assert (stats["failure_rate_ci"][1] - stats["failure_rate_ci"][0]) < (
        wide["failure_rate_ci"][1] - wide["failure_rate_ci"][0]
    )


def test_scored_eval_summary_unchanged():
    """Scored evals already convey spread through stddev; intervals stay off."""
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals)
    rows = [
        _row(passed=None, score=4, eval_id="ev1", tag="good", ev_type="llm"),
        _row(passed=None, score=5, eval_id="ev1", tag="good", ev_type="llm"),
    ]
    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]

    assert set(stats) == {
        "failure_rate", "mean", "min", "max", "stddev",
        "floor_hits", "total_runs", "error_count",
    }
    assert "failure_rate_ci" not in stats


def test_delta_flags_overlapping_intervals(tmp_path):
    """Movement between overlapping intervals is movement n cannot resolve."""
    baseline = tmp_path / "old-data.json"
    baseline.write_text(json.dumps({
        "run_id": "old", "set": "full", "judge": {"fingerprint": "a"},
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.0, "failure_rate_ci": [0.0, 0.4345],
        }}}},
    }))
    current = {"uc1": {"right": {"ev1": {
        "failure_rate": 0.2, "failure_rate_ci": [0.0362, 0.6245],
    }}}}

    delta = build_delta(current, baseline)
    assert delta["increased"][0]["overlapping"] is True


def test_delta_marks_non_overlapping_intervals(tmp_path):
    baseline = tmp_path / "old-data.json"
    baseline.write_text(json.dumps({
        "run_id": "old", "set": "full", "judge": {"fingerprint": "a"},
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.02, "failure_rate_ci": [0.0, 0.05],
        }}}},
    }))
    current = {"uc1": {"right": {"ev1": {
        "failure_rate": 0.6, "failure_rate_ci": [0.5, 0.7],
    }}}}

    delta = build_delta(current, baseline)
    assert delta["increased"][0]["overlapping"] is False


def test_delta_buckets_unchanged_by_overlap_flag(tmp_path):
    """The flag is additive: existing CI jq expressions keep working."""
    baseline = tmp_path / "old-data.json"
    baseline.write_text(json.dumps({
        "run_id": "old", "set": "full",
        "summary": {"uc1": {"right": {
            "up":   {"failure_rate": 0.0, "failure_rate_ci": [0.0, 0.43]},
            "down": {"failure_rate": 0.8, "failure_rate_ci": [0.4, 0.97]},
            "same": {"failure_rate": 0.5, "failure_rate_ci": [0.2, 0.8]},
        }}},
    }))
    current = {"uc1": {"right": {
        "up":   {"failure_rate": 0.4, "failure_rate_ci": [0.1, 0.8]},
        "down": {"failure_rate": 0.2, "failure_rate_ci": [0.0, 0.6]},
        "same": {"failure_rate": 0.5, "failure_rate_ci": [0.2, 0.8]},
    }}}

    delta = build_delta(current, baseline)
    assert [e["eval_id"] for e in delta["increased"]] == ["up"]
    assert [e["eval_id"] for e in delta["decreased"]] == ["down"]
    assert delta["unchanged"] == ["same"]
    assert all("overlapping" in e for e in delta["increased"] + delta["decreased"])


# ---------------------------------------------------------------------------
# Judge variance decomposition (spec 06)
# ---------------------------------------------------------------------------

def _reps(verdicts: list[bool], fixture_id="fix1", run=1, **kw) -> list[ResultRow]:
    """One output judged len(verdicts) times."""
    return [
        _row(passed=v, fixture_id=fixture_id, run=run, judge_run=i + 1, **kw)
        for i, v in enumerate(verdicts)
    ]


def test_judge_runs_defaults_to_one():
    from fieldtest.config import resolve_judge_runs

    config = _make_config()
    assert config.use_cases[0].fixtures.judge_runs == 1
    assert resolve_judge_runs(config, config.use_cases[0]) == 1


def test_judge_runs_one_produces_identical_output_to_v1():
    """Nobody pays for repeatability unless they ask: no judge fields at all."""
    config = _make_config()
    rows = [_row(passed=True), _row(passed=False, run=2)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert "judge_runs" not in stats
    assert "judge_disagreement_rate" not in stats
    assert stats["failure_rate"] == 0.5


def test_result_row_carries_judge_run():
    """Raw rows stay decomposable in -data.csv without needing the summary."""
    row = _row(judge_run=3)
    assert row.judge_run == 3
    assert row.model_dump()["judge_run"] == 3


def test_binary_disagreement_rate_computed():
    config = _make_config(judge_runs=3)
    rows = (
        _reps([True, True, True], run=1)      # agrees
        + _reps([True, False, True], run=2)   # disagrees
        + _reps([False, False, False], run=3) # agrees
    )
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["judge_runs"] == 3
    assert stats["judge_disagreement_rate"] == round(1 / 3, 6)


def test_binary_verdict_collapses_by_majority():
    config = _make_config(judge_runs=3)
    rows = _reps([True, False, True], run=1)   # majority pass
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["total_runs"] == 1
    assert stats["failure_rate"] == 0.0


def test_binary_tie_collapses_to_fail():
    """A tie means the judge could not decide; for a safe eval that is a fail."""
    config = _make_config(judge_runs=2)
    rows = _reps([True, False], run=1)
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["failure_rate"] == 1.0


def test_failure_rate_denominator_unaffected_by_judge_runs():
    """judge_runs: 3 must not triple the denominator and skew every rate."""
    single = build_summary(
        [_row(passed=True, run=1), _row(passed=False, run=2)],
        _make_config(),
    )["uc1"]["right"]["ev1"]

    tripled = build_summary(
        _reps([True, True, True], run=1) + _reps([False, False, False], run=2),
        _make_config(judge_runs=3),
    )["uc1"]["right"]["ev1"]

    assert single["total_runs"] == tripled["total_runs"] == 2
    assert single["failure_rate"] == tripled["failure_rate"] == 0.5


def test_scored_variance_decomposes_into_system_and_judge():
    """Two sources of variance were summed and blamed on the system."""
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=2)

    def _srow(score, run, judge_run):
        return _row(passed=None, score=score, run=run, judge_run=judge_run,
                    eval_id="ev1", tag="good", ev_type="llm")

    # Output 1 judged 2 and 4; output 2 judged 4 and 2. Per-output means are
    # both 3, so all of the spread belongs to the judge, none to the system.
    rows = [_srow(2, 1, 1), _srow(4, 1, 2), _srow(4, 2, 1), _srow(2, 2, 2)]
    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]

    assert stats["judge_runs"] == 2
    assert stats["system_stddev"] == 0.0
    assert stats["judge_stddev"] == 1.0
    assert stats["stddev"] == 1.0        # unchanged definition over all values


def test_scored_variance_attributes_spread_to_system_when_judge_is_stable():
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=2)

    def _srow(score, run, judge_run):
        return _row(passed=None, score=score, run=run, judge_run=judge_run,
                    eval_id="ev1", tag="good", ev_type="llm")

    # The judge agrees with itself every time; the outputs genuinely differ.
    rows = [_srow(2, 1, 1), _srow(2, 1, 2), _srow(4, 2, 1), _srow(4, 2, 2)]
    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]

    assert stats["judge_stddev"] == 0.0
    assert stats["system_stddev"] == 1.0


def test_scored_summary_has_no_judge_fields_at_one_repetition():
    evals = [_make_eval_def("ev1", is_scored=True)]
    rows = [
        _row(passed=None, score=4, eval_id="ev1", tag="good", ev_type="llm"),
        _row(passed=None, score=5, eval_id="ev1", tag="good", ev_type="llm", run=2),
    ]
    stats = build_summary(rows, _make_config(evals))["uc1"]["good"]["ev1"]

    assert "system_stddev" not in stats
    assert "judge_stddev" not in stats


def test_collapse_rows_is_identity_at_one_repetition():
    from fieldtest.results.aggregator import collapse_rows

    config = _make_config()
    rows = [_row(passed=True), _row(passed=False, run=2)]
    assert collapse_rows(rows, config) == rows


def test_collapse_rows_matches_headline_rate():
    """
    The matrix and tag health count rows. Without collapsing they report 5/6
    while the summary reports 1 of 2, and the two numbers contradict each other.
    """
    from fieldtest.results.aggregator import collapse_rows

    config = _make_config(judge_runs=3)
    rows = _reps([True, True, True], run=1) + _reps([True, False, False], run=2)

    collapsed = collapse_rows(rows, config)
    assert len(collapsed) == 2
    assert sorted(r.passed for r in collapsed) == [False, True]

    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]
    passed_in_view = sum(1 for r in collapsed if r.passed)
    assert passed_in_view / len(collapsed) == 1 - stats["failure_rate"]


def test_collapse_rows_leaves_scored_and_error_rows_alone():
    from fieldtest.results.aggregator import collapse_rows

    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=2)
    rows = [
        _row(passed=None, score=4, eval_id="ev1", tag="good", ev_type="llm", judge_run=1),
        _row(passed=None, score=2, eval_id="ev1", tag="good", ev_type="llm", judge_run=2),
        _row(passed=None, error="boom", eval_id="ev1", tag="good", ev_type="llm"),
    ]
    assert len(collapse_rows(rows, config)) == 3
