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
         judge_run: int = 1, detail: str | None = None) -> ResultRow:
    return ResultRow(
        use_case="uc1", eval_id=eval_id, tag=tag, type=ev_type,
        fixture_id=fixture_id, run=run, judge_run=judge_run,
        passed=passed, error=error, skipped=skipped,
        score=score, floor_hit=floor_hit, detail=detail,
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
        _row(ev_type="llm", passed=True, run=1),
        _row(ev_type="llm", passed=None, error="overloaded", run=2),
        _row(ev_type="llm", passed=None, error="overloaded", run=3),
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
        _row(ev_type="llm", passed=True, run=1),
        _row(ev_type="llm", passed=None, error="overloaded", run=2),
        _row(ev_type="llm", passed=None, error="overloaded", run=3),
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
        _row(passed=True, run=1),
        _row(passed=True, run=2),
        _row(passed=None, error="overloaded", run=3),
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
    assert stats["confidence_level"] == 0.95
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
    config.defaults.confidence_level = 0.80

    rows = [_row(passed=True) for _ in range(4)] + [_row(passed=False)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["confidence_level"] == 0.80
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
        "judge_calls", "outputs_attempted",
    }
    assert "failure_rate_ci" not in stats
    assert "confidence_level" not in stats


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
    # Pooled within-output sample variance: each output contributes
    # ((2-3)^2 + (4-3)^2) / (2-1) = 2, so judge_var = 4/2 = 2 and the judge SD
    # is sqrt(2). Was 1.0 while judge spread was the mean of population (n)
    # SDs, which understates a sample of 2 by sqrt(1/2).
    assert stats["judge_stddev"] == 1.4142
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
    # Per-output means are 2 and 4; the judge contributes nothing to subtract,
    # so system SD is the sample (n-1) SD of the means: sqrt(2). Was 1.0 while
    # system spread was a population (n) SD of the means. The n-1 form is what
    # makes the variance subtraction unbiased, so both halves move together.
    assert stats["system_stddev"] == 1.4142


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


# ---------------------------------------------------------------------------
# Human labels (spec 07)
# ---------------------------------------------------------------------------

def test_fixture_without_labels_unchanged():
    """No labels means no new fields — consumers unaffected until a user opts in."""
    config = _make_config()
    rows = [_row(passed=True), _row(passed=False, run=2)]

    without = build_summary(rows, config)["uc1"]["right"]["ev1"]
    empty   = build_summary(rows, config, labels={})["uc1"]["right"]["ev1"]

    assert without == empty
    assert "judge_agreement" not in without
    assert "labeled_runs" not in without


def test_judge_agreement_computed_from_labels():
    config = _make_config()
    rows = [
        _row(passed=True,  run=1),   # human: pass  → agree
        _row(passed=False, run=2),   # human: fail  → agree
        _row(passed=True,  run=3),   # human: fail  → disagree
    ]
    labels = {
        ("fix1", "ev1", 1): "pass",
        ("fix1", "ev1", 2): "fail",
        ("fix1", "ev1", 3): "fail",
    }
    stats = build_summary(rows, config, labels=labels)["uc1"]["right"]["ev1"]

    assert stats["labeled_runs"] == 3
    assert stats["judge_agreement"] == round(2 / 3, 6)


def test_false_pass_and_false_fail_counted_separately():
    """On a safe eval the false pass is the asymmetric error that matters."""
    config = _make_config()
    rows = [
        _row(passed=True,  run=1),   # human: fail → false pass
        _row(passed=False, run=2),   # human: pass → false fail
        _row(passed=True,  run=3),   # human: pass → agree
    ]
    labels = {
        ("fix1", "ev1", 1): "fail",
        ("fix1", "ev1", 2): "pass",
        ("fix1", "ev1", 3): "pass",
    }
    stats = build_summary(rows, config, labels=labels)["uc1"]["right"]["ev1"]

    assert stats["judge_false_pass"] == 1
    assert stats["judge_false_fail"] == 1
    assert stats["judge_agreement"] == round(1 / 3, 6)


def test_partial_label_coverage_allowed():
    """Partial coverage is the normal state and must not degrade anything."""
    config = _make_config()
    rows = [_row(passed=True, run=1), _row(passed=False, run=2), _row(passed=True, run=3)]
    labels = {("fix1", "ev1", 2): "fail"}

    stats = build_summary(rows, config, labels=labels)["uc1"]["right"]["ev1"]

    assert stats["labeled_runs"] == 1
    assert stats["judge_agreement"] == 1.0
    assert stats["total_runs"] == 3          # unlabeled runs still scored


def test_failure_rate_unaffected_by_labels():
    """Labels score the judge, never the system."""
    config = _make_config()
    rows = [_row(passed=True, run=1), _row(passed=False, run=2)]
    labels = {("fix1", "ev1", 1): "fail", ("fix1", "ev1", 2): "fail"}

    unlabeled = build_summary(rows, config)["uc1"]["right"]["ev1"]
    labeled   = build_summary(rows, config, labels=labels)["uc1"]["right"]["ev1"]

    assert labeled["failure_rate"] == unlabeled["failure_rate"] == 0.5
    assert labeled["failure_rate_ci"] == unlabeled["failure_rate_ci"]
    assert labeled["judge_agreement"] == 0.5


def test_scored_labels_report_mean_absolute_deviation():
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals)

    def _srow(score, run):
        return _row(passed=None, score=score, run=run,
                    eval_id="ev1", tag="good", ev_type="llm")

    rows = [_srow(4, 1), _srow(2, 2)]
    labels = {("fix1", "ev1", 1): 4, ("fix1", "ev1", 2): 5}
    stats = build_summary(rows, config, labels=labels)["uc1"]["good"]["ev1"]

    assert stats["labeled_runs"] == 2
    assert stats["mean_absolute_deviation"] == 1.5     # |4-4| and |2-5|
    assert "judge_false_pass" not in stats


def test_labels_compare_against_collapsed_verdict():
    """With repetitions, the human is compared to one verdict per output."""
    config = _make_config(judge_runs=3)
    rows = _reps([True, False, True], run=1)          # collapses to pass
    labels = {("fix1", "ev1", 1): "pass"}

    stats = build_summary(rows, config, labels=labels)["uc1"]["right"]["ev1"]
    assert stats["labeled_runs"] == 1
    assert stats["judge_agreement"] == 1.0


# ---------------------------------------------------------------------------
# Review findings — regression tests
# ---------------------------------------------------------------------------

def test_judge_calls_and_outputs_counted_in_their_own_units():
    from fieldtest.results.aggregator import summarize_judge_errors

    """
    A judge call is one repetition; an output is one generator run. Conflating
    them reported '3 of 4 calls failed' where the truth was 3 of 6, and claimed
    an eval was scored on 1 of 4 outputs where it was 1 of 2.
    """
    config = _make_config(judge_runs=3)
    rows = (
        _reps([True, True, True], run=1, ev_type="llm")
        + [_row(ev_type="llm", passed=None, error="overloaded", run=2, judge_run=i) for i in (1, 2, 3)]
    )
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["total_runs"] == 1          # outputs scored
    assert stats["error_count"] == 3         # judge calls that errored
    assert stats["judge_calls"] == 6         # judge calls attempted
    assert stats["outputs_attempted"] == 2   # outputs attempted

    errors = summarize_judge_errors(build_summary(rows, config))
    assert errors["failed"] == 3
    assert errors["total"] == 6
    assert errors["affected"] == [("ev1", 1, 2)]


def test_judge_error_units_unchanged_at_one_repetition():
    """At judge_runs: 1 calls and outputs coincide, which is why this hid."""
    from fieldtest.results.aggregator import summarize_judge_errors

    config = _make_config()
    rows = [_row(ev_type="llm", passed=True, run=1), _row(ev_type="llm", passed=None, error="boom", run=2)]
    stats = build_summary(rows, config)["uc1"]["right"]["ev1"]

    assert stats["judge_calls"] == stats["outputs_attempted"] == 2
    assert summarize_judge_errors(build_summary(rows, config))["total"] == 2


def test_scored_labels_report_no_agreement_figure():
    """
    Exact equality between an integer label and a mean across repetitions is
    almost never true: a judge returning 3, 4, 4 against a human's 4 matches
    perfectly on central tendency and would have scored zero agreement.
    """
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=3)

    rows = [
        _row(passed=None, score=s, run=1, judge_run=i + 1,
             eval_id="ev1", tag="good", ev_type="llm")
        for i, s in enumerate([3, 4, 4])
    ]
    labels = {("fix1", "ev1", 1): 4}
    stats = build_summary(rows, config, labels=labels)["uc1"]["good"]["ev1"]

    assert "judge_agreement" not in stats
    assert stats["labeled_runs"] == 1
    assert stats["mean_absolute_deviation"] == round(abs(11 / 3 - 4), 4)


def test_delta_shape_is_the_same_with_and_without_a_baseline(tmp_path):
    """A consumer indexing .delta must not meet two different shapes."""
    baseline = _write_data_json(tmp_path, "old-1", "full", judge={"fingerprint": "a"})

    with_baseline    = build_delta({}, baseline)
    without_baseline = build_delta({}, None)

    assert set(with_baseline) == set(without_baseline)
    assert without_baseline["baseline_pre_judge"] is False
    assert without_baseline["baseline_judge_runs"] is None


def test_scored_and_binary_report_n_in_the_same_unit():
    """
    The binary branch moved total_runs to collapsed outputs under judge_runs > 1
    while the scored branch stayed on raw repetitions, so one report table
    rendered an n column meaning two different things row to row.
    """
    evals = [_make_eval_def("binary_ev", is_scored=False),
             _make_eval_def("scored_ev", is_scored=True)]
    config = _make_config(evals, judge_runs=3)

    rows = []
    for run in (1, 2):
        for jr in (1, 2, 3):
            rows.append(_row(passed=True, run=run, judge_run=jr, eval_id="binary_ev"))
            rows.append(_row(passed=None, score=4, run=run, judge_run=jr,
                             eval_id="scored_ev", tag="good", ev_type="llm"))

    summary = build_summary(rows, config)
    binary = summary["uc1"]["right"]["binary_ev"]
    scored = summary["uc1"]["good"]["scored_ev"]

    assert binary["total_runs"] == 2      # outputs
    assert scored["total_runs"] == 2      # outputs, not 6 repetitions
    # The raw-score statistics keep their definitions over every value.
    assert scored["mean"] == 4.0


def test_collapsed_row_detail_matches_its_verdict():
    """
    The collapsed row took the first repetition's reasoning unconditionally, so
    a majority-fail output could carry text arguing that it passed.
    """
    from fieldtest.results.aggregator import collapse_rows

    config = _make_config(judge_runs=3)
    reps = [
        _row(passed=True,  run=1, judge_run=1, detail="meets all criteria"),
        _row(passed=False, run=1, judge_run=2, detail="invents a refund guarantee"),
        _row(passed=False, run=1, judge_run=3, detail="invents a refund guarantee"),
    ]

    collapsed = collapse_rows(reps, config)

    assert len(collapsed) == 1
    row = collapsed[0]
    assert row.passed is False
    assert "invents a refund guarantee" in row.detail
    assert "meets all criteria" not in row.detail
    # The split itself is worth seeing on an ambiguous eval.
    assert "[2/3 judges]" in row.detail


def test_collapsed_row_detail_unannotated_when_judges_agree():
    from fieldtest.results.aggregator import collapse_rows

    config = _make_config(judge_runs=3)
    reps = [_row(passed=False, run=1, judge_run=i, detail="clear violation")
            for i in (1, 2, 3)]

    row = collapse_rows(reps, config)[0]
    assert row.detail == "clear violation"


# ---------------------------------------------------------------------------
# Endpoint in the judge fingerprint (spec 11)
#
# Same model name on two endpoints is two instruments. Without base_url in the
# fingerprint, find_baseline() would compare a local 70B against a hosted one.
# ---------------------------------------------------------------------------

def _config_with_endpoint(base_url: str) -> Config:
    from fieldtest.config import ProviderSettings

    cfg = _make_config()
    cfg.defaults.provider = "openai_compatible"
    cfg.defaults.model    = "llama-3.3-70b-instruct"
    cfg.providers = {"openai_compatible": ProviderSettings(base_url=base_url)}
    return cfg


def test_fingerprint_includes_base_url():
    from fieldtest.results.provenance import build_judge_block

    judge = build_judge_block(_config_with_endpoint("http://localhost:8000/v1"))
    assert judge["endpoints"] == {"openai_compatible": "http://localhost:8000/v1"}


def test_fingerprint_differs_across_endpoints_for_the_same_model():
    from fieldtest.results.provenance import build_judge_block

    local  = build_judge_block(_config_with_endpoint("http://localhost:8000/v1"))
    hosted = build_judge_block(_config_with_endpoint("https://openrouter.ai/api/v1"))
    assert local["model"] == hosted["model"]
    assert local["fingerprint"] != hosted["fingerprint"]


def test_endpoint_change_is_named_in_the_judge_diff():
    from fieldtest.results.provenance import build_judge_block, describe_judge_change

    local  = build_judge_block(_config_with_endpoint("http://localhost:8000/v1"))
    hosted = build_judge_block(_config_with_endpoint("https://openrouter.ai/api/v1"))
    described = describe_judge_change(hosted, local)
    assert "openai_compatible endpoint" in described
    assert "openrouter.ai" in described


def test_fingerprint_unchanged_for_configs_without_endpoints():
    """
    A config naming no endpoint must hash the same with and without the
    endpoints field, so the fingerprint stays additive. The payload without it
    is rebuilt here rather than trusted.
    """
    import hashlib
    import json

    from fieldtest.results.provenance import build_judge_block, judge_fingerprint

    judge = build_judge_block(_make_config())
    assert "endpoints" not in judge

    before = {
        "provider":    judge["provider"],
        "model":       judge["model"],
        "temperature": judge["temperature"],
        "seed":        judge["seed"],
        "overrides":   judge["overrides"],
        "blinded_evals": judge["blinded_evals"],
    }
    canonical = json.dumps(before, sort_keys=True, separators=(",", ":"))
    assert judge_fingerprint(judge) == hashlib.sha256(canonical.encode()).hexdigest()[:8]


def test_delta_records_how_much_of_the_baseline_errored(tmp_path):
    """
    A baseline whose judge calls largely failed is a rate over whatever
    survived. One real run lost 140 of 237 calls to an exhausted balance,
    silently became the next run's baseline, and produced a 26-point "drop"
    against a third of the evidence.
    """
    import json

    from fieldtest.results.aggregator import build_delta

    baseline = tmp_path / "b-data.json"
    baseline.write_text(json.dumps({
        "run_id": "b", "judge": {"model": "m"}, "judge_runs": 1,
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.5, "total_runs": 10, "error_count": 30,
        }}}},
    }))
    current = {"uc1": {"right": {"ev1": {"failure_rate": 0.2, "total_runs": 40,
                                         "error_count": 0}}}}

    delta = build_delta(current, baseline)
    assert delta["baseline_error_share"] == 0.75
    # The comparison is kept — the caveat is the point, not suppression.
    assert delta["decreased"] or delta["increased"]


def test_a_clean_baseline_reports_no_error_share(tmp_path):
    import json

    from fieldtest.results.aggregator import build_delta

    baseline = tmp_path / "b-data.json"
    baseline.write_text(json.dumps({
        "run_id": "b", "judge": {"model": "m"}, "judge_runs": 1,
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.5, "total_runs": 40, "error_count": 0,
        }}}},
    }))
    delta = build_delta(
        {"uc1": {"right": {"ev1": {"failure_rate": 0.2, "total_runs": 40}}}}, baseline
    )
    assert delta["baseline_error_share"] == 0.0


def test_no_baseline_reports_no_error_share():
    from fieldtest.results.aggregator import build_delta

    assert build_delta({}, None)["baseline_error_share"] == 0.0


def test_delta_records_the_baseline_fixture_count(tmp_path):
    """
    A set can be redefined between runs. Comparing a rate over 14 fixtures
    against one over 11 is not like-for-like, and the deltas read as a change in
    the system rather than a change of population.
    """
    import json

    from fieldtest.results.aggregator import build_delta

    baseline = tmp_path / "b-data.json"
    baseline.write_text(json.dumps({
        "run_id": "b", "judge": {"model": "m"}, "judge_runs": 1,
        "fixture_count": 14,
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.5, "total_runs": 42, "error_count": 0}}}},
    }))
    delta = build_delta(
        {"uc1": {"right": {"ev1": {"failure_rate": 0.2, "total_runs": 33}}}}, baseline
    )
    assert delta["baseline_fixture_count"] == 14
    assert build_delta({}, None)["baseline_fixture_count"] is None

    # fixture_count alone is not enough: it counts what is on disk and does not
    # move when a *set* is redefined. The per-eval n does.
    assert delta["sample_changed"] == ["ev1 42→33"]


def test_an_unchanged_sample_reports_nothing(tmp_path):
    import json

    from fieldtest.results.aggregator import build_delta

    baseline = tmp_path / "b-data.json"
    baseline.write_text(json.dumps({
        "run_id": "b", "judge": {"model": "m"}, "judge_runs": 1, "fixture_count": 11,
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": 0.5, "total_runs": 33, "error_count": 0}}}},
    }))
    delta = build_delta(
        {"uc1": {"right": {"ev1": {"failure_rate": 0.2, "total_runs": 33}}}}, baseline
    )
    assert delta["sample_changed"] == []


def test_summary_eval_order_does_not_depend_on_row_arrival_order():
    """
    Rows arrive from as_completed(), so judge latency decided table order and
    two identical runs produced different reports — in markdown, HTML and JSON.
    """
    import random

    rows = _rows_for_two_evals()
    config = _config_for_two_evals()

    baseline = _eval_order(build_summary(rows, config))
    for seed in range(6):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert _eval_order(build_summary(shuffled, config)) == baseline, (
            f"row order changed the table order (seed {seed})"
        )

    # And that order is the order the evals are declared in, not alphabetical.
    declared = [ev.id for ev in config.use_cases[0].evals]
    assert baseline == declared, f"expected config order {declared}, got {baseline}"


def _eval_order(summary: dict) -> list[str]:
    out = []
    for uc in summary.values():
        for tag_evals in uc.values():
            if isinstance(tag_evals, dict):
                out.extend(k for k in tag_evals if isinstance(tag_evals[k], dict))
    return out


def _config_for_two_evals():
    return Config.model_validate({
        "schema_version": 1,
        "system": {"name": "s", "domain": "d"},
        "use_cases": [{
            "id": "uc1",
            "description": "d",
            # Declared zebra-first so config order and alphabetical differ.
            "evals": [
                {"id": "zebra_check", "tag": "right", "type": "regex",
                 "description": "d", "pattern": "z", "match": True},
                {"id": "alpha_check", "tag": "right", "type": "regex",
                 "description": "d", "pattern": "a", "match": True},
            ],
            "fixtures": {"directory": "fixtures/", "sets": {"full": ["f1"]}},
        }],
    })


def _rows_for_two_evals():
    rows = []
    for eval_id in ("zebra_check", "alpha_check"):
        for run in (1, 2, 3):
            rows.append(ResultRow(
                use_case="uc1", eval_id=eval_id, tag="right",
                fixture_id="f1", run=run, passed=run != 3, detail="", type="regex",
            ))
    return rows


def test_floor_hits_are_counted_per_output_not_per_judge_call():
    """
    n moved to outputs when judge_runs landed; floor_hits did not follow it, so
    two tainted outputs judged three times each read as 6 floor hits out of an
    n of 8 — a floor rate of 75% where the true one is 25%.
    """
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=3)
    rows = []
    for fixture, at_floor in (("fx-a", True), ("fx-b", False)):
        for jr in (1, 2, 3):
            rows.append(_row(
                passed=None, score=1 if at_floor else 4, eval_id="ev1",
                tag="good", ev_type="llm", fixture_id=fixture, judge_run=jr,
            ))

    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]
    assert stats["total_runs"] == 2, "n should count outputs"
    assert stats["floor_hits"] == 1, "one of the two outputs sat at the floor"
    assert stats["floor_hit_calls"] == 3, "three of the six judge calls did"


def test_a_split_verdict_on_the_floor_collapses_the_way_a_binary_one_does():
    """Ties go to the floor, matching how a split pass/fail collapses to fail."""
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=2)
    rows = [
        _row(passed=None, score=1, eval_id="ev1", tag="good", ev_type="llm",
             fixture_id="fx-a", judge_run=1),
        _row(passed=None, score=5, eval_id="ev1", tag="good", ev_type="llm",
             fixture_id="fx-a", judge_run=2),
    ]
    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]
    assert stats["floor_hits"] == 1


def test_the_same_eval_id_in_two_use_cases_keeps_its_own_type():
    """
    eval_meta was keyed by eval_id alone, so a later definition overwrote an
    earlier one. Where the two differed in type, a binary eval inherited
    is_scored from a scored namesake and reported failure_rate: null — an eval
    failing every run showed nothing at all.
    """
    config = Config.model_validate({
        "schema_version": 1,
        "system": {"name": "s", "domain": "d"},
        "use_cases": [
            {"id": "uc1", "description": "d",
             "evals": [{"id": "shared", "tag": "good", "type": "llm",
                        "description": "d", "pass_criteria": "a", "fail_criteria": "b"}],
             "fixtures": {"directory": "fixtures/", "sets": {"full": ["a"]}}},
            {"id": "uc2", "description": "d",
             "evals": [{"id": "shared", "tag": "good", "type": "llm",
                        "description": "d", "binary": False, "scale": [1, 5],
                        "anchors": {1: "x", 5: "y"}}],
             "fixtures": {"directory": "fixtures/", "sets": {"full": ["b"]}}},
        ],
    })
    rows = [
        ResultRow(use_case="uc1", eval_id="shared", tag="good", type="llm",
                  fixture_id="a", run=1, passed=False, detail=""),
        ResultRow(use_case="uc1", eval_id="shared", tag="good", type="llm",
                  fixture_id="a", run=2, passed=False, detail=""),
        ResultRow(use_case="uc2", eval_id="shared", tag="good", type="llm",
                  fixture_id="b", run=1, score=4, detail=""),
    ]
    summary = build_summary(rows, config)
    assert summary["uc1"]["good"]["shared"]["failure_rate"] == 1.0, \
        "the binary eval lost its failure rate to its scored namesake"
    assert summary["uc2"]["good"]["shared"]["mean"] == 4.0
    assert summary["uc2"]["good"]["shared"]["failure_rate"] is None

# ---------------------------------------------------------------------------
# Variance decomposition — unbiasedness
# ---------------------------------------------------------------------------

def _rule_eval(eval_id: str) -> Eval:
    return Eval(id=eval_id, tag="safe", type="rule", description="check it")


def test_decompose_variance_does_not_blame_the_system_for_judge_noise():
    """
    Four outputs of identical true quality, judged three times each by a noisy
    judge. True system spread is exactly 0 by construction, so a decomposition
    reporting system > judge sends the reader to fix the wrong thing.

    The old pair of formulas — SD of the per-output means, and the mean of the
    per-output population SDs — reported system 0.4714 > judge 0.4398 here.
    """
    from fieldtest.results.aggregator import decompose_variance

    system_sd, judge_sd = decompose_variance([[2, 3, 4], [2, 2, 3], [4, 4, 3], [3, 3, 3]])

    assert judge_sd > system_sd, (
        f"all spread here is judge noise; got system={system_sd} judge={judge_sd}"
    )
    # Pooled within-output variance: squared deviations 2, 2/3, 2/3, 0 over
    # 8 degrees of freedom = 0.41667, so judge SD = sqrt(0.41667) = 0.6455.
    assert round(judge_sd, 4) == 0.6455


def test_decompose_variance_recovers_known_components():
    """
    Hand-computable. Three outputs, two judge calls each:
      f1 [10, 12]   f2 [20, 22]   f3 [30, 32]

    Within each output the sample variance is (1 + 1) / (2 - 1) = 2, so the
    pooled judge variance is 2 and judge_sd = sqrt(2) = 1.41421.
    The means are 11, 21, 31, with sample variance 100. Each mean carries
    judge_var / 2 = 1 of that, so system_var = 100 - 1 = 99 and
    system_sd = sqrt(99) = 9.94987.
    """
    from fieldtest.results.aggregator import decompose_variance

    system_sd, judge_sd = decompose_variance([[10, 12], [20, 22], [30, 32]])

    assert round(judge_sd, 5) == 1.41421
    assert round(system_sd, 5) == 9.94987


def test_decompose_variance_clamps_system_spread_to_zero():
    """
    Identical per-output means with a disagreeing judge: the between-means term
    is smaller than the judge noise it carries, so the unclamped estimate goes
    negative. 0.0 is the honest reading, and a negative variance would take the
    sqrt down with it.
    """
    from fieldtest.results.aggregator import decompose_variance

    system_sd, judge_sd = decompose_variance([[1, 5], [5, 1], [3, 3]])

    assert system_sd == 0.0
    assert judge_sd > 0.0


def test_scored_summary_names_the_judge_when_the_judge_is_the_larger_source():
    """
    The Judge Repeatability table exists to answer one question: is the spread
    the system or the criteria? On data where the judge dominates, the summary
    has to say so, or the reader leaves an ambiguous pass_criteria in place.
    """
    evals = [_make_eval_def("ev1", is_scored=True)]
    config = _make_config(evals, judge_runs=3)

    # Four outputs of identical true quality, judged three times each. Every
    # point of spread here is the judge; true system spread is 0. The old
    # formulas inverted this exact case, reporting system 0.4714 > judge 0.4398.
    scores_by_output = [[2, 3, 4], [2, 2, 3], [4, 4, 3], [3, 3, 3]]
    rows = []
    for run, rep_scores in enumerate(scores_by_output, start=1):
        for jr, score in enumerate(rep_scores, start=1):
            rows.append(_row(passed=None, score=score, run=run, judge_run=jr,
                             eval_id="ev1", tag="good", ev_type="llm"))

    stats = build_summary(rows, config)["uc1"]["good"]["ev1"]

    assert stats["judge_stddev"] > stats["system_stddev"], (
        "outputs are of identical quality, so the judge is the only source of "
        f"spread; got system={stats['system_stddev']} judge={stats['judge_stddev']}"
    )


# ---------------------------------------------------------------------------
# Rule returns carrying no verdict
# ---------------------------------------------------------------------------

def test_rule_without_passed_key_is_an_error_not_a_pass():
    """
    A rule returning {"pass": ...} instead of {"passed": ...} used to produce
    passed=None with error=None. That row landed in the failure_rate denominator
    but was never counted a failure, so a `safe` eval on which every check failed
    reported 100% pass with a confidence interval and zero errors — while Tag
    Health, reading the same rows in the same report, reported 0%.
    """
    from fieldtest.judges.dispatch import dispatch_judge
    from fieldtest.judges.registry import _rule_registry, rule

    ev = _rule_eval("no_banned")

    @rule("no_banned")
    def _misspelled(output: str, inputs: dict) -> dict:
        return {"pass": False, "detail": "output contained a banned phrase"}

    try:
        config = _make_config([ev])
        rows = [
            dispatch_judge("uc1", ev, "bad", {"id": f"f{i}"}, 1, config)
            for i in range(4)
        ]

        assert all(r.passed is None for r in rows)
        assert all(r.error is not None for r in rows)
        assert "no usable verdict" in rows[0].error

        stats = build_summary(rows, config)["uc1"]["safe"]["no_banned"]
        assert stats["error_count"] == 4
        assert stats["total_runs"] == 0
        assert stats["failure_rate"] is None, (
            "four unusable rule returns must not read as a 0% failure rate"
        )
    finally:
        _rule_registry.pop("no_banned", None)


def test_rule_returning_non_dict_is_an_error():
    """A rule that falls off the end and returns None carries no verdict."""
    from fieldtest.judges.dispatch import dispatch_judge
    from fieldtest.judges.registry import _rule_registry, rule

    ev = _rule_eval("bare")

    @rule("bare")
    def _bare(output: str, inputs: dict):
        return None

    try:
        row = dispatch_judge("uc1", ev, "out", {"id": "f1"}, 1, _make_config([ev]))
        assert row.passed is None
        assert row.error is not None and "no usable verdict" in row.error
    finally:
        _rule_registry.pop("bare", None)


def test_rule_with_a_proper_verdict_still_passes_through():
    """The guard must not disturb a conforming rule."""
    from fieldtest.judges.dispatch import dispatch_judge
    from fieldtest.judges.registry import _rule_registry, rule

    ev = _rule_eval("ok")

    @rule("ok")
    def _ok(output: str, inputs: dict) -> dict:
        return {"passed": False, "detail": "banned phrase found"}

    try:
        row = dispatch_judge("uc1", ev, "out", {"id": "f1"}, 1, _make_config([ev]))
        assert row.passed is False
        assert row.error is None
        assert row.detail == "banned phrase found"
    finally:
        _rule_registry.pop("ok", None)
# ---------------------------------------------------------------------------
# The report's numbers, read as a reader reads them
#
# Every existing format_report assertion targets a narrow substring — an error
# banner, a "scored" marker, a heading. None reads the rate, the tag table or
# the failure list, so those three could be inverted with the suite green:
# the pass-rate cell could print the failure rate (75% shown as 25%, beside an
# unchanged 30-95% interval), Tag Health could render its header and no body,
# and Failure Details could list every passing run instead of the failing ones.
# ---------------------------------------------------------------------------

def _report_of_three_passes_and_one_failure() -> str:
    """Two fixtures, four judged outputs, one failure. Pass rate is 75%."""
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [
        _row(passed=True,  fixture_id="f1", run=1, detail="cited the handbook"),
        _row(passed=True,  fixture_id="f1", run=2, detail="cited the handbook"),
        _row(passed=True,  fixture_id="f2", run=1, detail="cited the handbook"),
        _row(passed=False, fixture_id="f2", run=2, detail="missed the citation"),
    ]
    return format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )


def test_report_eval_row_shows_the_pass_rate_not_the_failure_rate():
    """
    The column is headed "pass rate" and the interval beside it is the failure
    interval inverted. Print the failure rate there and the cell contradicts
    its own interval: 25% with a 30-95% bound.
    """
    report = _report_of_three_passes_and_one_failure()

    assert "| ev1 | — | 75% [30–95%] | 4 | — | 0 | 0 | — |" in report


def test_report_tag_health_table_has_a_body():
    """
    A header with no rows still looks like a table. Skipped and errored rows
    are outside the rate and must not reach the denominator.
    """
    from fieldtest.results.report import format_report

    config = _make_config()
    rows = [
        _row(passed=True,  fixture_id="f1", run=1),
        _row(passed=True,  fixture_id="f1", run=2),
        _row(passed=True,  fixture_id="f2", run=1),
        _row(passed=False, fixture_id="f2", run=2),
        _row(passed=None,  fixture_id="f3", run=1, error="overloaded"),
        _row(passed=None,  fixture_id="f4", run=1, skipped=True),
    ]
    report = format_report(
        rows=rows, summary=build_summary(rows, config), delta={},
        config=config, run_id="test-run", set_name="full",
    )

    assert "### Tag Health" in report
    assert "| RIGHT | 75% | 3 / 4 |" in report


def test_report_failure_details_lists_the_failures_and_only_those():
    """The section that tells a user what broke must not list what worked."""
    report = _report_of_three_passes_and_one_failure()

    assert "### Failure Details" in report
    assert "- `f2` run 2: missed the citation" in report
    assert "cited the handbook" not in report


def test_collapse_rows_collapses_at_two_repetitions():
    """
    The binary-collapse tests all use judge_runs: 3, so the boundary at 2 —
    the cheaper and more common setting — was never crossed. At judge_runs: 2
    an off-by-one in the identity guard hands the row-counting views two rows
    per output, which is exactly the double-counting this function exists to
    prevent.
    """
    from fieldtest.results.aggregator import collapse_rows

    config = _make_config(judge_runs=2)
    rows = _reps([True, True], run=1) + _reps([False, False], run=2)

    collapsed = collapse_rows(rows, config)

    assert len(collapsed) == 2
    assert sorted(r.passed for r in collapsed) == [False, True]


def test_a_delta_lands_on_the_use_case_it_belongs_to(tmp_path):
    """
    Delta entries carried a bare eval_id and the report indexed them by it. With
    the same eval id in two use cases, one use case's regression was printed
    against the other's row — a stable eval reading -90%.
    """
    import json

    baseline = {
        "uc1": {"safe": {"shared": {"failure_rate": 0.0, "total_runs": 10}}},
        "uc2": {"safe": {"shared": {"failure_rate": 0.0, "total_runs": 10}}},
    }
    current = {
        "uc1": {"safe": {"shared": {"failure_rate": 0.0, "total_runs": 10}}},
        "uc2": {"safe": {"shared": {"failure_rate": 0.9, "total_runs": 10}}},
    }
    b = tmp_path / "b-data.json"
    b.write_text(json.dumps({"run_id": "b", "set": "full", "summary": baseline}))

    delta = build_delta(current, b)
    moved = [i for i in delta["increased"] if i["eval_id"] == "shared"]
    assert len(moved) == 1
    assert moved[0]["use_case"] == "uc2", (
        f"the regression is uc2's; it is attributed to {moved[0].get('use_case')!r}")
    assert {(u["use_case"], u["eval_id"]) for u in delta["unchanged_keys"]} == {("uc1", "shared")}


def test_the_report_puts_a_shared_eval_id_delta_on_the_right_row(tmp_path):
    """End to end: the `vs prior` column, not just the delta structure."""
    import json

    from fieldtest.results.report import format_report

    config = Config.model_validate({
        "schema_version": 1,
        "system": {"name": "s", "domain": "d"},
        "use_cases": [
            {"id": u, "description": "d",
             "evals": [{"id": "shared", "tag": "safe", "type": "regex",
                        "description": "d", "pattern": "x", "match": True}],
             "fixtures": {"directory": "fixtures/", "sets": {"full": [f"f-{u}"]}}}
            for u in ("uc1", "uc2")
        ],
    })
    rows = []
    for uc, passed in (("uc1", True), ("uc2", False)):
        for run in (1, 2):
            rows.append(ResultRow(use_case=uc, eval_id="shared", tag="safe",
                                  type="regex", fixture_id=f"f-{uc}", run=run,
                                  passed=passed, detail=""))

    baseline = {u: {"safe": {"shared": {"failure_rate": 0.0, "total_runs": 2}}}
                for u in ("uc1", "uc2")}
    b = tmp_path / "b-data.json"
    b.write_text(json.dumps({"run_id": "b", "set": "full", "summary": baseline}))

    summary = build_summary(rows, config)
    report = format_report(rows, summary, build_delta(summary, b), config, "r", "full")

    uc1_block = report.split("## uc1")[1].split("## uc2")[0]
    uc2_block = report.split("## uc2")[1]
    assert "↔" in uc1_block, f"uc1's eval did not move; it should read ↔:\n{uc1_block}"
    assert "%" in uc2_block.split("| shared |")[1].split("\n")[0], (
        f"uc2's regression is missing from its own row:\n{uc2_block}")


# ---------------------------------------------------------------------------
# The floor-hit review list, and the judge_runs-mismatch caveat.
#
# The list is the only place the report names files for a human to open. It has
# to name the outputs the count counted, under the same rule, attributed to the
# eval that actually scored them at the floor.
# ---------------------------------------------------------------------------

def test_floor_hit_list_names_only_the_outputs_the_count_counted():
    """floor_hits is collapsed by majority; the list has to use the same rule.

    Listing the raw per-judge-call flag and de-duplicating by output listed
    every output where one repetition of three hit the floor. An output the
    judge scored 1, 5, 5 is not a floor hit — the count excludes it — yet the
    warning beside that count handed it to the user to review.
    """
    from fieldtest.results.report import format_report

    config = _make_config(evals=[_make_eval_def("s1", is_scored=True)], judge_runs=3)
    rows = []
    for run, scores in ((1, (1, 1, 5)), (2, (1, 5, 5))):
        for judge_run, score in enumerate(scores, start=1):
            rows.append(_row(
                passed=None, score=score, eval_id="s1", tag="good", ev_type="llm",
                floor_hit=score == 1, fixture_id="f1", run=run, judge_run=judge_run,
            ))

    summary = build_summary(rows, config)
    assert summary["uc1"]["good"]["s1"]["floor_hits"] == 1, "majority rule: only run-1"

    report = format_report(
        rows=rows, summary=summary, delta={},
        config=config, run_id="r", set_name="full",
    )
    listed = [ln for ln in report.splitlines() if ln.startswith("⚠ floor hits")]
    assert len(listed) == 1, f"expected one floor-hit block:\n{report}"
    assert "outputs/f1/run-1.txt" in listed[0], listed[0]
    assert "outputs/f1/run-2.txt" not in listed[0], (
        "run-2 scored 1, 5, 5 — the count does not call it a floor hit, so the "
        f"review list must not either:\n{listed[0]}"
    )


def test_floor_hit_list_attributes_each_output_to_the_eval_that_scored_it():
    """Two scored evals, two scales, one output at the floor of both.

    A single flat list labelled with one eval id printed the filename twice and
    reported grounding's 0 on a 0-10 scale as `helpfulness scored 1/5`.
    """
    from fieldtest.results.report import format_report

    helpfulness = Eval(
        id="helpfulness", tag="good", type="llm", binary=False,
        description="rate it", scale=[1, 5], anchors={1: "bad", 5: "great"},
    )
    grounding = Eval(
        id="grounding", tag="good", type="llm", binary=False,
        description="rate it", scale=[0, 10], anchors={0: "bad", 10: "great"},
    )
    config = _make_config(evals=[helpfulness, grounding])
    rows = [
        _row(passed=None, score=1, eval_id="helpfulness", tag="good", ev_type="llm",
             floor_hit=True, fixture_id="f1", run=1),
        _row(passed=None, score=4, eval_id="helpfulness", tag="good", ev_type="llm",
             fixture_id="f1", run=2),
        _row(passed=None, score=0, eval_id="grounding", tag="good", ev_type="llm",
             floor_hit=True, fixture_id="f1", run=1),
        _row(passed=None, score=7, eval_id="grounding", tag="good", ev_type="llm",
             fixture_id="f1", run=2),
    ]

    summary = build_summary(rows, config)
    assert summary["uc1"]["good"]["helpfulness"]["floor_hits"] == 1
    assert summary["uc1"]["good"]["grounding"]["floor_hits"] == 1

    report = format_report(
        rows=rows, summary=summary, delta={},
        config=config, run_id="r", set_name="full",
    )
    blocks = [ln for ln in report.splitlines() if ln.startswith("⚠ floor hits")]
    captions = [ln for ln in report.splitlines() if "review these outputs" in ln]
    assert len(blocks) == 2, f"one block per eval with floor hits:\n{report}"
    for block in blocks:
        assert block.count("outputs/f1/run-1.txt") == 1, (
            f"the same output is listed twice in one block:\n{block}"
        )
    assert any("helpfulness scored 1/5" in c for c in captions), captions
    assert any("grounding scored 0/10" in c for c in captions), (
        "grounding's floor hit is at 0 on a 0-10 scale, not 1 on a 1-5 one: "
        f"{captions}"
    )


def test_judge_runs_mismatch_caveat_is_a_whole_sentence(tmp_path):
    """The caveat lost its subject to an edit and rendered as a fragment:
    `— judge spread figures do not.` It has to state what is not comparable."""
    from fieldtest.results.report import format_report

    config = _make_config(evals=[_make_eval_def("ev1", is_scored=False)], judge_runs=2)
    rows = [
        _row(passed=True, fixture_id="f1", run=1, judge_run=1),
        _row(passed=True, fixture_id="f1", run=1, judge_run=2),
    ]
    summary = build_summary(rows, config)
    baseline = tmp_path / "b-data.json"
    baseline.write_text(json.dumps({
        "run_id": "b", "set": "full", "judge": {}, "judge_runs": 1,
        "summary": summary,
    }))

    report = format_report(
        rows=rows, summary=summary, delta=build_delta(summary, baseline),
        config=config, run_id="r", set_name="full",
    )
    caveat = next(
        ln for ln in report.splitlines()
        if ln.startswith("⚠ baseline judged each output")
    )
    tail = caveat.split("—", 1)[1]
    assert tail.strip() != "judge spread figures do not.", (
        f"the sentence is still truncated: {caveat}"
    )
    assert "ties" in tail and "fail" in tail, (
        f"the caveat must say why the rates move — ties resolve to fail: {caveat}"
    )
    assert "not comparable" in tail, (
        f"the caveat must say the judge spread figures are not comparable: {caveat}"
    )


def test_a_broken_rule_is_not_blamed_on_the_api_key():
    """
    A `rule` eval runs the user's own Python. When one raised, the report said
    "check your API key if errors persist" — sending them to the one place the
    bug is not, and never showing the exception.
    """
    from fieldtest.results.report import format_report

    config = _make_config([Eval(id="broken", tag="right", type="rule",
                                description="a rule of your own")])
    rows = [ResultRow(use_case="uc1", eval_id="broken", tag="right", type="rule",
                      fixture_id="f1", run=1,
                      error="ValueError: my rule has a bug")]
    report = format_report(rows, build_summary(rows, config), {}, config, "r", "full")

    assert "check your API key" not in report, (
        f"a rule error was blamed on the credential:\n{report}")
    assert "evals/rules.py" in report
    assert "my rule has a bug" in report, "the actual exception is not shown"


def test_a_provider_error_still_gets_provider_advice():
    """The rule branch must not swallow the credential case."""
    from fieldtest.results.report import format_report

    config = _make_config([Eval(id="judged", tag="right", type="llm",
                                description="d", pass_criteria="ok",
                                fail_criteria="not ok")])
    rows = [ResultRow(use_case="uc1", eval_id="judged", tag="right", type="llm",
                      fixture_id="f1", run=1,
                      error="Connection reset by peer")]
    report = format_report(rows, build_summary(rows, config), {}, config, "r", "full")
    assert "check your API key" in report or "concurrency 1" in report, report
