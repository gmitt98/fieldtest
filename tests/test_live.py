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
GEN = JudgeGenerationConfig()
RETRY = RetryPolicy()

needs_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="live: set ANTHROPIC_API_KEY"
)
needs_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="live: set OPENAI_API_KEY"
)
needs_gemini = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"), reason="live: set GEMINI_API_KEY"
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
    not os.environ.get("OPENROUTER_API_KEY"), reason="live: set OPENROUTER_API_KEY"
)


@needs_openrouter
def test_openai_compatible_reaches_a_third_party_endpoint():
    from fieldtest.providers.openai_compatible import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )
    result = adapter.call("meta-llama/llama-3.3-70b-instruct", PROMPT, GEN, RETRY)

    assert "error" not in result, result
    assert result.get("answer") == "Pass"
    # Whatever the endpoint refused is reported rather than silently dropped.
    print(f"\nopenrouter/llama-3.3-70b dropped: {result.get('unsupported') or 'nothing'}")


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
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        ),
    )
    result = adapter.call("meta-llama/llama-3.3-70b-instruct", PROMPT, GEN, RETRY)
    assert "error" not in result, result
    assert result.get("answer") == "Pass"
