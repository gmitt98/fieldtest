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


def _dataset_dir():
    import fieldtest
    return Path(fieldtest.__file__).resolve().parent / "datasets" / DATASET


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
