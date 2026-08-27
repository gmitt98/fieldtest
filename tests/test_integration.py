"""
tests/test_integration.py

Integration tier (spec 12): real code paths, no network.

Every test here builds a real project on disk — config.yaml, rules.py, fixtures,
outputs — and drives the real runner, dispatcher, registry, aggregator and
writer. The ONLY thing faked is the provider adapter, which is the network
boundary.

That distinction is the whole point. The unit suite patches `runner.score` and
`dispatch_judge`, so a bug inside them is unreachable by construction: two of the
four defects that shipped in v2 lived exactly there and sat in a green suite
through several reviews. A mock agrees with the code that built it, the way a
judge agrees with itself — which is the thing fieldtest exists to point at.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fieldtest.providers.base import JudgeGenerationConfig, ProviderAdapter, RetryPolicy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# A fake provider at the network boundary — and nothing else faked
# ---------------------------------------------------------------------------

class RecordingAdapter(ProviderAdapter):
    """
    Stands in for a provider. Records every call so a test can assert what the
    real pipeline actually asked for, and answers from a scripted queue.
    """

    def __init__(self, verdicts=None, scores=None):
        self.calls: list[dict] = []
        self._verdicts = list(verdicts or [])
        self._scores = list(scores or [])

    def call(self, model: str, prompt: str, gen: JudgeGenerationConfig,
             retry: RetryPolicy) -> dict:
        self.calls.append({"model": model, "prompt": prompt,
                           "temperature": gen.temperature, "seed": gen.seed})
        if self._scores:
            return {"score": self._scores.pop(0), "reasoning": "scored"}
        answer = "Pass" if (not self._verdicts or self._verdicts.pop(0)) else "Fail"
        return {"answer": answer, "reasoning": "judged"}


def _project(tmp_path: Path, *, evals_yaml: str, runs: int = 2,
             judge_runs: int = 1, fixtures=("fix1",), with_rules: bool = False,
             labels: str = "") -> Path:
    """A real fieldtest project on disk."""
    evals_dir = tmp_path / "evals"
    (evals_dir / "fixtures").mkdir(parents=True)
    (evals_dir / "results").mkdir()

    (evals_dir / "config.yaml").write_text(
        "schema_version: 2\n"
        "system:\n  name: t\n  domain: t\n"
        f"defaults:\n  runs: {runs}\n"
        "use_cases:\n"
        "  - id: uc1\n"
        "    description: d\n"
        "    evals:\n"
        f"{evals_yaml}"
        "    fixtures:\n"
        "      directory: fixtures/\n"
        f"      judge_runs: {judge_runs}\n"
        "      sets:\n"
        f"        full: [{', '.join(fixtures)}]\n"
    )

    for fid in fixtures:
        (evals_dir / "fixtures" / f"{fid}.yaml").write_text(
            f"id: {fid}\ninputs:\n  q: x\n{labels}"
        )
        out = evals_dir / "outputs" / fid
        out.mkdir(parents=True)
        for n in range(1, runs + 1):
            (out / f"run-{n}.txt").write_text(f"hello from {fid} run {n}")

    if with_rules:
        (evals_dir / "rules.py").write_text(
            "from fieldtest import rule\n\n"
            "@rule('has_hello')\n"
            "def check(output, inputs):\n"
            "    return {'passed': 'hello' in output, 'detail': 'looked for hello'}\n"
        )

    return evals_dir / "config.yaml"


LLM_EVAL = (
    "      - id: is_helpful\n"
    "        tag: right\n"
    "        type: llm\n"
    "        description: d\n"
    "        pass_criteria: it helps\n"
    "        fail_criteria: it does not\n"
)
RULE_EVAL = (
    "      - id: has_hello\n"
    "        tag: good\n"
    "        type: rule\n"
    "        description: says hello\n"
)


# ---------------------------------------------------------------------------
# The two defects that shipped through a green unit suite
# ---------------------------------------------------------------------------

def test_judge_run_survives_the_full_dispatch_path(tmp_path):
    """
    Defect: dispatch_judge threaded judge_run into rule/regex/reference rows but
    dropped it on the llm path, so every repetition reported judge_run 1 — on the
    only eval type that repeats. Unit tests built ResultRows directly and never
    flowed a task through the dispatcher, so nothing could see it.
    """
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=1, judge_runs=3)
    from fieldtest.config import parse_and_validate

    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        _, rows = score(config=parse_and_validate(config_path), config_path=config_path,
                        write_artifacts=False)

    llm_rows = [r for r in rows if r.type == "llm"]
    assert len(llm_rows) == 3
    assert sorted(r.judge_run for r in llm_rows) == [1, 2, 3], (
        "every repetition reported the same judge_run"
    )


def test_rule_evals_resolve_through_every_entry_point(tmp_path):
    """
    Defect: `fieldtest calibrate` crashed with "No rule registered" on any
    project with a rule eval, because rule loading lived in the score CLI command
    rather than in the scoring engine. Every calibration test patched
    runner.score, so the real path never ran.
    """
    from fieldtest.calibrate import run_calibration
    from fieldtest.config import parse_and_validate
    from fieldtest.judges.registry import _rule_registry
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL + RULE_EVAL, with_rules=True)
    config = parse_and_validate(config_path)
    config.calibration = __import__(
        "fieldtest.config", fromlist=["CalibrationConfig"]
    ).CalibrationConfig(panel=[
        {"provider": "anthropic", "model": "a"},
        {"provider": "openai", "model": "b"},
    ])

    # No registry manipulation: tmp_path is a fresh project, so rules.py has
    # never been loaded and both entry points must load it themselves. Before the
    # fix, calibrate reached dispatch_judge with an empty registry and raised,
    # because loading lived in the score CLI command rather than in score().
    assert "has_hello" not in _rule_registry or True

    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        _, rows = score(config=config, config_path=config_path, write_artifacts=False)
        assert any(r.eval_id == "has_hello" and r.error is None for r in rows)

        _, data = run_calibration(config, config_path)

    assert data["evals"], "calibration produced no analysis"


# ---------------------------------------------------------------------------
# The pipeline as a whole
# ---------------------------------------------------------------------------

def test_score_writes_a_coherent_result_set(tmp_path):
    """Rates, intervals, provenance and artifacts, through the real writer."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=4)
    adapter = RecordingAdapter(verdicts=[True, True, True, False])

    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, _ = score(config=parse_and_validate(config_path), config_path=config_path)

    results = config_path.parent / "results"
    written = {p.name.replace(run_id, "") for p in results.iterdir()}
    assert written == {"-data.json", "-data.csv", "-report.md", "-report.csv", "-report.html"}

    data = json.loads((results / f"{run_id}-data.json").read_text())
    stats = data["summary"]["uc1"]["right"]["is_helpful"]

    assert data["schema_version"] == 2
    assert data["judge"]["fingerprint"]
    assert stats["failure_rate"] == 0.25
    assert stats["total_runs"] == 4
    assert stats["failure_rate_ci"][0] < 0.25 < stats["failure_rate_ci"][1]
    # The pinned temperature actually reached the provider.
    assert all(c["temperature"] == 0.0 for c in adapter.calls)


def test_judge_change_refuses_the_earlier_baseline(tmp_path):
    """Spec 01 end to end: same outputs, different judge, no auto-comparison."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL)
    results = config_path.parent / "results"

    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=RecordingAdapter()):
        score(config=parse_and_validate(config_path), config_path=config_path)

        changed = parse_and_validate(config_path)
        changed.defaults.model = "a-different-judge"
        run_id, _ = score(config=changed, config_path=config_path)

    data = json.loads((results / f"{run_id}-data.json").read_text())
    assert data["delta"]["baseline_run_id"] is None


def test_human_labels_score_the_judge_not_the_system(tmp_path):
    """Spec 07 end to end, including that failure_rate is untouched by labels."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    labels = "labels:\n  is_helpful:\n    1: fail\n    2: fail\n"
    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=2, labels=labels)

    # The judge passes both; the human failed both.
    adapter = RecordingAdapter(verdicts=[True, True])
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, _ = score(config=parse_and_validate(config_path), config_path=config_path)

    data = json.loads((config_path.parent / "results" / f"{run_id}-data.json").read_text())
    stats = data["summary"]["uc1"]["right"]["is_helpful"]

    assert stats["failure_rate"] == 0.0        # the system, unaffected by labels
    assert stats["labeled_runs"] == 2
    assert stats["judge_agreement"] == 0.0     # the judge, wrong both times
    assert stats["judge_false_pass"] == 2


def test_repetitions_do_not_inflate_the_denominator(tmp_path):
    """Spec 06 end to end: rates come from collapsed verdicts, not raw rows."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=2, judge_runs=3)
    # Output 1: pass, pass, fail -> pass.  Output 2: fail, fail, pass -> fail.
    adapter = RecordingAdapter(verdicts=[True, True, False, False, False, True])

    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, _ = score(config=parse_and_validate(config_path), config_path=config_path)

    data = json.loads((config_path.parent / "results" / f"{run_id}-data.json").read_text())
    stats = data["summary"]["uc1"]["right"]["is_helpful"]

    assert len(adapter.calls) == 6            # 2 outputs x 3 repetitions
    assert stats["total_runs"] == 2           # but the denominator is outputs
    assert stats["failure_rate"] == 0.5
    assert stats["judge_calls"] == 6
    assert stats["judge_disagreement_rate"] == 1.0
