"""
tests/test_live.py

Live tier (spec 12): real providers, real credentials, opt-in.

Excluded from the default run by `addopts = "-m 'not live'"`, so a provider
outage can never look like a contributor error. Run with `pytest -m live`.

These assert the *provider contract*, not fieldtest's logic. Everything about
fieldtest's own behaviour is covered by the unit and integration tiers, which
need no network. What only a real call can establish is whether the provider
still behaves the way the adapter assumes — and four defects reached a release
through a suite that could not ask.

Cost discipline: one call per assertion, no scoring runs, no panels. The whole
tier is a handful of requests. A test that needs a second call to be meaningful
should say why in a comment.
"""
from __future__ import annotations

import os

import pytest

from fieldtest.providers.base import JudgeGenerationConfig, RetryPolicy

pytestmark = pytest.mark.live

PROMPT = ('Reply with exactly this JSON and nothing else: '
          '{"answer": "Pass", "reasoning": "ok"}')

# openai_compatible is not an OpenRouter adapter — OpenRouter is just the
# endpoint that is cheapest to reach from a laptop. Point these at a local
# vLLM or Ollama instead by setting these two.
COMPATIBLE_BASE_URL = os.environ.get(
    "FIELDTEST_LIVE_BASE_URL", "https://openrouter.ai/api/v1"
)
COMPATIBLE_MODEL = os.environ.get(
    "FIELDTEST_LIVE_MODEL", "meta-llama/llama-3.3-70b-instruct"
)
GEN = JudgeGenerationConfig()
RETRY = RetryPolicy()

needs_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="live: set ANTHROPIC_API_KEY"
)


def _missing(module: str) -> bool:
    """True when `module` cannot be imported. See tests/test_providers.py."""
    import importlib.util

    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ModuleNotFoundError, ValueError):
        return True


# A key is not enough: these call the provider SDK, which is an optional extra.
# With a key set on a dev-only install they failed on import instead of skipping.
needs_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY") or _missing("openai"),
    reason="live: needs OPENAI_API_KEY and pip install -e '.[openai]'",
)
needs_gemini = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY") or _missing("google.genai"),
    reason="live: needs GEMINI_API_KEY and pip install -e '.[gemini]'",
)


# ---------------------------------------------------------------------------
# Model discovery — a candidate list, never a guarantee
# ---------------------------------------------------------------------------

def _openai_models(prefixes: tuple) -> list[str]:
    import openai
    ids = sorted(m.id for m in openai.OpenAI().models.list())
    return [m for m in ids if m.startswith(prefixes)]


def _gemini_models() -> list[str]:
    """Newest first. Google's list advertises models that 404 on call."""
    import re

    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    names = [m.name.replace("models/", "") for m in client.models.list()]

    def version(name: str):
        n = re.search(r"(\d+)\.(\d+)", name)
        return (int(n.group(1)), int(n.group(2))) if n else (0, 0)

    return sorted(
        (n for n in names if "flash" in n and "tts" not in n and "image" not in n),
        key=version, reverse=True,
    )


# ---------------------------------------------------------------------------
# The contract every adapter has to hold
# ---------------------------------------------------------------------------

@needs_anthropic
def test_live_anthropic_returns_parseable_json():
    from fieldtest.providers.anthropic import AnthropicAdapter

    r = AnthropicAdapter().call("claude-haiku-4-5", PROMPT, GEN, RETRY)
    assert "error" not in r, r.get("error")
    assert r["answer"] == "Pass"


@needs_openai
def test_live_openai_returns_parseable_json():
    from fieldtest.providers.openai import OpenAIAdapter

    models = _openai_models(("gpt-4o-mini",))
    if not models:
        pytest.skip("no gpt-4o-mini on this key")
    r = OpenAIAdapter().call(models[0], PROMPT, GEN, RETRY)
    assert "error" not in r, r.get("error")
    assert r["answer"] == "Pass"


@needs_gemini
def test_live_gemini_returns_parseable_json():
    """
    Walks candidates rather than naming one: Google's models.list() returned
    gemini-2.5-flash and calling it gave 404, no longer available to new users.
    Discovery is a candidate list, not a guarantee.
    """
    from fieldtest.providers.gemini import GeminiAdapter

    tried = []
    for model in _gemini_models()[:3]:
        r = GeminiAdapter().call(model, PROMPT, GEN, RETRY)
        if "error" not in r:
            assert r["answer"] == "Pass"
            return
        tried.append((model, r["error"][:120]))
    pytest.fail(f"no advertised flash model was callable: {tried}")


# ---------------------------------------------------------------------------
# The paths that only a provider can verify
# ---------------------------------------------------------------------------

@needs_openai
def test_live_unsupported_parameter_is_dropped_and_named():
    """
    The reason this tier exists. Reasoning models reject `temperature` and
    require `max_completion_tokens` instead of `max_tokens`; fieldtest sends
    both. A run that completes AND names what it lost is the whole contract of
    spec 02 §2.5 — and it was written from documentation, never triggered by a
    provider, until this test.
    """
    from fieldtest.providers.openai import OpenAIAdapter

    models = _openai_models(("o1", "o3", "o4", "gpt-5"))
    if not models:
        pytest.skip("no reasoning model on this key")

    r = OpenAIAdapter().call(models[0], PROMPT, GEN, RETRY)

    assert "error" not in r, r.get("error")
    assert r["answer"] == "Pass", "the call must complete, not fail"
    assert "temperature" in r.get("unsupported", []), (
        "temperature was accepted where the provider documents a 400 — either "
        "the model changed or rejects_parameter() stopped matching"
    )
    # A rename is not a capability loss, so it stays out of `unsupported`. It is
    # reported separately rather than inferred from the call not failing.
    assert ("max_tokens", "max_completion_tokens") in r.get("renamed", []), (
        f"max_tokens was not renamed; the adapter reported {r.get('renamed')}"
    )


@needs_gemini
def test_live_capture_rejection_wording():
    """
    Records the exact refusal text for tests/fixtures/provider_errors.py.

    A live probe showed Gemini rejecting `seed`, but the message was never
    captured, and a plausible-looking invention would put a string in that
    fixture no provider ever sent. This prints the real one. It asserts nothing
    about the wording — providers change it — only that a rejection is still
    detected and degraded.
    """
    from fieldtest.providers.gemini import GeminiAdapter

    models = _gemini_models()
    if not models:
        pytest.skip("no flash model advertised")

    r = GeminiAdapter().call(models[0], PROMPT, JudgeGenerationConfig(seed=42), RETRY)
    if "error" in r:
        pytest.skip(f"model unavailable: {r['error'][:120]}")

    dropped = r.get("unsupported", [])
    print(f"\n  {models[0]} dropped: {dropped or 'nothing'}")
    assert r["answer"] == "Pass", "a refused parameter must not fail the call"


@needs_anthropic
def test_live_pinned_temperature_is_accepted_or_reported():
    """
    Either the judge is pinned or the report says it is not. Silently unpinned
    is the one outcome spec 02 rules out.
    """
    from fieldtest.providers.anthropic import AnthropicAdapter

    r = AnthropicAdapter().call("claude-haiku-4-5", PROMPT, GEN, RETRY)
    assert "error" not in r, r.get("error")
    assert "temperature" not in r.get("unsupported", []), (
        "claude-haiku-4-5 stopped accepting temperature — the default judge is "
        "no longer pinnable and defaults.model needs to move"
    )


# ---------------------------------------------------------------------------
# openai_compatible against a real endpoint (spec 11)
#
# What only a live call can establish: that pointing the OpenAI request path at
# another base_url still returns a parseable verdict. The claim that it does is
# what shrank spec 11's problem statement, so it should not rest on a mock.
# ---------------------------------------------------------------------------

needs_openrouter = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason=(
        "live: OPENROUTER_API_KEY unset — the openai_compatible adapter goes "
        "untested. Key from openrouter.ai/keys; these two tests cost a few "
        "cents total."
    ),
)


@needs_openrouter
def test_openai_compatible_reaches_a_third_party_endpoint():
    from fieldtest.providers.openai_compatible import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(
        base_url=COMPATIBLE_BASE_URL,
        api_key_env="OPENROUTER_API_KEY",
    )
    result = adapter.call(COMPATIBLE_MODEL, PROMPT, GEN, RETRY)

    assert "error" not in result, result
    assert result.get("answer") == "Pass"
    # Whatever the endpoint refused is reported rather than silently dropped.
    print(f"\n{COMPATIBLE_MODEL} at {COMPATIBLE_BASE_URL} dropped: {result.get('unsupported') or 'nothing'}")


@needs_openrouter
def test_openai_compatible_config_path_end_to_end(tmp_path):
    """
    The adapter reached through config rather than constructed directly, since
    naming the endpoint in config.yaml is the entire point of the provider.
    """
    from fieldtest.config import ProviderSettings
    from fieldtest.providers import get_provider_adapter

    adapter = get_provider_adapter(
        "openai_compatible",
        ProviderSettings(
            base_url=COMPATIBLE_BASE_URL,
            api_key_env="OPENROUTER_API_KEY",
        ),
    )
    result = adapter.call(COMPATIBLE_MODEL, PROMPT, GEN, RETRY)
    assert "error" not in result, result
    assert result.get("answer") == "Pass"


# ---------------------------------------------------------------------------
# The judge path, not just the adapter
#
# Every test above asserts on the adapter's dict. 0.3.0 made a reply carrying no
# usable verdict an error rather than a silent Fail, and nothing live exercises
# that parse — so a real model whose reply stopped matching would show up as a
# judged Fail here and be invisible. These run the whole way through
# judge_llm_binary and judge_llm_scored.
# ---------------------------------------------------------------------------

def _live_config(binary: bool):
    from fieldtest.config import Config

    ev = {
        "id": "live_probe", "tag": "right", "type": "llm",
        "description": "whether the output is a greeting",
    }
    if binary:
        ev |= {"pass_criteria": "the output greets someone",
               "fail_criteria": "the output does not greet anyone"}
    else:
        ev |= {"binary": False, "scale": [1, 5],
               "anchors": {1: "not a greeting at all", 5: "an unmistakable greeting"}}
    return Config.model_validate({
        "schema_version": 1,
        "system": {"name": "live", "domain": "live"},
        "defaults": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "use_cases": [{
            "id": "uc1", "description": "d", "evals": [ev],
            "fixtures": {"directory": "fixtures/", "sets": {"full": []}},
        }],
    }), ev


@needs_anthropic
def test_live_binary_judge_returns_a_verdict_not_an_error():
    """A real reply must survive the verdict check, not fall through it."""
    from fieldtest.config import Eval
    from fieldtest.judges.llm import judge_llm_binary

    config, ev = _live_config(binary=True)
    row = judge_llm_binary(
        "uc1", Eval.model_validate(ev), "Hello there, good to meet you.",
        {"id": "fx", "inputs": {}}, 1, config,
    )
    assert row.error is None, f"a real judge reply was rejected: {row.error}"
    assert row.passed is True, row.detail


@needs_anthropic
def test_live_scored_judge_returns_a_score_inside_its_scale():
    """0.3.0 makes an out-of-range score an error; a real model must stay in range."""
    from fieldtest.config import Eval
    from fieldtest.judges.llm import judge_llm_scored

    config, ev = _live_config(binary=False)
    row = judge_llm_scored(
        "uc1", Eval.model_validate(ev), "Hello there, good to meet you.",
        {"id": "fx", "inputs": {}}, 1, config,
    )
    assert row.error is None, f"a real judge score was rejected: {row.error}"
    assert 1 <= row.score <= 5, row.score


@needs_anthropic
def test_the_site_repeatability_figures_still_match_a_real_run(tmp_path, monkeypatch):
    """
    The last figures on the site that were never checked against a run. They
    matched when checked by hand; this keeps them matching.

    Live rather than unit: the numbers come from an LLM judging the bundled
    outputs twice. A drift here is a real signal — either the provider changed
    or the site is now wrong — which is what the live tier is for.
    """
    import html
    import re
    import shutil
    from pathlib import Path

    import fieldtest
    from fieldtest.config import parse_and_validate
    from fieldtest.results.aggregator import build_summary
    from fieldtest.results.report import format_report
    from fieldtest.runner import score

    site = (Path(fieldtest.__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    i = site.index("### Judge Repeatability")
    shown = html.unescape(re.sub(r"<[^>]+>", "", site[i:site.index("</pre>", i)]))
    site_rows = {
        m.group(1): [c.strip() for c in m.group(2).split("|") if c.strip()]
        for m in re.finditer(r"\|\s*(\w+)\s*\|([^\n]*)\|", shown)
    }
    # The header matches this regex like any other row, so it lands in the dict
    # as "eval". Written without that, the assertion below could never hold: it
    # has been in the tree since 34ce982 and, because the live tier is opt-in,
    # had never once run. The figures it exists to check were never checked.
    header = site_rows.pop("eval", None)
    assert header is not None, f"the site block lost its header row: {site_rows}"
    assert len(site_rows) == 3, f"the site block changed shape: {site_rows}"

    src = Path(fieldtest.__file__).resolve().parent / "datasets" / "expense-report"
    dest = tmp_path / "evals"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))

    cfg = dest / "reference-evals.yaml"
    text = cfg.read_text()
    assert "      sets:" in text, "reference-evals.yaml changed shape"
    cfg.write_text(text.replace("      sets:", "      judge_runs: 2\n      sets:", 1))

    from fieldtest.judges.registry import _loaded_rule_files, _rule_registry
    _rule_registry.clear()
    _loaded_rule_files.clear()

    config = parse_and_validate(cfg)
    _, rows = score(config=config, config_path=cfg, set_name="full",
                    write_artifacts=False)
    summary = build_summary(rows, config)
    report = format_report(rows, summary, {}, config, "live-probe", "full")

    j = report.index("### Judge Repeatability")
    real = {
        m.group(1): [c.strip() for c in m.group(2).split("|") if c.strip()]
        for m in re.finditer(r"\|\s*(\w+)\s*\|([^\n]*)\|", report[j:j + 900])
    }

    real.pop("eval", None)

    # The exact system-spread decimal is not pinned, and the history is why.
    # The bundled outputs have not changed since the dataset landed, yet that
    # figure has read 1.633, 1.7321 and 1.8028 across three days — and it
    # oscillates rather than drifts: 1.7321 and 1.8028 have each come up
    # repeatedly. Within one session the judge is stable, which is what
    # `judge_runs` measures; across sessions it is not, and that variance has
    # nowhere to go but the system-spread column. Pinning the decimal tests the
    # provider's mood on the day.
    #
    # What the site actually argues: the judge's own wobble is small next to
    # how much the outputs differ from one another — that is the distinction
    # judge_runs exists to draw. An earlier version of this test asserted judge
    # spread == 0.0, because it had been 0.0 on every run seen; then a run came
    # in at 0.2357 and the "invariant" turned out to be one more observation
    # promoted to a claim, on the same table, for the fourth time. A judge at
    # temperature 0 is usually self-consistent within a run, not always. That
    # is fine, and it is precisely what the column is for.
    # Subset, not equality: the 900-char window also catches the header of the
    # table that follows. What matters is that every eval the site publishes is
    # one the run actually produces.
    missing = sorted(set(site_rows) - set(real))
    assert not missing, (
        f"the site shows evals the run does not produce: {missing}")

    for name, values in {k: real[k] for k in site_rows}.items():
        disagreement, system_spread, judge_spread = values
        if judge_spread != "—":
            js = float(judge_spread)
            assert js >= 0.0, f"{name}: judge spread {judge_spread} is not a spread"
            if system_spread != "—":
                assert float(system_spread) > js, (
                    f"{name}: judge spread {judge_spread} is not smaller than "
                    f"system spread {system_spread} — the site's argument is that "
                    f"the outputs differ more than the judge wavers")
        if disagreement != "—":
            d = float(disagreement.rstrip("%"))
            assert 0.0 <= d <= 100.0, f"{name}: disagreement {disagreement} is not a %"
        if system_spread != "—":
            # A tripwire, not the invariant. The derived invariant is simply
            # "> 0 while judge spread is 0"; 1.0 is a floor calibrated to the
            # observed history (lowest seen 1.633), so a run beneath it should
            # wake somebody rather than pass quietly.
            assert float(system_spread) >= 1.0, (
                f"{name}: system spread {system_spread} on a 1-5 scale is far "
                f"below anything previously observed")

    # The site must show the same shape, without its numbers being pinned to a
    # given day's run. Which cells carry a figure and which carry a dash is
    # derived from the run, not hardcoded — a scored eval reports spreads and no
    # disagreement, a binary one the reverse. Guarding each site assertion on
    # `!= "—"` alone let two mutations through: dashing out every site figure
    # skipped all of them, and publishing a system spread of 0.0 passed a check
    # that only parsed the number.
    for name, values in site_rows.items():
        site_dis, site_sys, site_judge = values
        run_dis, run_sys, run_judge = real[name]

        for label, site_cell, run_cell in (
            ("disagreement", site_dis, run_dis),
            ("system spread", site_sys, run_sys),
            ("judge spread", site_judge, run_judge),
        ):
            assert (site_cell == "—") == (run_cell == "—"), (
                f"{name}: the site shows {label} as {site_cell!r} where the run "
                f"gives {run_cell!r} — one of them is a placeholder the other "
                f"is not")

        if site_judge != "—":
            js = float(site_judge)
            assert js >= 0.0
            if site_sys != "—":
                assert float(site_sys) > js, (
                    f"the site's table for {name} does not show system spread "
                    f"exceeding judge spread, which is the point it makes")
        if site_dis != "—":
            d = float(site_dis.rstrip("%"))
            assert 0.0 <= d <= 100.0
        if site_sys != "—":
            assert float(site_sys) >= 1.0, (
                f"the site publishes system spread {site_sys} for {name}, which "
                f"does not support its claim that the outputs genuinely differ")
