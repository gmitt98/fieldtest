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
from fieldtest.results.aggregator import build_delta, build_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(evals: list[Eval] | None = None) -> Config:
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
                fixtures=FixturesConfig(directory="fixtures/", sets={"full": []}),
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
         floor_hit: bool = False) -> ResultRow:
    return ResultRow(
        use_case="uc1", eval_id=eval_id, tag=tag, type=ev_type,
        fixture_id="fix1", run=1,
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
    p = tmp_path / f"{run_id}.json"
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
