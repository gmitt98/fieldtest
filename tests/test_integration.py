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
             labels: str = "", defaults_extra: str = "",
             providers_py: str = "") -> Path:
    """A real fieldtest project on disk."""
    evals_dir = tmp_path / "evals"
    (evals_dir / "fixtures").mkdir(parents=True)
    (evals_dir / "results").mkdir()

    (evals_dir / "config.yaml").write_text(
        "schema_version: 2\n"
        "system:\n  name: t\n  domain: t\n"
        f"defaults:\n  runs: {runs}\n"
        f"{defaults_extra}"
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

    if providers_py:
        (evals_dir / "providers.py").write_text(providers_py)

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
    from fieldtest.judges.registry import _loaded_rule_files, _rule_registry
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL + RULE_EVAL, with_rules=True)
    config = parse_and_validate(config_path)
    config.calibration = __import__(
        "fieldtest.config", fromlist=["CalibrationConfig"]
    ).CalibrationConfig(panel=[
        {"provider": "anthropic", "model": "a"},
        {"provider": "openai", "model": "b"},
    ])

    # The precondition this test rests on: nothing has registered has_hello, so
    # both entry points must load rules.py themselves. Before the fix, calibrate
    # reached dispatch_judge with an empty registry and raised, because loading
    # lived in the score CLI command rather than in score().
    #
    # Cleared rather than assumed. The registry is process-global, so an earlier
    # test in the same session can satisfy the precondition by accident — which
    # is why this assertion was previously written `or True` and proved nothing.
    _rule_registry.pop("has_hello", None)
    _loaded_rule_files.clear()
    assert "has_hello" not in _rule_registry

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


def test_scoring_an_empty_set_is_refused(tmp_path):
    """
    An empty set used to write a complete five-file result set reporting zero
    evaluations — and that run then qualified as a baseline for a real one.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.errors import OutputError
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, fixtures=())
    results = config_path.parent / "results"

    with pytest.raises(OutputError, match="No fixtures resolved"):
        score(config=parse_and_validate(config_path), config_path=config_path)

    assert list(results.iterdir()) == [], "a refused run must write nothing"


def test_rag_grounding_eval_can_reach_its_context(tmp_path):
    """
    Spec 13's reason for existing. A grounding eval asks whether every claim
    traces to the retrieved context; before this the judge never received the
    context and answered anyway, so a rate that looked judged was guessed.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    grounding_eval = (
        "      - id: no_hallucination\n"
        "        tag: safe\n"
        "        type: llm\n"
        "        description: no fabricated details beyond the source\n"
        "        pass_criteria: every detail can be found in the context\n"
        "        fail_criteria: any detail appears invented\n"
    )
    config_path = _project(tmp_path, evals_yaml=grounding_eval, runs=1)

    # Give the fixture a context the judge must be able to read.
    fixture = config_path.parent / "fixtures" / "fix1.yaml"
    fixture.write_text(
        "id: fix1\n"
        "inputs:\n"
        "  question: what is the limit?\n"
        "  context: |\n"
        "    Expenses under $75 need no approval.\n"
    )

    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        score(config=parse_and_validate(config_path), config_path=config_path,
              write_artifacts=False)

    prompt = adapter.calls[0]["prompt"]
    assert "Expenses under $75 need no approval." in prompt, (
        "the judge was asked about grounding without being given the source"
    )
    assert "question: what is the limit?" in prompt
    assert prompt.index("System input:") < prompt.index("Output to evaluate:")


# ---------------------------------------------------------------------------
# User-registered providers (spec 11)
#
# No adapter is patched here. The provider comes from a providers.py on disk,
# reached through parse_and_validate and score() the way a user's would be.
# ---------------------------------------------------------------------------

REGISTERED_PROVIDER = """\
from fieldtest import provider

@provider("my-inference-service")
def call(model, prompt, gen, retry):
    return {"answer": "Pass", "reasoning": f"judged by {model} at temp {gen.temperature}"}
"""


def test_user_registered_provider_used_by_score(tmp_path):
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(
        tmp_path,
        evals_yaml=LLM_EVAL,
        runs=2,
        defaults_extra="  provider: my-inference-service\n  model: local-7b\n",
        providers_py=REGISTERED_PROVIDER,
    )
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, write_artifacts=False)

    rows = [r for r in rows if r.eval_id == "is_helpful"]
    assert len(rows) == 2
    assert all(r.passed for r in rows)
    # Proves the user's function ran, not a fallback: only it writes this text,
    # and it echoes the generation config it was handed.
    assert all("judged by local-7b at temp 0.0" in r.detail for r in rows)


def test_registered_provider_appears_in_the_run_provenance(tmp_path):
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(
        tmp_path,
        evals_yaml=LLM_EVAL,
        runs=1,
        defaults_extra="  provider: my-inference-service\n  model: local-7b\n",
        providers_py=REGISTERED_PROVIDER,
    )
    config = parse_and_validate(config_path)
    score(config=config, config_path=config_path, write_artifacts=False)

    from fieldtest.results.provenance import build_judge_block
    judge = build_judge_block(config)
    assert judge["provider"] == "my-inference-service"
    assert judge["model"] == "local-7b"


def test_registered_provider_that_raises_produces_an_error_row_not_a_crash(tmp_path):
    """
    A user's adapter that raises instead of returning {"error": ...} costs one
    errored row. This call is inside a ThreadPoolExecutor, so an exception
    reaching future.result() would take every other eval down with it.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(
        tmp_path,
        evals_yaml=LLM_EVAL,
        runs=2,
        defaults_extra="  provider: exploding-service\n  model: boom\n",
        providers_py=(
            "from fieldtest import provider\n\n"
            '@provider("exploding-service")\n'
            "def call(model, prompt, gen, retry):\n"
            "    raise RuntimeError('user adapter blew up')\n"
        ),
    )
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, write_artifacts=False)

    rows = [r for r in rows if r.eval_id == "is_helpful"]
    assert len(rows) == 2
    assert all(r.error for r in rows)
    assert all("user adapter blew up" in r.error for r in rows)


# ---------------------------------------------------------------------------
# A real OpenAI-protocol endpoint on a loopback socket (spec 11 §5)
#
# Spec 11's behavioural acceptance asks for a model served by vLLM or Ollama,
# a config pointed at it, and a calibration panel mixing it with a hosted judge.
# This runs that shape against a server on 127.0.0.1 rather than a GPU: real
# socket, real openai SDK, real base_url routing, real score() and calibrate().
#
# What it cannot establish is whether a 7B model's judgment is any good, which
# is what `fieldtest calibrate` exists to answer and not a property of this
# code. Everything between config.yaml and the HTTP request is covered here.
#
# Hermetic and free — loopback only, no credentials, no external network — so
# it stays in this tier rather than the opt-in live one.
# ---------------------------------------------------------------------------

class _FakeEndpoint:
    """An OpenAI chat-completions server. Records what it was actually sent."""

    def __init__(self, verdict: str = "Pass", reject: str | None = None):
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep pytest output clean
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                payload = _json.loads(body)
                outer.requests.append({"path": self.path, **payload})

                if reject and reject in payload:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(_json.dumps({"error": {
                        "message": f"Unsupported parameter: '{reject}'",
                        "type": "invalid_request_error", "param": reject,
                    }}).encode())
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json.dumps({
                    "id": "x", "object": "chat.completion", "created": 0,
                    "model": payload.get("model", "?"),
                    "choices": [{
                        "index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content":
                                    f'{{"answer": "{verdict}", '
                                    f'"reasoning": "served locally"}}'},
                    }],
                }).encode())

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def test_a_config_can_score_against_a_local_openai_protocol_endpoint(tmp_path):
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    with _FakeEndpoint(verdict="Pass") as endpoint:
        config_path = _project(
            tmp_path,
            evals_yaml=LLM_EVAL,
            runs=2,
            defaults_extra=(
                "  provider: openai_compatible\n"
                "  model: meta-llama/llama-3.3-70b-instruct\n"
            ),
        )
        config_path.write_text(
            config_path.read_text()
            + "providers:\n"
              "  openai_compatible:\n"
              f"    base_url: {endpoint.base_url}\n"
        )
        config = parse_and_validate(config_path)
        _, rows = score(config=config, config_path=config_path, write_artifacts=False)

    llm_rows = [r for r in rows if r.eval_id == "is_helpful"]
    assert len(llm_rows) == 2
    assert all(r.passed for r in llm_rows), [r.error for r in llm_rows]
    assert all("served locally" in r.detail for r in llm_rows)

    # The endpoint received what the config asked for, over a real connection.
    assert len(endpoint.requests) == 2
    assert endpoint.requests[0]["path"] == "/v1/chat/completions"
    assert endpoint.requests[0]["model"] == "meta-llama/llama-3.3-70b-instruct"
    assert endpoint.requests[0]["temperature"] == 0.0


def test_a_local_endpoint_rejecting_a_parameter_is_reported_not_fatal(tmp_path):
    """
    The drop path against a server that actually returns 400, rather than a
    mock raising on cue. This is the path the OpenRouter caveat says did not
    fire there.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.judges.llm import _unsupported_params
    from fieldtest.runner import score

    _unsupported_params.clear()
    with _FakeEndpoint(verdict="Pass", reject="temperature") as endpoint:
        config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=1,
                               defaults_extra="  provider: openai_compatible\n  model: local-7b\n")
        config_path.write_text(
            config_path.read_text()
            + f"providers:\n  openai_compatible:\n    base_url: {endpoint.base_url}\n"
        )
        config = parse_and_validate(config_path)
        _, rows = score(config=config, config_path=config_path, write_artifacts=False)

    llm_rows = [r for r in rows if r.eval_id == "is_helpful"]
    assert all(r.passed for r in llm_rows), [r.error for r in llm_rows]
    # Retried without it, and said so rather than leaving the judge silently unpinned.
    assert any("temperature" in p for p in _unsupported_params)
    assert "temperature" not in endpoint.requests[-1]


def test_calibrate_runs_a_panel_mixing_a_local_endpoint_and_another_judge(tmp_path):
    """
    Spec 11's stated purpose: a local judge and a hosted judge disagreeing on
    an eval. Only the hosted half is faked here — the local half is served.
    """
    from unittest.mock import patch

    from fieldtest.calibrate import run_calibration
    from fieldtest.config import parse_and_validate

    class _AlwaysFails(ProviderAdapter):
        def call(self, model, prompt, gen, retry):
            return {"answer": "Fail", "reasoning": "hosted judge disagrees"}

    with _FakeEndpoint(verdict="Pass") as endpoint:
        config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=2,
                               defaults_extra="  provider: openai_compatible\n  model: local-7b\n")
        config_path.write_text(
            config_path.read_text()
            + f"providers:\n  openai_compatible:\n    base_url: {endpoint.base_url}\n"
            + "calibration:\n"
              "  panel:\n"
              "    - { provider: openai_compatible, model: local-7b }\n"
              "    - { provider: anthropic, model: claude-haiku-4-5 }\n"
        )
        config = parse_and_validate(config_path)

        from fieldtest.providers import get_provider_adapter as real_get

        def _route(provider, settings=None):
            # The local endpoint stays real; only the hosted judge is faked.
            if provider == "anthropic":
                return _AlwaysFails()
            return real_get(provider, settings)

        with patch("fieldtest.judges.llm.get_provider_adapter", side_effect=_route):
            _, data = run_calibration(config, config_path, "full")

    # Two judges, opposite verdicts on every row: agreement is zero.
    stats = data["evals"]["is_helpful"]
    assert stats["judges_participating"] == 2
    assert stats["mean_agreement"] == 0.0
    assert endpoint.requests, "the local endpoint was never called"


# ---------------------------------------------------------------------------
# file: inputs (spec 14 §3)
#
# The defect these fix: inputs reach the judge through str(value), so a fixture
# written the way README §3 showed sent the judge a 25-character path instead of
# the document. Spec 13's defect, arriving through documentation.
# ---------------------------------------------------------------------------

HANDBOOK = "Employees may expense meals up to $75 without prior approval."


def _project_with_file_input(tmp_path: Path, value: str) -> Path:
    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=1)
    evals_dir = config_path.parent
    (evals_dir / "sources").mkdir(exist_ok=True)
    (evals_dir / "sources" / "handbook.md").write_text(HANDBOOK)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(
        "id: fix1\ninputs:\n"
        f"  policy: \"{value}\"\n"
        "  question: What is the meal limit?\n"
    )
    return config_path


def test_a_file_input_reaches_the_judge_as_the_document(tmp_path):
    """
    Asserted on the prompt the judge was handed, not on the loader's return
    value. The loader returning a string was never the broken part.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project_with_file_input(tmp_path, "file:sources/handbook.md")
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        score(config=parse_and_validate(config_path), config_path=config_path,
              write_artifacts=False)

    prompt = adapter.calls[0]["prompt"]
    assert HANDBOOK in prompt
    assert "file:sources/handbook.md" not in prompt
    assert "sources/handbook.md" not in prompt


def test_a_plain_string_input_is_left_alone(tmp_path):
    """`see notes/faq.md` is a legitimate literal, not a file reference."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project_with_file_input(tmp_path, "see sources/handbook.md")
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        score(config=parse_and_validate(config_path), config_path=config_path,
              write_artifacts=False)

    prompt = adapter.calls[0]["prompt"]
    assert "see sources/handbook.md" in prompt
    assert HANDBOOK not in prompt


def test_a_rule_eval_gets_the_document_too(tmp_path):
    """
    Resolution happens at load, not at prompt-building, so a rule eval and an
    LLM eval are handed the same thing. A rule reading a path would be the same
    defect in a cheaper judge.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _project(tmp_path, evals_yaml=RULE_EVAL, runs=1, with_rules=True)
    evals_dir = config_path.parent
    (evals_dir / "sources").mkdir(exist_ok=True)
    (evals_dir / "sources" / "handbook.md").write_text(HANDBOOK)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(
        'id: fix1\ninputs:\n  policy: "file:sources/handbook.md"\n'
    )
    (evals_dir / "rules.py").write_text(
        "from fieldtest import rule\n\n"
        "@rule('has_hello')\n"
        "def check(output, inputs):\n"
        "    got = inputs.get('policy', '')\n"
        "    return {'passed': '$75' in got, 'detail': f'policy is {len(got)} chars'}\n"
    )
    _, rows = score(config=parse_and_validate(config_path), config_path=config_path,
                    write_artifacts=False)
    row = [r for r in rows if r.eval_id == "has_hello"][0]
    assert row.passed, row.detail
    assert f"{len(HANDBOOK)} chars" in row.detail


def test_a_missing_file_input_fails_validation_not_the_judge(tmp_path):
    """Twenty errored rows into a run is the wrong place to learn this."""
    from fieldtest.config import parse_and_validate, summarize_file_inputs

    from fieldtest.config import load_fixture, validate_fixture_labels
    from fieldtest.errors import ConfigError

    config_path = _project_with_file_input(tmp_path, "file:sources/nope.md")
    config = parse_and_validate(config_path)
    base_dir = config_path.parent

    errors, _ = validate_fixture_labels(config, base_dir)
    assert any("nope.md" in e for e in errors), errors

    # And the loader refuses rather than handing anyone the path.
    with pytest.raises(ConfigError) as exc:
        load_fixture(base_dir / "fixtures" / "fix1.yaml", base_dir)
    assert "nope.md" in str(exc.value)
    assert "inputs.policy" in str(exc.value)

    # summarize_file_inputs is a reporting helper and skips what it cannot read,
    # so a broken reference shows up as an error above, not as a false report.
    assert summarize_file_inputs(config, base_dir) == {}


def test_validate_reports_resolved_file_inputs(tmp_path):
    from fieldtest.config import parse_and_validate, summarize_file_inputs

    config_path = _project_with_file_input(tmp_path, "file:sources/handbook.md")
    config = parse_and_validate(config_path)
    assert summarize_file_inputs(config, config_path.parent) == {"fix1": ["policy"]}


# ---------------------------------------------------------------------------
# The bundled datasets (spec 14 §5)
#
# Acceptance is behavioural: the shipped scaffold must produce real failures
# with no API key, and the answer key must catch every planted defect. A test
# that only checks the files exist would pass on a dataset that scores nothing.
# ---------------------------------------------------------------------------

DATASET = "expense-report"
DATASETS = ["expense-report", "support-agent"]


def _dataset_dir(name: str = DATASET):
    import fieldtest
    return Path(fieldtest.__file__).resolve().parent / "datasets" / name


def test_the_shipped_scaffold_scores_with_no_api_key(tmp_path, monkeypatch):
    """The first run has to work before the user has a key or has written
    anything, or the dataset cannot be explored at all."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _dataset_dir() / "config.yaml"
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    assert not any(r.error for r in rows), [r.error for r in rows if r.error]
    failures = [r for r in rows if r.passed is False]
    assert failures, "a dataset whose scaffold finds nothing teaches nothing"
    # Not all failing either — a user should see the report distinguish them.
    assert any(r.passed for r in rows)


def test_every_planted_defect_is_caught_by_a_deterministic_eval(tmp_path):
    """
    The three defects the README claims are findable without an API key.
    Asserted by fixture and run, so moving a defect without updating the README
    fails here.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _dataset_dir() / "config.yaml"
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    failed = {(r.fixture_id, r.run, r.eval_id) for r in rows if r.passed is False}
    assert ("october-trip", 3, "total_matches_line_items") in failed
    assert ("march-trip", 2, "no_unfilled_placeholders") in failed
    assert ("june-trip", 2, "excluded_categories_not_reimbursed") in failed


def test_the_clean_outputs_actually_pass(tmp_path):
    """Every planted defect is deliberate, so run 1 of each trip is clean."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    config_path = _dataset_dir() / "config.yaml"
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    for trip in ("october-trip", "march-trip", "june-trip"):
        clean = [r for r in rows if r.fixture_id == trip and r.run == 1
                 and r.passed is False]
        assert not clean, [(r.eval_id, r.detail) for r in clean]


def test_the_answer_key_covers_every_judge_type():
    """
    Five types exist and the dataset is meant to teach the mechanics of each.
    Four of five would leave one type undemonstrated.
    """
    from fieldtest.config import parse_and_validate

    config = parse_and_validate(_dataset_dir() / "reference-evals.yaml")
    evals = [ev for uc in config.use_cases for ev in uc.evals]
    kinds = {ev.type for ev in evals}
    assert kinds == {"rule", "regex", "llm", "reference"}
    assert any(ev.type == "llm" and ev.binary for ev in evals), "no binary llm eval"
    assert any(ev.type == "llm" and not ev.binary for ev in evals), "no scored llm eval"


def test_the_scaffold_leaves_work_to_do():
    """
    A scaffold with no TODO is a fourth demo. Checked on the file, because the
    TODOs are commented-out YAML and invisible to the parser.
    """
    text = (_dataset_dir() / "config.yaml").read_text()
    assert text.count("# TODO") >= 3
    assert "TODO" in (_dataset_dir() / "rules.py").read_text()


def test_dataset_use_copies_a_runnable_project(tmp_path):
    from click.testing import CliRunner

    from fieldtest.cli import main

    dest = tmp_path / "evals"
    result = CliRunner().invoke(
        main, ["dataset", "use", DATASET, "--dest", str(dest)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert (dest / "config.yaml").is_file()
    assert (dest / "outputs" / "october-trip" / "run-1.txt").is_file()
    # Results belong to whoever runs it, not to the shipped copy.
    assert not (dest / "results").exists()


def test_dataset_use_refuses_to_overwrite_existing_work(tmp_path):
    """Copying over someone's evals is not recoverable."""
    from click.testing import CliRunner

    from fieldtest.cli import main

    dest = tmp_path / "evals"
    dest.mkdir()
    (dest / "config.yaml").write_text("mine: do not clobber\n")
    result = CliRunner().invoke(
        main, ["dataset", "use", DATASET, "--dest", str(dest)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert (dest / "config.yaml").read_text() == "mine: do not clobber\n"


def test_dataset_use_rejects_an_unknown_name(tmp_path):
    from click.testing import CliRunner

    from fieldtest.cli import main

    result = CliRunner().invoke(
        main, ["dataset", "use", "no-such-dataset", "--dest", str(tmp_path / "e")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "Unknown dataset" in result.output
    assert DATASET in result.output


@pytest.mark.parametrize("name", DATASETS)
def test_every_dataset_scores_offline_and_finds_something(name, monkeypatch):
    """
    Applied to every bundled dataset, so a new one cannot be added without
    meeting the bar the first one set.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _dataset_dir(name) / "config.yaml"
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    assert not any(r.error for r in rows), [r.error for r in rows if r.error]
    assert any(r.passed is False for r in rows), f"{name} finds nothing"
    assert any(r.passed for r in rows), f"{name} fails everything"


@pytest.mark.parametrize("name", DATASETS)
def test_every_dataset_validates_without_warnings(name):
    """
    A label naming an eval the scaffold does not declare warns on every run.
    That shipped once; this stops it shipping again.
    """
    from fieldtest.config import parse_and_validate, validate_fixture_labels

    config_path = _dataset_dir(name) / "config.yaml"
    config = parse_and_validate(config_path)
    errors, coverage = validate_fixture_labels(config, config_path.parent)
    assert errors == [], errors
    assert coverage, f"{name} ships no human labels"


@pytest.mark.parametrize("name", DATASETS)
def test_every_dataset_labels_agree_with_its_deterministic_evals(name, monkeypatch):
    """
    The shipped labels are ground truth for evals that cannot be wrong. If they
    disagree, the labels are wrong — and a dataset teaching judge-vs-human
    agreement cannot ship labels that are themselves incorrect.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _dataset_dir(name) / "config.yaml"
    config = parse_and_validate(config_path)
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    from fieldtest.config import extract_labels, load_fixture

    base_dir = config_path.parent
    truth = {}
    for uc in config.use_cases:
        for fp in sorted((base_dir / uc.fixtures.directory).glob("*.yaml")):
            fixture = load_fixture(fp, base_dir)
            for (eval_id, run), verdict in extract_labels(fixture).items():
                truth[(fixture["id"], eval_id, run)] = verdict

    assert truth, f"{name} ships no labels to check"

    disagreements = [
        (r.fixture_id, r.run, r.eval_id, "judge=pass" if r.passed else "judge=fail",
         f"human={truth[(r.fixture_id, r.eval_id, r.run)]}")
        for r in rows
        if r.passed is not None
        and (r.fixture_id, r.eval_id, r.run) in truth
        and (truth[(r.fixture_id, r.eval_id, r.run)] == "pass") != r.passed
    ]
    assert not disagreements, disagreements


@pytest.mark.parametrize("name", DATASETS)
def test_every_dataset_answer_key_covers_all_five_judge_types(name):
    from fieldtest.config import parse_and_validate

    config = parse_and_validate(_dataset_dir(name) / "reference-evals.yaml")
    evals = [ev for uc in config.use_cases for ev in uc.evals]
    assert {ev.type for ev in evals} == {"rule", "regex", "llm", "reference"}
    assert any(ev.type == "llm" and ev.binary for ev in evals)
    assert any(ev.type == "llm" and not ev.binary for ev in evals)


@pytest.mark.parametrize("name", DATASETS)
def test_every_dataset_leaves_work_to_do(name):
    d = _dataset_dir(name)
    assert "TODO" in (d / "config.yaml").read_text()
    assert "TODO" in (d / "rules.py").read_text()
    assert (d / "README.md").is_file()
    assert (d / "PROMPT.md").is_file()


def test_the_agent_dataset_scores_json_traces():
    """
    An output is text. A trace is JSON in that text, and a rule eval parses it —
    no code in fieldtest knows the difference, which is the claim this pins.
    """
    import json

    for p in sorted((_dataset_dir("support-agent") / "outputs").rglob("run-*.txt")):
        trace = json.loads(p.read_text())
        assert trace["steps"], p


@pytest.mark.parametrize("name", DATASETS)
def test_uncommenting_the_answer_key_labels_keeps_the_others(name):
    """
    The README tells the reader to uncomment labels for the answer-key eval.
    Those lines shipped once as a second top-level `labels:` key, which YAML
    resolves by keeping the last one — so following the instruction silently
    deleted every label above it, with no error.

    Simulates the edit rather than trusting the indentation by eye.
    """
    import re

    import yaml

    for path in sorted((_dataset_dir(name) / "fixtures").glob("*.yaml")):
        text = path.read_text()
        before = yaml.safe_load(text).get("labels", {})

        # The dangerous shape directly: a commented-out top-level `labels:`.
        # Uncommenting it makes a second top-level key, and YAML keeps the last.
        assert not any(
            re.match(r"^#\s*labels:", ln) for ln in text.splitlines()
        ), (
            f"{path.name}: a commented-out top-level 'labels:' key. Uncommenting "
            f"it would replace the labels above it instead of adding to them — "
            f"indent the entries under the existing key."
        )

        if not any(re.match(r"^  # \w+:", ln) for ln in text.splitlines()):
            continue  # this fixture has no commented labels

        uncommented = "\n".join(
            ln.replace("  # ", "  ", 1)
            if re.match(r"^  # (\w+:|  \d+: (pass|fail))", ln) else ln
            for ln in text.splitlines()
        )
        after = yaml.safe_load(uncommented).get("labels", {})

        assert set(before) <= set(after), (
            f"{path.name}: uncommenting dropped {set(before) - set(after)}"
        )
        assert len(after) > len(before), f"{path.name}: uncommenting added nothing"


# ---------------------------------------------------------------------------
# The walkthrough (docs/walkthrough.md)
#
# It claims every command and every block of output in it is real. That claim
# rots the first time a message changes, so it is executed rather than trusted:
# the rule the doc tells a reader to paste is extracted from the doc itself and
# run, and the figures it quotes are asserted against a real run.
# ---------------------------------------------------------------------------

def _walkthrough() -> str:
    return (Path(__file__).resolve().parent.parent / "docs" / "walkthrough.md").read_text()


def _score_dataset_copy(tmp_path, monkeypatch, mutate=None):
    """Copy expense-report, optionally edit it, score it offline."""
    import shutil

    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    dest = tmp_path / "evals"
    shutil.copytree(_dataset_dir("expense-report"), dest,
                    ignore=shutil.ignore_patterns("results", "__pycache__"))
    if mutate:
        mutate(dest)

    # A fresh registry: rules.py here is a different file with the same name.
    from fieldtest.judges.registry import _loaded_rule_files, _rule_registry
    _rule_registry.clear()
    _loaded_rule_files.clear()

    config_path = dest / "config.yaml"
    return score(config=parse_and_validate(config_path), config_path=config_path,
                 set_name="full", write_artifacts=False)[1]


def test_walkthrough_step_4_output_is_what_the_command_prints(tmp_path, monkeypatch):
    """The failure lines quoted in step 4, asserted against a real run."""
    rows = _score_dataset_copy(tmp_path, monkeypatch)
    doc = _walkthrough()

    failures = {(r.fixture_id, r.run, r.eval_id): r.detail
                for r in rows if r.passed is False}

    assert ("june-trip", 2, "excluded_categories_not_reimbursed") in failures
    assert ("october-trip", 3, "total_matches_line_items") in failures
    # The quoted details must appear verbatim in the doc.
    assert "R-1190 (alcohol) reimbursed $47.00" in doc
    assert failures[("june-trip", 2, "excluded_categories_not_reimbursed")] == \
        "R-1190 (alcohol) reimbursed $47.00"
    assert "line items sum to $897.70, output states $912.70" in doc
    assert failures[("october-trip", 3, "total_matches_line_items")] == \
        "line items sum to $897.70, output states $912.70"

    # 9/12 RIGHT, quoted in the Tag Health table.
    right = [r for r in rows if r.tag == "right" and r.passed is not None]
    assert (sum(1 for r in right if r.passed), len(right)) == (9, 12)
    assert "| RIGHT | 75% | 9 / 12 |" in doc


def test_walkthrough_step_6_rule_is_runnable_and_catches_r1049(tmp_path, monkeypatch):
    """
    The rule the doc tells a reader to paste, extracted from the doc and run.
    If the snippet stops working, this fails rather than the reader.
    """
    import re

    doc = _walkthrough()
    match = re.search(r"```python\n(@rule\(\"no_invented_receipts\"\).*?)```", doc, re.S)
    assert match, "the walkthrough no longer contains the rule it tells you to write"
    snippet = match.group(1)

    def mutate(dest: Path):
        (dest / "rules.py").write_text((dest / "rules.py").read_text() + "\n\n" + snippet)
        cfg = dest / "config.yaml"
        yaml_block = re.search(
            r"```yaml\n(      - id: no_invented_receipts\n.*?)```", doc, re.S
        )
        assert yaml_block, "the walkthrough no longer contains the config block"
        s = cfg.read_text()
        todo = "      # TODO one of the outputs cites a receipt that exists in no source file."
        assert todo in s, "the TODO the walkthrough quotes is gone from config.yaml"
        cfg.write_text(s.replace(todo, yaml_block.group(1).rstrip() + "\n\n" + todo, 1))

    rows = _score_dataset_copy(tmp_path, monkeypatch, mutate)

    new = [r for r in rows if r.eval_id == "no_invented_receipts"]
    assert len(new) == 9, "the eval did not run on every output"
    failed = [r for r in new if r.passed is False]
    assert [(r.fixture_id, r.run) for r in failed] == [("october-trip", 3)]
    assert failed[0].detail == "cites R-1049, which is in no source receipt"
    assert "cites R-1049, which is in no source receipt" in doc

    # 17/21 RIGHT after adding it, quoted in step 7.
    right = [r for r in rows if r.tag == "right" and r.passed is not None]
    assert (sum(1 for r in right if r.passed), len(right)) == (17, 21)
    assert "| RIGHT | 81% | 17 / 21 |" in doc


def test_walkthrough_quotes_the_real_receipt_ids():
    """Step 5's argument rests on R-1049 being absent from the source."""
    csv_text = (_dataset_dir("expense-report") / "sources" / "receipts-october.csv").read_text()
    ids = [ln.split(",")[0] for ln in csv_text.splitlines()[1:] if ln.strip()]
    assert ids == ["R-1041", "R-1042", "R-1043", "R-1044", "R-1045", "R-1046"]
    assert "R-1049" not in csv_text
    assert ", ".join(ids).replace(", ", ",") in _walkthrough().replace(" ", "")


def test_walkthrough_fault_counts_are_arithmetic_not_prose(tmp_path, monkeypatch):
    """
    The doc says the shipped evals flag three of nine outputs and leave three
    faulty ones unflagged. I first wrote "two", from memory rather than from a
    run. Counted here so the sentence cannot drift from the dataset.
    """
    rows = _score_dataset_copy(tmp_path, monkeypatch)
    doc = _walkthrough()

    flagged = {(r.fixture_id, r.run) for r in rows if r.passed is False}
    faulty = {("october-trip", 2), ("october-trip", 3), ("march-trip", 2),
              ("march-trip", 3), ("june-trip", 2), ("june-trip", 3)}

    assert len(flagged) == 3, flagged
    assert len(faulty - flagged) == 3, faulty - flagged
    assert sum(1 for r in rows if r.passed is False) == 5

    assert "five failures, across three of the nine outputs" in doc
    assert "The other three\nfaulty outputs went unflagged" in doc

    # The three the doc names as needing a judge are exactly the unflagged ones.
    for fixture, run in sorted(faulty - flagged):
        assert f"{fixture}/run-{run}" in doc, f"{fixture}/run-{run} not named in step 9"


def test_walkthrough_file_tree_lists_what_dataset_use_copies(tmp_path):
    """
    Step 2 tells the reader to open evals/README.md; step 3's listing omitted
    it. A tree that quietly disagrees with the directory is worse than none.
    """
    from click.testing import CliRunner

    from fieldtest.cli import main

    dest = tmp_path / "evals"
    CliRunner().invoke(main, ["dataset", "use", "expense-report", "--dest", str(dest)],
                       catch_exceptions=False)
    doc = _walkthrough()
    tree = doc[doc.index("evals/\n"):doc.index("**A fixture**")]

    for entry in sorted(p.name for p in dest.iterdir()):
        assert entry in tree, f"{entry} is copied but missing from the walkthrough tree"


def test_site_dataset_figures_match_a_real_run(tmp_path, monkeypatch):
    """
    docs/index.html quotes the dataset's report. Those figures were pasted from
    a run and will rot silently; the site is the one place nobody re-runs.
    """
    rows = _score_dataset_copy(tmp_path, monkeypatch)
    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()

    right = [r for r in rows if r.tag == "right" and r.passed is not None]
    good  = [r for r in rows if r.tag == "good" and r.passed is not None]
    assert f"{sum(1 for r in right if r.passed)} / {len(right)}" in site
    assert f"{sum(1 for r in good if r.passed)} / {len(good)}" in site

    details = {r.detail for r in rows if r.passed is False}
    for quoted in ("R-1190 (alcohol) reimbursed $47.00",
                   "line items sum to $897.70, output states $912.70"):
        assert quoted in details, f"{quoted!r} is on the site but not in a real run"
        assert quoted in site


def test_site_output_comparison_matches_the_real_outputs():
    """
    The side-by-side panels claim run 1 reimburses R-1045 at $75.00 and run 2 at
    $91.40, with totals of $869.70 and $886.10. Read from the outputs.
    """
    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    out = _dataset_dir("expense-report") / "outputs" / "october-trip"

    run1, run2 = (out / "run-1.txt").read_text(), (out / "run-2.txt").read_text()
    assert "$75.00" in run1 and "Total reimbursable: $869.70" in run1
    assert "$91.40 | $91.40" in run2 and "Total reimbursable: $886.10" in run2
    for figure in ("869.70", "886.10", "91.40", "75.00"):
        assert figure in site


def test_site_uses_no_unstyled_class_names():
    """
    I shipped .code-window / .cw-bar with no CSS, so two panels rendered as bare
    <pre>. Every class used in the markup must be defined in the stylesheet.
    """
    import re

    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    styles = site[site.index("<style>"):site.index("</style>")]
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", styles))

    used: set[str] = set()
    for attr in re.findall(r'class="([^"]+)"', site):
        used.update(attr.split())

    # Utility names that are legitimately styled only via a parent selector.
    allowed = {"red", "yellow", "green"}
    undefined = sorted(used - defined - allowed)
    assert not undefined, f"classes used but never styled: {undefined}"


def test_docs_quote_the_real_prompt():
    """
    The site and the walkthrough both reproduce PROMPT.md. Editing the prompt
    left both quoting a version that no longer existed — the prompt is an input
    the judge reads, so a stale copy misdescribes what the evals are scoring.

    Abridgement is fine; invention is not. Every non-empty line quoted must
    appear in the real file.
    """
    root = Path(__file__).resolve().parent.parent
    prompt = (_dataset_dir("expense-report") / "PROMPT.md").read_text()
    prompt_lines = {ln.strip() for ln in prompt.splitlines() if ln.strip()}

    quoting = [
        doc for doc in ("docs/walkthrough.md", "docs/index.html")
        if "You are an expense assistant" in (root / doc).read_text()
    ]
    assert quoting, "no doc reproduces PROMPT.md — did the site section move?"

    for doc in quoting:
        text = (root / doc).read_text()
        import re

        start = text.index("You are an expense assistant for Meridian Corp.")
        # To the end of the enclosing block, whichever syntax the doc uses.
        ends = [e for e in (text.find("</pre>", start), text.find("```", start)) if e != -1]
        block = text[start:min(ends)]
        quoted = [
            re.sub(r"<[^>]+>", "", ln).strip()
            for ln in block.splitlines() if ln.strip()
        ]
        missing = [ln for ln in quoted if ln and ln not in prompt_lines]
        assert not missing, f"{doc} quotes lines not in PROMPT.md: {missing}"

    # And the requirement the caps_applied eval depends on is actually stated.
    assert "Apply the limits and exclusions in the policy" in prompt


def test_bundled_demo_results_use_the_current_schema():
    """
    `fieldtest demo --offline` serves pre-scored JSON rather than re-running, so
    a renamed summary field leaves the demo showing a schema the code no longer
    emits. Compared against a real run's own keys rather than a hardcoded list.
    """
    import json

    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    root = Path(__file__).resolve().parent.parent / "fieldtest" / "demo"
    for demo in ("rag", "email", "extraction"):
        config_path = root / demo / "config.yaml"
        # A rule/regex-only pass is enough: the summary shape is the same.
        config = parse_and_validate(config_path)
        _, rows = score(config=config, config_path=config_path,
                        write_artifacts=False, allow_partial=True)

        bundled = json.loads((root / demo / "results" / "demo-offline-data.json").read_text())
        shipped_keys = {
            k
            for uc in bundled["summary"].values()
            for tag in uc.values()
            for stats in tag.values()
            for k in stats
        }
        assert "confidence" not in shipped_keys, (
            f"{demo}: bundled results still use the pre-0.3.0 'confidence' key"
        )
        assert "confidence_level" in shipped_keys, f"{demo}: no confidence_level in summary"


def test_two_threads_loading_rules_both_see_the_registry(tmp_path):
    """
    The calibration panel scores with each judge in its own thread, and each
    calls load_rules(). The memo recorded the path *before* executing the
    module, so the second thread saw it as loaded while the first was still
    running it and dispatched against an empty registry:

        No rule registered for eval 'total_matches_line_items'

    Reproduced deterministically: the module sleeps while executing, so a
    second thread is guaranteed to arrive mid-load.
    """
    import threading
    import time

    from fieldtest.judges.registry import (_loaded_rule_files, _rule_registry,
                                           get_rule, load_rules)

    rules = tmp_path / "rules.py"
    rules.write_text(
        "import time\n"
        "from fieldtest import rule\n\n"
        "time.sleep(0.3)\n\n"          # the window the race needs
        "@rule('slow_to_register')\n"
        "def check(output, inputs):\n"
        "    return {'passed': True, 'detail': 'ok'}\n"
    )

    _rule_registry.pop("slow_to_register", None)
    _loaded_rule_files.discard(str(rules.resolve()))

    seen: list = []

    def worker():
        load_rules(rules)
        seen.append(get_rule("slow_to_register"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
        time.sleep(0.05)   # stagger so the others arrive mid-load
    for t in threads:
        t.join()

    assert len(seen) == 4
    assert all(fn is not None for fn in seen), (
        "a thread returned from load_rules() before the registry was populated"
    )


def test_a_module_that_raises_is_not_recorded_as_loaded(tmp_path):
    """
    The path was recorded before execution, so a module that blew up counted as
    loaded and the next call silently skipped it. Retrying should raise again.
    """
    from fieldtest.errors import ConfigError
    from fieldtest.judges.registry import _loaded_rule_files, load_rules

    rules = tmp_path / "rules.py"
    rules.write_text("raise RuntimeError('boom')\n")
    _loaded_rule_files.discard(str(rules.resolve()))

    for _ in range(2):
        with pytest.raises(ConfigError) as exc:
            load_rules(rules)
        assert "boom" in str(exc.value)


@pytest.mark.parametrize("name,expected", [
    ("expense-report", {"rule": 18, "regex": 9, "reference": 3}),
    ("support-agent",  {"rule": 18, "regex": 9}),
])
def test_documented_label_agreement_is_what_the_data_says(name, expected, monkeypatch):
    """
    Both dataset READMEs quote how many labelled runs agree with the judge.
    expense-report's said "the rule evals agree on all thirty" — the rule evals
    account for 18 of those; the other 12 belong to a regex and a reference
    eval. A count that spans three eval types cannot be attributed to one.
    """
    import shutil
    import tempfile

    import yaml

    from fieldtest.config import parse_and_validate
    from fieldtest.judges.registry import _loaded_rule_files, _rule_registry
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    dest = Path(tempfile.mkdtemp()) / "evals"
    shutil.copytree(_dataset_dir(name), dest,
                    ignore=shutil.ignore_patterns("results", "__pycache__"))
    _rule_registry.clear()
    _loaded_rule_files.clear()

    config_path = dest / "config.yaml"
    config = parse_and_validate(config_path)
    types = {ev.id: ev.type for uc in config.use_cases for ev in uc.evals}
    _, rows = score(config=config, config_path=config_path, set_name="full",
                    write_artifacts=False)

    truth = {}
    for f in sorted((dest / "fixtures").glob("*.yaml")):
        fixture = yaml.safe_load(f.read_text())
        for eval_id, runs in (fixture.get("labels") or {}).items():
            for run, verdict in runs.items():
                truth[(fixture["id"], eval_id, run)] = verdict

    counts, agree = {}, {}
    for r in rows:
        key = (r.fixture_id, r.eval_id, r.run)
        if key not in truth or r.passed is None:
            continue
        t = types[r.eval_id]
        counts[t] = counts.get(t, 0) + 1
        agree[t] = agree.get(t, 0) + ((truth[key] == "pass") == r.passed)

    assert counts == expected, f"{name}: labelled runs per eval type changed"
    for t in counts:
        assert agree[t] == counts[t], (
            f"{name}: {t} evals disagree with the shipped labels "
            f"({agree[t]}/{counts[t]}) — the READMEs claim 100%"
        )

    readme = (_dataset_dir(name) / "README.md").read_text()
    total = sum(counts.values())
    assert str(total) in readme or _spelled(total) in readme, (
        f"{name}: README does not state its {total} labelled runs"
    )


def _spelled(n: int) -> str:
    return {27: "twenty-seven", 30: "thirty"}.get(n, str(n))


def test_report_header_names_outputs_and_judge_repeats_separately(tmp_path, monkeypatch):
    """
    `runs` are generator outputs; `judge_runs` are repeat verdicts on each. The
    header multiplied only the first, so a run with judge_runs: 3 said "3
    evaluations per eval" while making nine judge calls.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=3, judge_runs=3)
    config = parse_and_validate(config_path)
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, rows = score(config=config, config_path=config_path,
                             write_artifacts=False)

    # Nine judge calls for three outputs.
    assert len(adapter.calls) == 9
    assert len({(r.fixture_id, r.run) for r in rows if r.type == "llm"}) == 3
    assert {r.judge_run for r in rows if r.type == "llm"} == {1, 2, 3}

    md = format_report(rows, build_summary(rows, config), {}, config, run_id, "full")
    header = md.splitlines()[1]
    assert "3 scored output(s) per eval" in header
    assert "judged 3× each" in header
    assert "Judge Repeatability (judge_runs: 3)" in md


def test_report_header_stays_quiet_when_each_output_is_judged_once(tmp_path, monkeypatch):
    """The common case should not grow a clause about a setting nobody used."""
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=3)
    config = parse_and_validate(config_path)
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, rows = score(config=config, config_path=config_path,
                             write_artifacts=False)

    md = format_report(rows, build_summary(rows, config), {}, config, run_id, "full")
    header = md.splitlines()[1]
    assert "3 scored output(s) per eval" in header
    assert "judged" not in header


def test_a_rate_counts_outputs_not_judge_calls(tmp_path, monkeypatch):
    """
    With judge_runs: 3, failure_rate must stay per-output — majority across the
    repetitions — so rates are comparable across judge_runs settings. A rate
    over nine rows instead of three would move when you changed the setting.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import build_summary
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=3, judge_runs=3)
    config = parse_and_validate(config_path)
    # Two of three outputs fail, every judge repetition agreeing.
    adapter = RecordingAdapter(verdicts=[True, True, True, False, False, False,
                                         False, False, False])
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        _, rows = score(config=config, config_path=config_path, write_artifacts=False)

    summary = build_summary(rows, config)
    stats = summary["uc1"]["right"]["is_helpful"]
    assert stats["total_runs"] == 3, "total_runs must count outputs"
    assert stats["judge_calls"] == 9, "judge_calls must count judge invocations"
    assert stats["failure_rate"] == round(2 / 3, 6)


def _html_for(tmp_path, monkeypatch, *, judge_runs=1, labels=""):
    """Score a project and return the generated HTML report."""
    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=3,
                           judge_runs=judge_runs, labels=labels)
    config = parse_and_validate(config_path)
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        score(config=config, config_path=config_path)

    results = list((config_path.parent / "results").glob("*-report.html"))
    assert results, "no HTML report written"
    return results[0].read_text()


def test_html_report_names_the_judge_that_produced_its_numbers(tmp_path, monkeypatch):
    """
    The markdown report has named the judge since spec 01. The HTML — the file
    `fieldtest view` opens, and the one the site calls "everything in one file"
    — did not, while embedding the whole judge block in its data.
    """
    html = _html_for(tmp_path, monkeypatch)
    assert "Judge:" in html
    assert "claude-haiku-4-5" in html
    assert "temp 0.0" in html


def test_html_report_shows_judge_repeatability(tmp_path, monkeypatch):
    """judge_runs > 1 produced a markdown section and nothing in the HTML."""
    html = _html_for(tmp_path, monkeypatch, judge_runs=3)
    assert "Judge repeatability" in html
    assert "judge disagreement" in html
    assert "judged 3× each" in html


def test_html_report_shows_agreement_with_your_labels(tmp_path, monkeypatch):
    """Same for spec 07's labels: markdown had the table, HTML had nothing."""
    html = _html_for(
        tmp_path, monkeypatch,
        labels="labels:\n  is_helpful:\n    1: pass\n    2: fail\n    3: pass\n",
    )
    assert "Judge vs your labels" in html
    assert "false pass" in html
    assert "labelled runs" in html


def test_html_report_omits_the_judge_tables_when_there_is_nothing_to_say(tmp_path, monkeypatch):
    """A run with one judge pass and no labels should not grow empty tables."""
    html = _html_for(tmp_path, monkeypatch)
    assert "Judge vs your labels" not in html
    assert "Judge repeatability" not in html


def test_repeatability_lists_only_evals_a_judge_actually_repeated(tmp_path, monkeypatch):
    """
    judge_runs applies to llm evals; a rule or regex eval is evaluated once
    however high the setting goes. The summary reported the configured value on
    every eval, so both reports listed rule evals in the repeatability table at
    "0.0% disagreement" — implying a judge had been consulted twice and agreed,
    when none was consulted at all.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.html import _build_judge_tables
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL + RULE_EVAL,
                           runs=2, judge_runs=3, with_rules=True)
    config = parse_and_validate(config_path)
    adapter = RecordingAdapter()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=adapter):
        run_id, rows = score(config=config, config_path=config_path,
                             write_artifacts=False)

    # The rule eval really was evaluated once per output.
    assert {r.judge_run for r in rows if r.type == "rule"} == {1}
    assert {r.judge_run for r in rows if r.type == "llm"} == {1, 2, 3}

    summary = build_summary(rows, config)
    stats = {eid: st for tag in summary["uc1"].values() for eid, st in tag.items()}
    # Absent rather than 1: a rule eval has no judge, so there is no repetition
    # count to report, and the repeatability table keys off this field.
    assert "judge_runs" not in stats["has_hello"]
    assert stats["is_helpful"]["judge_runs"] == 3

    md = format_report(rows, summary, {}, config, run_id, "full")
    repeat_block = md[md.index("Judge Repeatability"):]
    repeat_block = repeat_block[:repeat_block.index("\n\n")]
    assert "is_helpful" in repeat_block
    assert "has_hello" not in repeat_block

    html = _build_judge_tables(summary["uc1"])
    assert "is_helpful" in html
    assert "has_hello" not in html


def test_every_artifact_carries_the_judge_repetition_data(tmp_path, monkeypatch):
    """
    judge_runs must be visible in all four things a run writes, not just the
    markdown. The HTML table shipped with only the disagreement column, so a
    scored eval — where the two spreads are the entire point — rendered as an
    empty row.
    """
    import csv
    import json

    from fieldtest.config import parse_and_validate
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    scored_eval = (
        "      - id: clarity\n"
        "        tag: good\n"
        "        type: llm\n"
        "        binary: false\n"
        "        description: how clear it is\n"
        "        scale: [1, 5]\n"
        "        anchors:\n"
        "          1: unclear\n"
        "          5: clear\n"
    )
    # Only the scored eval: RecordingAdapter serves its score queue to every
    # call, so a binary eval alongside it would eat the scores.
    config_path = _project(tmp_path, evals_yaml=scored_eval, runs=3, judge_runs=2)
    config = parse_and_validate(config_path)
    class _ByOutput(ProviderAdapter):
        """
        Scores from the output's own text, so repetitions of one output always
        agree and different outputs do not. A scripted queue cannot do this:
        the judge calls run in a thread pool, so queue order does not map to
        (output, repetition).
        """

        def call(self, model, prompt, gen, retry):
            run = next(n for n in ("1", "2", "3") if f"run {n}" in prompt)
            return {"score": {"1": 1, "2": 3, "3": 5}[run], "reasoning": "by output"}

    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=_ByOutput()):
        score(config=config, config_path=config_path)

    results = config_path.parent / "results"
    data = json.loads(next(results.glob("*-data.json")).read_text())
    rows = list(csv.DictReader(next(results.glob("*-data.csv")).read_text().splitlines()))
    md = next(results.glob("*-report.md")).read_text()
    html = next(results.glob("*-report.html")).read_text()

    stats = {
        eid: st
        for uc in data["summary"].values()
        for tag in uc.values()
        for eid, st in tag.items()
    }
    # JSON: the decomposition itself.
    assert stats["clarity"]["judge_runs"] == 2
    assert stats["clarity"]["system_stddev"] > 0, "outputs differ, so system spread must"
    assert stats["clarity"]["judge_stddev"] == 0.0, "the judge repeated itself exactly"

    # CSV: one row per repetition.
    assert sorted({r["judge_run"] for r in rows}) == ["1", "2"]

    # Both reports: the columns that carry the comparison.
    #
    # The HTML embeds the whole result set as JSON, so searching the file finds
    # every number whether or not it is displayed. This assertion passed with
    # the table cells deleted until the data blob was stripped first — the same
    # trap that made the missing judge sections invisible to grep.
    import re

    rendered = re.sub(r"const RUN_DATA = .*?;\n", "", html, flags=re.S)
    assert "RUN_DATA = {" not in rendered, "the data blob was not stripped"
    for artifact, name in ((md, "markdown"), (rendered, "html")):
        assert "system spread" in artifact, f"{name} lacks the system spread column"
        assert "judge spread" in artifact, f"{name} lacks the judge spread column"
        assert str(stats["clarity"]["system_stddev"]) in artifact, (
            f"{name} does not display the system spread value"
        )
        assert str(stats["clarity"]["judge_stddev"]) in artifact, (
            f"{name} does not display the judge spread value"
        )


def test_judge_error_remediation_names_the_provider_stated_cause(tmp_path, monkeypatch):
    """
    A real run died on an exhausted credit balance and the report said "check
    your API key" — sending someone to inspect a credential that was working.
    The provider had said exactly what was wrong; the report repeated generic
    advice over it.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.providers.base import ProviderAdapter
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    class _OutOfCredit(ProviderAdapter):
        def call(self, model, prompt, gen: JudgeGenerationConfig, retry: RetryPolicy):
            return {"error": "Error code: 400 - Your credit balance is too low "
                             "to access the Anthropic API."}

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=2)
    config = parse_and_validate(config_path)
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=_OutOfCredit()):
        run_id, rows = score(config=config, config_path=config_path,
                             write_artifacts=False)

    md = format_report(rows, build_summary(rows, config), {}, config, run_id, "full")
    assert "the account is out of credit" in md
    assert "check your API key" not in md


def test_generic_remediation_survives_for_an_unrecognised_error(tmp_path, monkeypatch):
    """When the provider says nothing useful, the old advice is still the best."""
    from fieldtest.config import parse_and_validate
    from fieldtest.providers.base import ProviderAdapter
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    class _Vague(ProviderAdapter):
        def call(self, model, prompt, gen, retry):
            return {"error": "something went wrong"}

    config_path = _project(tmp_path, evals_yaml=LLM_EVAL, runs=2)
    config = parse_and_validate(config_path)
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=_Vague()):
        run_id, rows = score(config=config, config_path=config_path,
                             write_artifacts=False)

    md = format_report(rows, build_summary(rows, config), {}, config, run_id, "full")
    assert "check your API key" in md
