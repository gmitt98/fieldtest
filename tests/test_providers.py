"""
tests/test_providers.py

Tests for provider adapters and factory.
"""
from unittest.mock import DEFAULT, MagicMock, patch

import importlib.util

import pytest

from fieldtest.errors import ProviderError
from fieldtest.providers import get_provider_adapter
from fieldtest.providers.anthropic import AnthropicAdapter
from fieldtest.providers.base import JudgeGenerationConfig, RetryPolicy
from fieldtest.providers.gemini import GeminiAdapter
from fieldtest.providers.openai import OpenAIAdapter


# Optional-provider imports. `pip install -e ".[dev]"` pulls pytest and nothing
# else, so these raised ModuleNotFoundError and a contributor's first suite run
# showed ten failures that were not theirs. httpx is in the list because
# anthropic 1.x depends on httpx2, so plain httpx is no longer transitive.
def _missing(module: str) -> bool:
    """True when `module` cannot be imported.

    find_spec("google.genai") raises rather than returning None when the parent
    package is absent, which turned a skip into a collection error and took the
    whole file down with it.
    """
    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ModuleNotFoundError, ValueError):
        return True


needs_httpx = pytest.mark.skipif(
    _missing("httpx"),
    reason="httpx not installed (anthropic 1.x uses httpx2); pip install -e '.[dev]' does not pull it",
)
needs_google_genai = pytest.mark.skipif(
    _missing("google.genai"),
    reason="google-genai not installed; pip install -e '.[gemini]'",
)
needs_openai_pkg = pytest.mark.skipif(
    _missing("openai"),
    reason="openai not installed; pip install -e '.[openai]'",
)


# Default generation config: temperature 0.0, no seed, 2048 max tokens.
GEN = JudgeGenerationConfig()
# Default retry policy: 6 retries on 5/10/20/40/60/60 second backoff.
RETRY = RetryPolicy()


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

def test_factory_returns_anthropic():
    adapter = get_provider_adapter("anthropic")
    assert isinstance(adapter, AnthropicAdapter)


def test_factory_returns_openai():
    adapter = get_provider_adapter("openai")
    assert isinstance(adapter, OpenAIAdapter)


def test_factory_returns_gemini():
    adapter = get_provider_adapter("gemini")
    assert isinstance(adapter, GeminiAdapter)


def test_factory_unknown_provider():
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider_adapter("cohere")


# ---------------------------------------------------------------------------
# OpenAI adapter tests (mocked — no real API calls)
# ---------------------------------------------------------------------------

def test_openai_missing_api_key():
    mock_openai_module = MagicMock()

    with patch.dict("os.environ", {}, clear=True):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "OPENAI_API_KEY" in result["error"]


def test_openai_missing_package():
    adapter = OpenAIAdapter()
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": None}):
            result = adapter.call("gpt-4o", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "openai" in result["error"].lower()


def test_openai_successful_call():
    mock_message = MagicMock()
    mock_message.content = '{"answer": "Pass", "reasoning": "Looks good"}'
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response

    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            # Re-import to pick up mocked module
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", GEN, RETRY)

    assert result == {"answer": "Pass", "reasoning": "Looks good"}


def test_openai_strips_markdown_fences():
    mock_message = MagicMock()
    mock_message.content = '```json\n{"answer": "Fail", "reasoning": "Bad"}\n```'
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response

    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", GEN, RETRY)

    assert result == {"answer": "Fail", "reasoning": "Bad"}


def test_openai_non_json_response():
    mock_message = MagicMock()
    mock_message.content = "This is not JSON"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response

    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", GEN, RETRY)

    assert "error" in result
    assert "non-JSON" in result["error"]


# ---------------------------------------------------------------------------
# Gemini adapter tests (mocked — no real API calls)
# ---------------------------------------------------------------------------

def test_gemini_missing_api_key():
    mock_genai_module = MagicMock()
    mock_google_module = MagicMock()
    mock_google_module.genai = mock_genai_module

    with patch.dict("os.environ", {}, clear=True):
        with patch.dict("sys.modules", {"google": mock_google_module, "google.genai": mock_genai_module}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "GEMINI_API_KEY" in result["error"]


def test_gemini_missing_package():
    adapter = GeminiAdapter()
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            result = adapter.call("gemini-2.5-flash", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "google-genai" in result["error"].lower()


def test_gemini_successful_call():
    mock_response = MagicMock()
    mock_response.text = '{"answer": "Pass", "reasoning": "Looks good"}'

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_genai_module = MagicMock()
    mock_genai_module.Client.return_value = mock_client_instance
    mock_google_module = MagicMock()
    mock_google_module.genai = mock_genai_module

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": mock_google_module, "google.genai": mock_genai_module}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt", GEN, RETRY)

    assert result == {"answer": "Pass", "reasoning": "Looks good"}


def test_gemini_strips_markdown_fences():
    mock_response = MagicMock()
    mock_response.text = '```json\n{"answer": "Fail", "reasoning": "Bad"}\n```'

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_genai_module = MagicMock()
    mock_genai_module.Client.return_value = mock_client_instance
    mock_google_module = MagicMock()
    mock_google_module.genai = mock_genai_module

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": mock_google_module, "google.genai": mock_genai_module}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt", GEN, RETRY)

    assert result == {"answer": "Fail", "reasoning": "Bad"}


def test_gemini_non_json_response():
    mock_response = MagicMock()
    mock_response.text = "This is not JSON"

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_genai_module = MagicMock()
    mock_genai_module.Client.return_value = mock_client_instance
    mock_google_module = MagicMock()
    mock_google_module.genai = mock_genai_module

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": mock_google_module, "google.genai": mock_genai_module}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt", GEN, RETRY)

    assert "error" in result
    assert "non-JSON" in result["error"]


# ---------------------------------------------------------------------------
# Anthropic adapter tests (mocked — no real API calls)
# ---------------------------------------------------------------------------

def _make_anthropic_module(*, raises=None, returns_text=None):
    """
    Build a mock `anthropic` module. Pass either `raises` (an exception
    instance, or a list of exceptions to raise in sequence then succeed) or
    `returns_text` to control what the mocked client returns.
    """
    mock_anthropic_module = MagicMock()

    # Real-ish APIStatusError that the adapter's except clause can catch.
    class _APIStatusError(Exception):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    mock_anthropic_module.APIStatusError = _APIStatusError

    mock_client_instance = MagicMock()

    if raises is not None:
        side = raises if isinstance(raises, list) else [raises]
        # After exhausting `side`, fall back to returning a successful response
        # so retry-then-succeed scenarios work.
        success_message = MagicMock()
        success_message.content = [MagicMock(text=returns_text or '{"answer": "Pass", "reasoning": "ok"}')]
        mock_client_instance.messages.create.side_effect = side + [success_message]
    else:
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=returns_text)]
        mock_client_instance.messages.create.return_value = mock_message

    mock_anthropic_module.Anthropic.return_value = mock_client_instance
    return mock_anthropic_module, mock_client_instance, _APIStatusError


def test_anthropic_missing_api_key():
    mock_anthropic_module = MagicMock()
    with patch.dict("os.environ", {}, clear=True):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "ANTHROPIC_API_KEY" in result["error"]


def test_anthropic_missing_package():
    adapter = AnthropicAdapter()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": None}):
            result = adapter.call("claude-haiku-4-5", "test prompt", GEN, RETRY)
    assert "error" in result
    assert "anthropic" in result["error"].lower()


def test_anthropic_successful_call():
    mock_anthropic_module, _, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "Looks good"}'
    )

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert result == {"answer": "Pass", "reasoning": "Looks good"}


def test_anthropic_strips_markdown_fences():
    mock_anthropic_module, _, _ = _make_anthropic_module(
        returns_text='```json\n{"answer": "Fail", "reasoning": "Bad"}\n```'
    )

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert result == {"answer": "Fail", "reasoning": "Bad"}


def test_anthropic_non_json_response():
    mock_anthropic_module, _, _ = _make_anthropic_module(returns_text="This is not JSON")

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert "error" in result
    assert "non-JSON" in result["error"]


def test_anthropic_retries_on_overloaded_then_succeeds():
    """529 OverloadedError on first attempt, success on second — should retry."""
    mock_anthropic_module = MagicMock()

    class _APIStatusError(Exception):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    mock_anthropic_module.APIStatusError = _APIStatusError

    overload_err = _APIStatusError("Overloaded", 529)
    success_message = MagicMock()
    success_message.content = [MagicMock(text='{"answer": "Pass", "reasoning": "ok"}')]

    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.side_effect = [overload_err, success_message]
    mock_anthropic_module.Anthropic.return_value = mock_client_instance

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert result == {"answer": "Pass", "reasoning": "ok"}
    assert mock_client_instance.messages.create.call_count == 2
    mock_sleep.assert_called_once_with(5)  # first backoff


def test_anthropic_exhausts_retries_on_persistent_overload():
    """Every attempt 529s — adapter should give up after the schedule and return error."""
    mock_anthropic_module = MagicMock()

    class _APIStatusError(Exception):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    mock_anthropic_module.APIStatusError = _APIStatusError
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.side_effect = _APIStatusError("Overloaded", 529)
    mock_anthropic_module.Anthropic.return_value = mock_client_instance

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert "error" in result
    assert "Overloaded" in result["error"]
    # 7 attempts total = 1 initial + 6 retries from the backoff schedule
    assert mock_client_instance.messages.create.call_count == 7
    assert mock_sleep.call_count == 6


def test_anthropic_does_not_retry_non_529_status_errors():
    """Non-529 APIStatusError (e.g. 401) should fail immediately, no retry."""
    mock_anthropic_module = MagicMock()

    class _APIStatusError(Exception):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    mock_anthropic_module.APIStatusError = _APIStatusError
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.side_effect = _APIStatusError("Unauthorized", 401)
    mock_anthropic_module.Anthropic.return_value = mock_client_instance

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert "error" in result
    assert mock_client_instance.messages.create.call_count == 1
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Config validation — provider field
# ---------------------------------------------------------------------------

def test_config_accepts_openai_provider(tmp_path):
    from fieldtest.config import parse_and_validate

    content = """\
schema_version: 1
system:
  name: test
  domain: test
defaults:
  provider: openai
  model: gpt-4o
use_cases:
  - id: uc1
    description: test
    evals:
      - id: ev1
        tag: right
        type: regex
        description: checks something
        pattern: "foo"
        match: true
    fixtures:
      directory: fixtures/
      sets:
        full: []
"""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = parse_and_validate(p)
    assert cfg.defaults.provider == "openai"
    assert cfg.defaults.model == "gpt-4o"


def test_config_accepts_gemini_provider(tmp_path):
    from fieldtest.config import parse_and_validate

    content = """\
schema_version: 1
system:
  name: test
  domain: test
defaults:
  provider: gemini
  model: gemini-2.5-flash
use_cases:
  - id: uc1
    description: test
    evals:
      - id: ev1
        tag: right
        type: regex
        description: checks something
        pattern: "foo"
        match: true
    fixtures:
      directory: fixtures/
      sets:
        full: []
"""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = parse_and_validate(p)
    assert cfg.defaults.provider == "gemini"
    assert cfg.defaults.model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Judge generation config (spec 02)
# ---------------------------------------------------------------------------

def test_adapter_call_accepts_generation_config():
    """Every adapter takes a JudgeGenerationConfig and forwards its settings."""
    gen = JudgeGenerationConfig(temperature=0.3, max_tokens=512)

    mock_anthropic_module, mock_client, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "ok"}'
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", gen, RETRY)

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["temperature"] == 0.3
    assert kwargs["max_tokens"] == 512

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"answer": "Pass", "reasoning": "ok"}'))]
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", gen, RETRY)

    kwargs = mock_client_instance.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.3
    assert kwargs["max_tokens"] == 512


def test_openai_forwards_seed_when_set():
    """OpenAI supports seed, so it is passed through when the user sets one."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"answer": "Pass", "reasoning": "ok"}'))]
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call(
                "gpt-4o", "test prompt", JudgeGenerationConfig(seed=42), RETRY
            )

    assert mock_client_instance.chat.completions.create.call_args.kwargs["seed"] == 42
    assert "unsupported" not in result


def test_openai_omits_seed_when_unset():
    """No seed requested means no seed key — not seed=None."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"answer": "Pass", "reasoning": "ok"}'))]
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_response
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt", GEN, RETRY)

    assert "seed" not in mock_client_instance.chat.completions.create.call_args.kwargs


def test_anthropic_adapter_reports_seed_unsupported():
    """Anthropic has no seed parameter: drop it, name it, do not fail."""
    mock_anthropic_module, mock_client, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "ok"}'
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call(
                "claude-haiku-4-5", "test prompt", JudgeGenerationConfig(seed=42), RETRY
            )

    assert result["unsupported"] == ["seed"]
    assert result["answer"] == "Pass"
    assert "seed" not in mock_client.messages.create.call_args.kwargs


def test_anthropic_reports_nothing_unsupported_without_seed():
    """The unsupported key appears only when a parameter was actually dropped."""
    mock_anthropic_module, _, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "ok"}'
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert "unsupported" not in result


def test_gemini_adapter_sets_max_tokens():
    """Gemini previously did not bound output length at all."""
    mock_response = MagicMock()
    mock_response.text = '{"answer": "Pass", "reasoning": "ok"}'
    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_genai_module = MagicMock()
    mock_genai_module.Client.return_value = mock_client_instance
    mock_google_module = MagicMock()
    mock_google_module.genai = mock_genai_module

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": mock_google_module, "google.genai": mock_genai_module}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt", GEN, RETRY)

    mock_genai_module.types.GenerateContentConfig.assert_called_once_with(
        temperature=0.0, max_output_tokens=2048
    )


def test_unsupported_params_surface_once_in_report():
    """
    Two judge calls that both drop the same parameter produce one report line,
    not one per row. The record is per run, not per ResultRow.
    """
    from fieldtest.config import Config, Defaults, Eval, SystemConfig
    from fieldtest.judges.llm import (
        call_judge_llm,
        get_unsupported_params,
        reset_unsupported_params,
    )
    from fieldtest.results.report import format_report

    config = Config(
        schema_version=1,
        system=SystemConfig(name="test", domain="test"),
        use_cases=[],
        defaults=Defaults(judge_seed=42),
    )
    ev = Eval(
        id="ev1", tag="right", type="llm", description="test eval",
        pass_criteria="passes", fail_criteria="fails",
    )

    fake_adapter = MagicMock()
    fake_adapter.call.return_value = {
        "answer": "Pass", "reasoning": "ok", "unsupported": ["seed"],
    }

    reset_unsupported_params()
    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=fake_adapter):
        first  = call_judge_llm("prompt one", ev, config)
        second = call_judge_llm("prompt two", ev, config)

    # Collected once, deduped, and stripped from the response the judge sees.
    assert get_unsupported_params() == ["seed (anthropic)"]
    assert "unsupported" not in first
    assert "unsupported" not in second

    report = format_report(
        rows=[], summary={}, delta={}, config=config,
        run_id="test-run", set_name="full",
        unsupported_params=get_unsupported_params(),
    )
    assert report.count("judge parameters ignored by provider") == 1
    assert "seed (anthropic)" in report


def _llm_judge_config(**defaults_kwargs):
    """Config with one llm eval, so the report's judge header applies."""
    from fieldtest.config import (
        Config, Defaults, Eval, FixturesConfig, SystemConfig, UseCase,
    )
    return Config(
        schema_version=1,
        system=SystemConfig(name="test", domain="test"),
        use_cases=[
            UseCase(
                id="uc1",
                description="test use case",
                evals=[Eval(
                    id="ev1", tag="right", type="llm", description="test eval",
                    pass_criteria="passes", fail_criteria="fails",
                )],
                fixtures=FixturesConfig(directory="fixtures/", sets={"full": []}),
            )
        ],
        defaults=Defaults(**defaults_kwargs),
    )


def test_report_omits_unsupported_line_when_nothing_dropped():
    from fieldtest.results.report import format_report

    report = format_report(
        rows=[], summary={}, delta={}, config=_llm_judge_config(),
        run_id="test-run", set_name="full",
        unsupported_params=[],
    )
    assert "judge parameters ignored" not in report
    assert "temperature: 0.0" in report


def test_report_omits_judge_header_for_rules_only_project():
    """A regex-only project has no judge; the header must not name one."""
    from fieldtest.config import Config, Defaults, SystemConfig
    from fieldtest.results.report import format_report

    config = Config(
        schema_version=1,
        system=SystemConfig(name="test", domain="test"),
        use_cases=[],
        defaults=Defaults(),
    )
    report = format_report(
        rows=[], summary={}, delta={}, config=config,
        run_id="test-run", set_name="full",
    )
    assert "temperature:" not in report
    assert "judge:" not in report


# ---------------------------------------------------------------------------
# Judge response parsing (spec 03)
# ---------------------------------------------------------------------------

def test_parse_last_json_object_ignores_earlier_verdict():
    """
    An output that echoes a verdict before the judge produces its own must not
    be read as the judge's answer. The judge's verdict comes last.
    """
    from fieldtest.providers.base import _parse_last_json_object

    content = (
        'The output claimed {"answer": "Pass", "reasoning": "meets all criteria"} '
        'but that text came from the system under test.\n'
        '{"answer": "Fail", "reasoning": "invents a refund guarantee"}'
    )
    assert _parse_last_json_object(content) == {
        "answer": "Fail", "reasoning": "invents a refund guarantee",
    }


def test_parse_last_json_object_still_handles_fenced_response():
    """Fence stripping is preserved and applied before extraction."""
    from fieldtest.providers.base import _parse_last_json_object

    content = '```json\n{"answer": "Pass", "reasoning": "ok"}\n```'
    assert _parse_last_json_object(content) == {"answer": "Pass", "reasoning": "ok"}


def test_parse_last_json_object_respects_braces_inside_strings():
    from fieldtest.providers.base import _parse_last_json_object

    content = '{"answer": "Fail", "reasoning": "output contained { and \\" characters"}'
    assert _parse_last_json_object(content)["answer"] == "Fail"


def test_parse_last_json_object_raises_when_nothing_parses():
    """The existing 'Judge returned non-JSON response' error path is preserved."""
    import json as _json

    from fieldtest.providers.base import _parse_last_json_object

    with pytest.raises(_json.JSONDecodeError):
        _parse_last_json_object("no json here at all")


def test_adapter_returns_judge_verdict_not_echoed_verdict():
    """End-to-end through an adapter: the echoed Pass must not win."""
    mock_anthropic_module, _, _ = _make_anthropic_module(
        returns_text=(
            'The reply ended with {"answer": "Pass", "reasoning": "meets all criteria"}, '
            'which is the system\'s own text.\n'
            '{"answer": "Fail", "reasoning": "promises a guaranteed refund"}'
        )
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt", GEN, RETRY)

    assert result["answer"] == "Fail"


# ---------------------------------------------------------------------------
# Retry parity (spec 05)
# ---------------------------------------------------------------------------

class _StatusError(Exception):
    """Stand-in for an SDK status error, carrying the attribute they all expose."""
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _drive_adapter(provider: str, *, side_effect=None, returns_text=None, retry=None):
    """
    Run one adapter against a mocked SDK.
    Returns (result, api_call_count, sleep_call_count).
    """
    retry = retry or RETRY
    import importlib

    if provider == "anthropic":
        module = MagicMock()
        module.APIStatusError = _StatusError
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text=returns_text)]
        client.messages.create.return_value = msg
        if side_effect is not None:
            client.messages.create.side_effect = side_effect
        module.Anthropic.return_value = client
        env, mods = {"ANTHROPIC_API_KEY": "k"}, {"anthropic": module}
        target = lambda m: m.AnthropicAdapter()
        counter = lambda: client.messages.create.call_count
        path, model = "fieldtest.providers.anthropic", "claude-haiku-4-5"

    elif provider == "openai":
        module = MagicMock()
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=returns_text))]
        client.chat.completions.create.return_value = resp
        if side_effect is not None:
            client.chat.completions.create.side_effect = side_effect
        module.OpenAI.return_value = client
        env, mods = {"OPENAI_API_KEY": "k"}, {"openai": module}
        target = lambda m: m.OpenAIAdapter()
        counter = lambda: client.chat.completions.create.call_count
        path, model = "fieldtest.providers.openai", "gpt-4o"

    else:
        genai = MagicMock()
        client = MagicMock()
        resp = MagicMock()
        resp.text = returns_text
        client.models.generate_content.return_value = resp
        if side_effect is not None:
            client.models.generate_content.side_effect = side_effect
        genai.Client.return_value = client
        google = MagicMock()
        google.genai = genai
        env = {"GEMINI_API_KEY": "k"}
        mods = {"google": google, "google.genai": genai}
        target = lambda m: m.GeminiAdapter()
        counter = lambda: client.models.generate_content.call_count
        path, model = "fieldtest.providers.gemini", "gemini-2.5-flash"

    with patch.dict("os.environ", env):
        with patch.dict("sys.modules", mods):
            mod = importlib.import_module(path)
            importlib.reload(mod)
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = target(mod).call(model, "test prompt", GEN, retry)

    return result, counter(), mock_sleep.call_count


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_all_adapters_retry_rate_limit(provider):
    """429 is transient everywhere — previously only Anthropic retried anything."""
    result, calls, sleeps = _drive_adapter(
        provider, side_effect=_StatusError("Rate limited", 429)
    )
    assert "error" in result
    assert calls == 7    # 1 initial + 6 retries
    assert sleeps == 6


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_all_adapters_retry_server_error(provider, status):
    result, calls, sleeps = _drive_adapter(
        provider, side_effect=_StatusError("Server error", status)
    )
    assert "error" in result
    assert calls == 7
    assert sleeps == 6


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_auth_failure_not_retried(provider):
    """401 is a standing condition; retrying cannot fix it."""
    result, calls, sleeps = _drive_adapter(
        provider, side_effect=_StatusError("Unauthorized", 401)
    )
    assert "Unauthorized" in result["error"]
    assert calls == 1
    assert sleeps == 0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_invalid_model_not_retried(provider):
    result, calls, sleeps = _drive_adapter(
        provider, side_effect=_StatusError("model not found", 404)
    )
    assert calls == 1
    assert sleeps == 0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_non_json_response_not_retried(provider):
    """A malformed verdict is an answer, not a transient failure."""
    result, calls, sleeps = _drive_adapter(provider, returns_text="not json at all")
    assert "Judge returned non-JSON response" in result["error"]
    assert calls == 1
    assert sleeps == 0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_retry_policy_configurable(provider):
    """A fast local demo and a nightly CI run want different patience."""
    policy = RetryPolicy(max_attempts=2, initial_delay=1.0, max_delay=4.0, multiplier=2.0)
    result, calls, sleeps = _drive_adapter(
        provider, side_effect=_StatusError("Overloaded", 529), retry=policy
    )
    assert "error" in result
    assert calls == 3
    assert sleeps == 2


def test_anthropic_schedule_unchanged_by_default():
    """The original schedule is reproduced exactly, not re-tuned."""
    assert [RetryPolicy().delay_for(i) for i in range(RetryPolicy().max_attempts)] == [
        5.0, 10.0, 20.0, 40.0, 60.0, 60.0
    ]

    module = MagicMock()
    module.APIStatusError = _StatusError
    client = MagicMock()
    client.messages.create.side_effect = _StatusError("Overloaded", 529)
    module.Anthropic.return_value = client

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.dict("sys.modules", {"anthropic": module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "p", GEN, RETRY)

    assert [c.args[0] for c in mock_sleep.call_args_list] == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


def test_retry_succeeds_after_transient_failures():
    """The point of the policy: complete the run rather than shrink the sample."""
    module = MagicMock()
    module.APIStatusError = _StatusError
    client = MagicMock()
    success = MagicMock()
    success.content = [MagicMock(text='{"answer": "Pass", "reasoning": "ok"}')]
    client.messages.create.side_effect = [
        _StatusError("Overloaded", 529),
        _StatusError("Rate limited", 429),
        success,
    ]
    module.Anthropic.return_value = client

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.dict("sys.modules", {"anthropic": module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            with patch("fieldtest.providers.base.time.sleep"):
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "p", GEN, RETRY)

    assert result == {"answer": "Pass", "reasoning": "ok"}


# ---------------------------------------------------------------------------
# Judge response must be a dict (review finding)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", ['"just a string"', "[1,2,3]", "null", "42"])
def test_parse_last_json_object_rejects_non_objects(payload):
    """
    A bare scalar or an object-free array is a malformed verdict, not a crash.
    Every caller indexes the return value, so the dict contract must hold.
    """
    import json as _json

    from fieldtest.providers.base import _parse_last_json_object

    with pytest.raises(_json.JSONDecodeError):
        _parse_last_json_object(payload)


def test_non_object_response_becomes_an_errored_row_not_a_crash():
    """The adapter's documented 'never raises' contract has to survive this."""
    mock_anthropic_module, _, _ = _make_anthropic_module(returns_text='["Pass"]')

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "p", GEN, RETRY)

    assert "Judge returned non-JSON response" in result["error"]


def test_call_judge_llm_survives_an_adapter_that_breaks_the_contract():
    """
    call_judge_llm runs inside a ThreadPoolExecutor. An exception here reaches
    future.result() and takes every other eval in the run down with it, so a
    third-party adapter returning the wrong type must yield one errored row.
    """
    from fieldtest.config import Config, Defaults, Eval, SystemConfig
    from fieldtest.judges.llm import call_judge_llm

    config = Config(
        schema_version=2,
        system=SystemConfig(name="t", domain="t"),
        use_cases=[],
        defaults=Defaults(),
    )
    ev = Eval(id="ev1", tag="right", type="llm", description="d",
              pass_criteria="p", fail_criteria="f")

    rogue = MagicMock()
    rogue.call.return_value = ["not", "a", "dict"]

    with patch("fieldtest.judges.llm.get_provider_adapter", return_value=rogue):
        response = call_judge_llm("prompt", ev, config)

    assert "returned list, expected dict" in response["error"]


# ---------------------------------------------------------------------------
# Models that removed sampling parameters (found by live verification)
# ---------------------------------------------------------------------------

def _temperature_rejected_error():
    class _E(Exception):
        status_code = 400
    return _E(
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': '`temperature` is deprecated for "
        "this model.'}}"
    )


def test_temperature_dropped_and_reported_when_the_model_rejects_it():
    """
    Sampling parameters were removed on Sonnet 5 / Opus 5 / Fable 5 / Opus 4.7+.
    Spec 02 §2.5 says an unsupported parameter is dropped and named, not fatal —
    this used to error every single judge call on those models.
    """
    module, client, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "ok"}'
    )
    client.messages.create.side_effect = [
        _temperature_rejected_error(),
        client.messages.create.return_value,
    ]

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.dict("sys.modules", {"anthropic": module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-sonnet-5", "p", GEN, RETRY)

    assert result["answer"] == "Pass"
    assert result["unsupported"] == ["temperature"]
    # Retried without temperature rather than failing.
    assert "temperature" not in client.messages.create.call_args.kwargs
    assert client.messages.create.call_count == 2


def test_temperature_retry_happens_once_not_per_call():
    """After the first rejection the adapter stops sending it."""
    module, client, _ = _make_anthropic_module(
        returns_text='{"answer": "Pass", "reasoning": "ok"}'
    )
    ok = client.messages.create.return_value
    client.messages.create.side_effect = [_temperature_rejected_error(), ok]

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.dict("sys.modules", {"anthropic": module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            adapter = ant_mod.AnthropicAdapter()
            adapter.call("claude-sonnet-5", "p", GEN, RETRY)

    assert client.messages.create.call_count == 2


def test_other_400s_still_fail_rather_than_retrying_bare():
    """The narrow match must not swallow an unrelated bad request."""
    class _E(Exception):
        status_code = 400

    module, client, _ = _make_anthropic_module(returns_text='{"answer": "Pass"}')
    client.messages.create.side_effect = _E("model not found")

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.dict("sys.modules", {"anthropic": module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("bad-model", "p", GEN, RETRY)

    assert "model not found" in result["error"]
    assert client.messages.create.call_count == 1


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_all_adapters_drop_a_rejected_parameter(provider):
    """
    Provider parameter support changes on their schedule, not ours. Any provider
    that rejects a generation parameter by name must degrade to a run that
    reports the fact, not fail every judge call.
    """
    class _Rejected(Exception):
        status_code = 400

    err = _Rejected("Unsupported parameter: 'temperature' is not supported with this model.")
    result, calls, sleeps = _drive_adapter(
        provider,
        side_effect=[err, DEFAULT],
        returns_text='{"answer": "Pass", "reasoning": "ok"}',
    )

    assert result.get("answer") == "Pass"
    assert result["unsupported"] == ["temperature"]
    assert sleeps == 0        # a refused parameter is not a transient failure


def test_openai_renames_max_tokens_for_reasoning_models():
    """
    Reasoning models reject max_tokens and require max_completion_tokens. It has
    to be renamed, not dropped: spec 02 §2.4 requires every adapter to bound
    output, and an unbounded judge is a worse outcome than a failed one.
    """
    class _Rejected(Exception):
        status_code = 400

    err = _Rejected(
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    )
    result, calls, _ = _drive_adapter(
        "openai",
        side_effect=[err, DEFAULT],
        returns_text='{"answer": "Pass", "reasoning": "ok"}',
    )

    assert result["answer"] == "Pass"
    # A rename is not a capability loss, so it is not reported as unsupported.
    assert "unsupported" not in result


def test_openai_handles_the_value_form_of_the_temperature_rejection():
    """o1-mini phrases it as a value complaint rather than a parameter one."""
    class _Rejected(Exception):
        status_code = 400

    err = _Rejected(
        "Unsupported value: 'temperature' does not support 0.0 with this model. "
        "Only the default (1) value is supported."
    )
    result, _, _ = _drive_adapter(
        "openai",
        side_effect=[err, DEFAULT],
        returns_text='{"answer": "Pass", "reasoning": "ok"}',
    )

    assert result["answer"] == "Pass"
    assert result["unsupported"] == ["temperature"]


def test_gemini_forwards_seed():
    """
    Spec 02's matrix claimed Gemini had no seed parameter, so the adapter
    dropped it and the report named it under "ignored by provider" — a false
    statement. GenerateContentConfig exposes seed; confirmed against the SDK.
    """
    mock_response = MagicMock()
    mock_response.text = '{"answer": "Pass", "reasoning": "ok"}'
    client = MagicMock()
    client.models.generate_content.return_value = mock_response
    genai = MagicMock()
    genai.Client.return_value = client
    google = MagicMock()
    google.genai = genai

    with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
        with patch.dict("sys.modules", {"google": google, "google.genai": genai}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            result = gem_mod.GeminiAdapter().call(
                "gemini-2.5-flash", "p", JudgeGenerationConfig(seed=42), RETRY
            )

    kwargs = genai.types.GenerateContentConfig.call_args.kwargs
    assert kwargs["seed"] == 42
    assert kwargs["temperature"] == 0.0
    assert "unsupported" not in result


def test_gemini_omits_seed_when_unset():
    mock_response = MagicMock()
    mock_response.text = '{"answer": "Pass", "reasoning": "ok"}'
    client = MagicMock()
    client.models.generate_content.return_value = mock_response
    genai = MagicMock()
    genai.Client.return_value = client
    google = MagicMock()
    google.genai = genai

    with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
        with patch.dict("sys.modules", {"google": google, "google.genai": genai}):
            import importlib
            import fieldtest.providers.gemini as gem_mod
            importlib.reload(gem_mod)
            gem_mod.GeminiAdapter().call("gemini-2.5-flash", "p", GEN, RETRY)

    assert "seed" not in genai.types.GenerateContentConfig.call_args.kwargs


# ---------------------------------------------------------------------------
# Documented provider rejections (spec 12 §3) — no network
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rejection",
    __import__("tests.fixtures.provider_errors", fromlist=["REJECTIONS"]).REJECTIONS,
    ids=lambda r: f"{r.provider}-{r.param}",
)
def test_rejects_parameter_matches_every_documented_rejection(rejection):
    """
    The detector is string matching against wording providers change without
    notice. When one rephrases, this test fails and the fixture is where the fix
    goes — rather than the drop path silently ceasing to fire and every judge
    call to that model erroring.
    """
    from fieldtest.providers.base import rejects_parameter

    class _Rejected(Exception):
        status_code = 400

    assert rejects_parameter(_Rejected(rejection.message), rejection.param), (
        f"{rejection.provider} rejection of {rejection.param} no longer matches "
        f"(source: {rejection.source}, confirmed {rejection.confirmed})"
    )


@pytest.mark.parametrize(
    "message",
    __import__("tests.fixtures.provider_errors",
               fromlist=["UNRELATED_BAD_REQUESTS"]).UNRELATED_BAD_REQUESTS,
)
def test_rejects_parameter_ignores_unrelated_bad_requests(message):
    """
    The other half. A 400 that names no parameter must fail on the first attempt
    rather than be retried with fields stripped out one at a time.
    """
    from fieldtest.providers.base import rejects_parameter

    class _Rejected(Exception):
        status_code = 400

    for param in ("temperature", "seed", "max_tokens", "top_p"):
        assert not rejects_parameter(_Rejected(message), param), (
            f"matched {param!r} in an unrelated failure: {message!r}"
        )


def test_rename_is_reported_separately_from_unsupported():
    """
    A renamed parameter is not a capability loss, so it must not appear in
    `unsupported` — that field drives the report line telling a user their judge
    ran without something. It is reported on its own so a rename can be observed
    rather than inferred from the call having succeeded.
    """
    class _Rejected(Exception):
        status_code = 400

    err = _Rejected(
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    )
    result, _, _ = _drive_adapter(
        "openai", side_effect=[err, DEFAULT],
        returns_text='{"answer": "Pass", "reasoning": "ok"}',
    )

    assert result["renamed"] == [("max_tokens", "max_completion_tokens")]
    assert "unsupported" not in result


# ---------------------------------------------------------------------------
# openai_compatible adapter (spec 11)
#
# Adds no reach the environment did not already have — the openai SDK honours
# OPENAI_BASE_URL — but it puts the endpoint in config, and therefore in the
# fingerprint. These tests are about that plumbing, not about network access.
# ---------------------------------------------------------------------------

from fieldtest.config import ProviderSettings
from fieldtest.providers.openai_compatible import OpenAICompatibleAdapter


@needs_openai_pkg
def test_openai_compatible_requires_base_url():
    adapter = OpenAICompatibleAdapter(base_url="", api_key_env=None)
    result = adapter.call("m", "p", GEN, RETRY)
    assert "base_url" in result["error"]


def test_factory_openai_compatible_without_settings_names_the_fix():
    with pytest.raises(ProviderError, match="base_url"):
        get_provider_adapter("openai_compatible")


def test_openai_compatible_works_without_an_api_key(monkeypatch):
    """A self-hosted endpoint may need no key. Absence is configuration."""
    adapter = OpenAICompatibleAdapter(base_url="http://localhost:8000/v1")
    args = adapter._client_args()
    assert args["base_url"] == "http://localhost:8000/v1"
    assert args["api_key"] == "not-required"


def test_api_key_read_from_named_env_var(monkeypatch):
    monkeypatch.setenv("MY_ENDPOINT_KEY", "sk-from-env")
    adapter = OpenAICompatibleAdapter(
        base_url="https://openrouter.ai/api/v1", api_key_env="MY_ENDPOINT_KEY"
    )
    assert adapter._client_args()["api_key"] == "sk-from-env"


@needs_openai_pkg
def test_missing_named_env_var_is_an_error_naming_the_variable(monkeypatch):
    monkeypatch.delenv("MY_ENDPOINT_KEY", raising=False)
    adapter = OpenAICompatibleAdapter(
        base_url="https://openrouter.ai/api/v1", api_key_env="MY_ENDPOINT_KEY"
    )
    result = adapter.call("m", "p", GEN, RETRY)
    assert "MY_ENDPOINT_KEY" in result["error"]


def test_api_key_never_read_from_config_literal():
    """
    ProviderSettings has no field a literal key could land in. A config that
    tries is rejected rather than quietly honoured.
    """
    assert "api_key" not in ProviderSettings.model_fields
    with pytest.raises(Exception):
        ProviderSettings(base_url="http://x/v1", api_key="sk-literal-in-config")


def test_unknown_provider_error_mentions_openai_compatible():
    with pytest.raises(ProviderError, match="openai_compatible"):
        get_provider_adapter("cohere")


def test_unknown_provider_error_mentions_the_decorator():
    with pytest.raises(ProviderError, match="@provider"):
        get_provider_adapter("cohere")


def test_rejected_parameter_dropped_on_a_compatible_endpoint(monkeypatch):
    """
    The drop path is inherited, not reimplemented, so a compatible endpoint
    refusing temperature behaves exactly as OpenAI does.
    """
    monkeypatch.setenv("EP_KEY", "k")
    calls = []

    class _Rejects:
        def __init__(self, **kw):
            self.chat = MagicMock()
            self.chat.completions.create = self._create

        def _create(self, **kwargs):
            calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise ValueError("400: Unsupported parameter: 'temperature'")
            msg = MagicMock()
            msg.message.content = '{"answer": "pass", "reasoning": "ok"}'
            return MagicMock(choices=[msg])

    fake_openai = MagicMock()
    fake_openai.OpenAI = _Rejects
    with patch.dict("sys.modules", {"openai": fake_openai}):
        adapter = OpenAICompatibleAdapter(base_url="http://ep/v1", api_key_env="EP_KEY")
        result = adapter.call("llama", "prompt", GEN, RETRY)

    assert result["answer"] == "pass"
    assert result["unsupported"] == ["temperature"]
    assert "temperature" not in calls[-1]


def test_retry_policy_applies_to_a_compatible_endpoint(monkeypatch):
    """
    A self-hosted endpoint is more likely to be briefly unavailable than a
    hosted one, not less.
    """
    monkeypatch.setenv("EP_KEY", "k")
    attempts = []

    class _Flaky:
        def __init__(self, **kw):
            self.chat = MagicMock()
            self.chat.completions.create = self._create

        def _create(self, **kwargs):
            attempts.append(1)
            if len(attempts) < 2:
                raise fake_openai.APIConnectionError("endpoint down")
            msg = MagicMock()
            msg.message.content = '{"answer": "pass", "reasoning": "ok"}'
            return MagicMock(choices=[msg])

    class _APIConnectionError(Exception):
        pass

    fake_openai = MagicMock()
    fake_openai.OpenAI = _Flaky
    fake_openai.APIConnectionError = _APIConnectionError
    fake_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
    fake_openai.InternalServerError = type("InternalServerError", (Exception,), {})
    fake_openai.RateLimitError = type("RateLimitError", (Exception,), {})

    with patch.dict("sys.modules", {"openai": fake_openai}):
        adapter = OpenAICompatibleAdapter(base_url="http://ep/v1", api_key_env="EP_KEY")
        result = adapter.call("llama", "p", GEN, RetryPolicy(max_retries=2, backoff=[0]))

    assert len(attempts) == 2
    assert result["answer"] == "pass"


@needs_openai_pkg
def test_provider_env_names_match_what_the_adapters_read():
    """
    `fieldtest validate` reports a credential per provider. If that table names
    a different variable than the adapter reads, validate reports a correctly
    configured key as missing — or worse, reports a missing one as present and
    the run fails anyway.

    Asserted against the adapters' own behaviour rather than a second copy of
    the table, so the two cannot drift.
    """
    from fieldtest.cli_common import _PROVIDER_ENV
    from fieldtest.providers import get_provider_adapter

    for provider, declared in _PROVIDER_ENV.items():
        adapter = get_provider_adapter(provider)
        with patch.dict("os.environ", {}, clear=True):
            result = adapter.call("some-model", "prompt", GEN, RETRY)
        assert "error" in result, f"{provider} did not error with no credentials"
        assert declared in result["error"], (
            f"validate says {provider} reads {declared}, but its adapter's error "
            f"names something else: {result['error']!r}"
        )


def test_one_definition_of_the_builtin_provider_names():
    """
    The factory constructs these and config validates against them. Two copies
    would let a new provider be accepted by config and then fail to construct,
    or be constructible and rejected by config.
    """
    from fieldtest.config import VALID_PROVIDERS
    from fieldtest.providers import BUILTIN_PROVIDERS
    from fieldtest.providers.settings import BUILTIN_PROVIDERS as source

    assert BUILTIN_PROVIDERS is source
    assert VALID_PROVIDERS is source


def test_every_builtin_provider_can_actually_be_constructed():
    """A name in the table that the factory cannot build is a config error
    waiting to happen at the first judge call instead of at validation."""
    from fieldtest.config import ProviderSettings
    from fieldtest.providers import BUILTIN_PROVIDERS, get_provider_adapter

    for name in BUILTIN_PROVIDERS:
        settings = (
            ProviderSettings(base_url="http://x/v1")
            if name == "openai_compatible" else None
        )
        assert get_provider_adapter(name, settings) is not None


# ---------------------------------------------------------------------------
# Facts the documentation states, pinned to the code that produces them
#
# Prose restating a constant is the same defect as a second table: the gemini
# credential name was wrong in exactly this way, and nothing noticed because
# nothing tied the claim to the behaviour.
# ---------------------------------------------------------------------------

def test_documented_retry_schedule_is_the_one_the_policy_produces():
    """README, CHANGELOG and spec 05 all state 5/10/20/40/60/60 seconds."""
    from fieldtest.providers.base import RetryPolicy

    policy = RetryPolicy()
    schedule = [policy.delay_for(i) for i in range(policy.max_attempts)]
    assert schedule == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


def test_documented_defaults_are_the_ones_the_config_uses():
    """
    Each of these appears as a literal in README, CHANGELOG or docs/index.html.
    Changing one without updating the prose should fail here rather than
    quietly leaving the docs wrong.
    """
    from fieldtest.config import Defaults

    d = Defaults()
    assert d.model == "claude-haiku-4-5"      # README Install, CHANGELOG judge block
    assert d.provider == "anthropic"
    assert d.runs == 5
    assert d.judge_temperature == 0.0          # "pinned to 0.0 unless you say otherwise"
    assert d.judge_seed is None
    assert d.confidence_level == 0.95          # "defaults.confidence_level sets the level"


def test_documented_judge_fingerprint_matches_the_bundled_demos():
    """
    The CHANGELOG prints fingerprint 4f10569a in its judge block, and the
    bundled demo results were generated under it. If a change to the judge
    fields moves the hash, both the notes and those artifacts go stale.
    """
    from pathlib import Path

    from fieldtest.config import parse_and_validate
    from fieldtest.results.provenance import build_judge_block

    root = Path(__file__).resolve().parent.parent / "fieldtest" / "demo"
    for demo in ("rag", "email", "extraction"):
        judge = build_judge_block(parse_and_validate(root / demo / "config.yaml"))
        assert judge["fingerprint"] == "4f10569a", demo


def test_a_client_library_that_dropped_the_parameter_is_treated_as_a_refusal():
    """
    anthropic 1.2.0 removed `temperature` from messages.create(). fieldtest
    pins `anthropic>=0.20.0` with no upper bound, so a fresh install got the
    new SDK and every Anthropic judge call failed with a TypeError — on the
    default provider, with the default model.

    A library that has dropped a parameter from its own signature is refusing
    it, just earlier and with a TypeError instead of a 400. The drop path
    handles it like any other refusal.
    """
    from fieldtest.providers.base import rejects_parameter

    sdk_error = TypeError(
        "Messages.create() got an unexpected keyword argument 'temperature'"
    )
    assert rejects_parameter(sdk_error, "temperature")

    # Still narrow: the parameter must be named and the message must read as a
    # support complaint, so an unrelated TypeError is not swallowed.
    assert not rejects_parameter(TypeError("bad thing happened"), "temperature")
    assert not rejects_parameter(sdk_error, "seed")


def test_the_drop_path_recovers_from_an_sdk_signature_mismatch():
    """End to end: the call succeeds and names what it dropped."""
    from fieldtest.providers.base import call_dropping_unsupported

    calls = []

    def invoke(kwargs):
        calls.append(dict(kwargs))
        if "temperature" in kwargs:
            raise TypeError(
                "Messages.create() got an unexpected keyword argument 'temperature'"
            )
        return {"ok": True}

    unsupported: list = []
    result = call_dropping_unsupported(
        invoke, {"model": "m", "temperature": 0.0, "max_tokens": 8}, unsupported
    )
    assert result == {"ok": True}
    assert unsupported == ["temperature"]
    assert "temperature" not in calls[-1]
    assert calls[-1]["max_tokens"] == 8


# ---------------------------------------------------------------------------
# A parameter the client library drops but the API still takes
#
# anthropic 1.x removed temperature/top_p/top_k from messages.create(). A fresh
# `pip install fieldtest` gets that SDK, so every default judge ran unpinned —
# reported as unsupported, but unpinned, which is the guarantee spec 02 exists
# to give. Only CI caught it: the repo venv still had anthropic 0.86.
# ---------------------------------------------------------------------------

def test_a_param_the_sdk_drops_is_relocated_not_reported_unsupported():
    from fieldtest.providers.base import call_dropping_unsupported
    from fieldtest.providers.anthropic import RELOCATE_TO_EXTRA_BODY

    seen = []

    def invoke(kwargs):
        seen.append(dict(kwargs))
        if "temperature" in kwargs:
            # Exactly what anthropic 1.2.0 raises: a TypeError from the signature.
            raise TypeError(
                "Messages.create() got an unexpected keyword argument 'temperature'"
            )
        return "ok"

    unsupported, relocated = [], []
    result = call_dropping_unsupported(
        invoke,
        {"model": "m", "max_tokens": 8, "temperature": 0.0},
        unsupported,
        relocate=RELOCATE_TO_EXTRA_BODY,
        relocated=relocated,
    )

    assert result == "ok"
    assert relocated == ["temperature"]
    assert unsupported == [], "a relocated parameter is still in force, not lost"
    assert seen[-1]["extra_body"] == {"temperature": 0.0}
    assert "temperature" not in seen[-1], "it must not be sent both ways"


def test_a_param_the_api_also_refuses_is_dropped_after_the_relocate():
    """Relocating is an attempt, not a guarantee. A real 400 still drops."""
    from fieldtest.providers.base import call_dropping_unsupported
    from fieldtest.providers.anthropic import RELOCATE_TO_EXTRA_BODY

    class Rejected(Exception):
        status_code = 400

    def invoke(kwargs):
        if "temperature" in kwargs:
            raise TypeError(
                "Messages.create() got an unexpected keyword argument 'temperature'"
            )
        if "temperature" in kwargs.get("extra_body", {}):
            raise Rejected("temperature is not supported on this model")
        return "ok"

    unsupported, relocated = [], []
    result = call_dropping_unsupported(
        invoke, {"model": "m", "temperature": 0.0}, unsupported,
        relocate=RELOCATE_TO_EXTRA_BODY, relocated=relocated,
    )

    assert result == "ok"
    assert unsupported == ["temperature"], "the model really did refuse it"
    assert relocated == [], "a relocate that the API then refused is not a relocate"


def test_a_server_side_refusal_is_dropped_without_trying_extra_body():
    """A 400 says the model refused. Retrying it in extra_body just wastes a call."""
    from fieldtest.providers.base import call_dropping_unsupported
    from fieldtest.providers.anthropic import RELOCATE_TO_EXTRA_BODY

    class Rejected(Exception):
        status_code = 400

    calls = []

    def invoke(kwargs):
        calls.append(dict(kwargs))
        if "temperature" in kwargs:
            raise Rejected("temperature is not supported on this model")
        return "ok"

    unsupported = []
    call_dropping_unsupported(
        invoke, {"model": "m", "temperature": 0.0}, unsupported,
        relocate=RELOCATE_TO_EXTRA_BODY,
    )
    assert unsupported == ["temperature"]
    assert not any("extra_body" in c for c in calls), \
        "a server refusal must not be retried through the passthrough"


def test_the_installed_anthropic_sdk_decides_which_path_runs():
    """
    Documents which case this machine exercises, so a green run on an old SDK is
    not mistaken for proof that the new one works.
    """
    import inspect

    import anthropic

    sig = inspect.signature(anthropic.Anthropic(api_key="x").messages.create)
    typed = "temperature" in sig.parameters
    assert "extra_body" in sig.parameters, (
        f"anthropic {anthropic.__version__} has no extra_body; the relocate path "
        f"cannot work and temperature would be dropped"
    )
    if not typed:
        assert anthropic.__version__ >= "1", anthropic.__version__


# ---------------------------------------------------------------------------
# The exception-class lists, against the SDKs that define them
#
# _drive_adapter builds each SDK as a bare MagicMock, and _exception_types
# discards anything that is not a real class — so under it `transient` is empty
# for gemini and holds one class for anthropic, and every retry assertion above
# is decided by the status code alone. Rename the classes in the adapter and the
# suite does not move, while retry on connection and timeout errors — which
# carry no status code at all — silently disappears from the default provider.
#
# These two drive the real SDK module with only its client mocked, so the names
# the adapter passes have to resolve to the classes the SDK actually raises.
# ---------------------------------------------------------------------------

def _real_anthropic_error(name: str):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return getattr(anthropic, name)(request=request)


@needs_httpx
@pytest.mark.parametrize("error_name", ["APIConnectionError", "APITimeoutError"])
def test_anthropic_retries_transport_errors_that_carry_no_status(error_name):
    """
    A dropped connection and a timeout are the transient failures with nothing
    for the status-code check to read. Only the class list saves them.
    """
    import anthropic

    client = MagicMock()
    client.messages.create.side_effect = _real_anthropic_error(error_name)

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
        with patch.object(anthropic, "Anthropic", return_value=client):
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = AnthropicAdapter().call(
                    "claude-haiku-4-5", "test prompt", GEN, RETRY
                )

    assert "error" in result
    assert client.messages.create.call_count == 7   # 1 initial + 6 retries
    assert mock_sleep.call_count == 6


@needs_google_genai
def test_gemini_retries_a_real_server_error_by_class():
    """
    google-genai raises ServerError, and gemini.py names it deliberately —
    APIError is also ClientError's base, so the class list cannot simply widen.
    The code carried here is outside the shared retryable set, so nothing but
    the name recognises it.
    """
    from google import genai
    from google.genai import errors

    client = MagicMock()
    client.models.generate_content.side_effect = errors.ServerError(
        599, {"error": {"message": "backend unavailable"}}
    )

    with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
        with patch.object(genai, "Client", return_value=client):
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = GeminiAdapter().call(
                    "gemini-2.5-flash", "test prompt", GEN, RETRY
                )

    assert "error" in result
    assert client.models.generate_content.call_count == 7
    assert mock_sleep.call_count == 6


@needs_google_genai
def test_a_gemini_client_error_is_not_retried():
    """The reason the list names ServerError rather than its base."""
    from google import genai
    from google.genai import errors

    client = MagicMock()
    client.models.generate_content.side_effect = errors.ClientError(
        401, {"error": {"message": "bad key"}}
    )

    with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
        with patch.object(genai, "Client", return_value=client):
            with patch("fieldtest.providers.base.time.sleep") as mock_sleep:
                result = GeminiAdapter().call(
                    "gemini-2.5-flash", "test prompt", GEN, RETRY
                )

    assert "error" in result
    assert client.models.generate_content.call_count == 1
    assert mock_sleep.call_count == 0
# Packaging and release mechanics (audit findings, 0.3.0)
# ---------------------------------------------------------------------------

import re
import shutil as _shutil
from pathlib import Path

from click.testing import CliRunner

from fieldtest.cli import main as _cli_main

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_keyless_demo_leaves_no_directory_behind(tmp_path, monkeypatch):
    """A live `fieldtest demo` without a key must fail BEFORE copying anything,
    so the suggested `--offline` retry does not hit the dest-exists guard."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = CliRunner().invoke(_cli_main, ["demo"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
    assert not (tmp_path / "fieldtest-demo").exists(), (
        "keyless demo left a half-created fieldtest-demo/ behind"
    )

    # And the printed remedy actually works on the very next command.
    retry = CliRunner().invoke(
        _cli_main, ["demo", "--offline"], catch_exceptions=False
    )
    assert retry.exit_code == 0, retry.output


def test_demo_does_not_copy_pycache_into_the_project(tmp_path, monkeypatch):
    """pip compiles __pycache__ into site-packages at install time; the demo
    copy must not drag it into the user's project (dataset use already
    excludes it)."""
    import fieldtest.cli_project as cli_project

    demo_source = Path(cli_project.__file__).parent / "demo" / "email"
    pycache = demo_source / "__pycache__"
    created = not pycache.exists()
    pycache.mkdir(exist_ok=True)
    (pycache / "rules.cpython-314.pyc").write_bytes(b"\x00")
    try:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            _cli_main, ["demo", "--offline"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        copied = list((tmp_path / "fieldtest-demo").rglob("__pycache__")) + list(
            (tmp_path / "fieldtest-demo").rglob("*.pyc")
        )
        assert copied == [], f"byte-code clutter copied into project: {copied}"
    finally:
        if created:
            _shutil.rmtree(pycache, ignore_errors=True)
        else:
            (pycache / "rules.cpython-314.pyc").unlink(missing_ok=True)


def test_typed_classifier_is_backed_by_a_py_typed_marker():
    """PEP 561: 'Typing :: Typed' is a claim; without a shipped py.typed
    marker, mypy/pyright treat the package as untyped."""
    pyproject = _pyproject()
    classifiers = pyproject["project"]["classifiers"]
    declared = "Typing :: Typed" in classifiers
    marker = (_REPO_ROOT / "fieldtest" / "py.typed").exists()
    # Both directions, unconditionally: the classifier without the marker is a
    # false claim, and the marker without the classifier is a wasted one. As an
    # `if`, dropping the classifier made this test assert nothing.
    assert declared == marker, (
        f"'Typing :: Typed' declared={declared} but fieldtest/py.typed "
        f"exists={marker} — they must agree"
    )


def test_anthropic_floor_is_usable_with_current_httpx():
    """anthropic<0.40 passes `proxies` to httpx.Client, removed in httpx 0.28;
    under default resolution client construction raises TypeError and every
    judge call becomes an errored row. The declared floor must be >=0.40."""
    pyproject = _pyproject()
    deps = pyproject["project"]["dependencies"]
    anthropic_spec = next(d for d in deps if d.startswith("anthropic"))
    m = re.search(r">=\s*(\d+)\.(\d+)", anthropic_spec)
    assert m, f"anthropic dependency has no floor: {anthropic_spec!r}"
    major, minor = int(m.group(1)), int(m.group(2))
    assert (major, minor) >= (0, 40), (
        f"anthropic floor {anthropic_spec!r} is unusable with httpx>=0.28"
    )


def test_publish_workflow_gates_on_tag_version_and_tests():
    """The publish workflow must refuse a tag that does not match the
    pyproject version, and must run the test suite, both before uploading."""
    workflow = (_REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()

    publish_at = workflow.find("gh-action-pypi-publish")
    assert publish_at != -1, "publish step missing from publish.yml"

    tag_check_at = workflow.find("GITHUB_REF_NAME")
    assert tag_check_at != -1, "no tag-vs-version check in publish.yml"
    assert "pyproject" in workflow[max(0, tag_check_at - 500):tag_check_at + 500], (
        "tag check does not compare against the pyproject.toml version"
    )
    assert tag_check_at < publish_at, "tag check must run before publishing"

    test_at = workflow.find("pytest")
    assert test_at != -1, "no test gate in publish.yml"
    assert test_at < publish_at, "test gate must run before publishing"



def _pyproject() -> dict:
    """
    pyproject.toml as a dict.

    tomllib is 3.11+, and this project supports 3.10 — a module-level
    `import tomllib` took the whole test file down there. tomli is not a
    declared dependency, so on 3.10 the few fields these tests read are pulled
    out with a regex rather than adding one.
    """
    import re as _re

    text = (_REPO_ROOT / "pyproject.toml").read_text()
    try:
        import tomllib
        return tomllib.loads(text)
    except ModuleNotFoundError:
        pass

    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    def _list(field):
        m = _re.search(rf"^{field}\s*=\s*\[(.*?)\]", project, _re.S | _re.M)
        return _re.findall(r'"([^"]+)"', m.group(1)) if m else []
    m = _re.search(r'^requires-python\s*=\s*"([^"]+)"', project, _re.M)
    return {"project": {
        "classifiers": _list("classifiers"),
        "dependencies": _list("dependencies"),
        "requires-python": m.group(1) if m else "",
    }}


def test_the_publish_gate_installs_what_the_suite_needs():
    """
    A test step was added to publish.yml to protect the release, and it
    installed only `[dev]` — pytest and pytest-asyncio. Ten provider tests then
    failed on import, so every tag push would have failed the gate and nothing
    would ever have published. The step meant to protect the release blocked it.

    It went unnoticed because the repo's own .venv happens to have openai,
    google-genai and httpx installed.
    """
    import re

    workflow = (_REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()
    installs = re.findall(r"pip install -e \"\.\[([^\]]+)\]\"", workflow)
    assert installs, "publish.yml no longer installs the package for its gate"

    for extras in installs:
        named = {e.strip() for e in extras.split(",")}
        missing = {"openai", "gemini", "dev"} - named
        assert not missing, (
            f"the publish gate installs [{extras}] and omits {sorted(missing)}; "
            f"the provider tests fail on import and no tag ever publishes"
        )


def test_a_dev_only_install_has_no_hard_failures():
    """
    `pip install -e ".[dev]"` is what a contributor runs. Every test that needs
    an optional provider package must skip rather than fail, or their first run
    shows failures that are not theirs.

    Follows one level of helper calls: the first version of this check looked
    only at a test's own body, so a test importing httpx through a helper passed
    with its marker deleted. CI has a dev-only job that checks the real property;
    this catches it at desk speed.
    """
    import ast

    # Any of these markers guards the module; test_live.py names them for the
    # provider ("needs_gemini") rather than for the import ("google").
    NEEDS = {
        "httpx":  ("needs_httpx",),
        "google": ("needs_google_genai", "needs_gemini"),
        "openai": ("needs_openai_pkg", "needs_openai"),
    }

    for path in sorted((_REPO_ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text())
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

        def modules_used(fn, seen=None):
            """Optional modules this function imports, directly or via a helper."""
            seen = seen or set()
            if fn.name in seen:
                return set()
            seen.add(fn.name)
            found = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Import):
                    found |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".")[0])
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    callee = funcs.get(node.func.id)
                    if callee is not None:
                        found |= modules_used(callee, seen)
            return found

        for name, fn in funcs.items():
            if not name.startswith("test_"):
                continue
            decorators = " ".join(ast.unparse(d) for d in fn.decorator_list)
            for module in modules_used(fn) & set(NEEDS):
                markers = NEEDS[module]
                guarded = any(m in decorators for m in markers) or "skipif" in decorators
                assert guarded, (
                    f"{path.name}::{name} reaches {module} without any of "
                    f"{list(markers)}; it will fail rather than skip on a "
                    f"dev-only install"
                )


def test_config_module_survives_pep484_type_comment_parsing():
    """py.typed ships, so downstream mypy reads fieldtest's sources with PEP 484
    type-comment parsing on. A bare `# type: <words>` comment (e.g. the old
    `# type: regex` section markers in config.py) parses as an invalid type
    comment and aborts the whole check — the consumer's own code goes
    unchecked. ast.parse(type_comments=True) applies the same rules."""
    import ast as _ast

    for path in sorted((_REPO_ROOT / "fieldtest").rglob("*.py")):
        # datasets/ holds project templates exec'd from user projects, not
        # modules mypy follows from `import fieldtest`. (expense-report/rules.py
        # currently carries a `type: rule` line inside a commented YAML example
        # that would trip this check — a user running mypy over a copied
        # template hits it, but that is the datasets area's to fix.)
        if "datasets" in path.parts:
            continue
        try:
            _ast.parse(path.read_text(), filename=str(path), type_comments=True)
        except SyntaxError as e:
            raise AssertionError(
                f"{path.relative_to(_REPO_ROOT)}:{e.lineno} breaks PEP 484 "
                f"type-comment parsing ({e.msg}) — reword any bare '# type:' "
                f"comment so typed consumers can run mypy at all"
            ) from None


def test_dependency_floors_are_installable_on_every_claimed_python():
    """The classifiers claim 3.10-3.14. pyyaml 6.0's sdist cannot build under
    Cython 3 and has no wheels for 3.12+; pydantic-core only gains wheels for a
    new CPython in later releases and its sdist needs Rust. A floor below these
    minima makes lowest-resolution installs (uv --resolution lowest,
    constraints files) fail on the very interpreters the package claims.
    Minima verified against PyPI wheel availability (see pyproject comments)."""
    from packaging.requirements import Requirement
    from packaging.version import Version

    pyproject = _pyproject()
    reqs = [Requirement(d) for d in pyproject["project"]["dependencies"]]

    def effective_floor(name: str, python_version: str) -> Version:
        env = {
            "python_version": python_version,
            "python_full_version": python_version + ".0",
        }
        floors = []
        for r in reqs:
            if r.name.lower() != name:
                continue
            if r.marker is not None and not r.marker.evaluate(env):
                continue
            for spec in r.specifier:
                if spec.operator in (">=", "==", "~="):
                    floors.append(Version(spec.version))
        assert floors, f"no applicable floor for {name} on Python {python_version}"
        return max(floors)

    pydantic_minima = {
        "3.10": "2.0",
        "3.11": "2.0",
        "3.12": "2.5",   # first pydantic-core cp312 wheels
        "3.13": "2.9",   # first pydantic-core cp313 wheels
        "3.14": "2.12",  # first pydantic-core cp314 wheels
    }
    for py, minimum in pydantic_minima.items():
        floor = effective_floor("pydantic", py)
        assert floor >= Version(minimum), (
            f"pydantic floor {floor} is not installable on Python {py}; "
            f"needs >={minimum}"
        )
        yaml_floor = effective_floor("pyyaml", py)
        assert yaml_floor >= Version("6.0.1"), (
            f"pyyaml floor {yaml_floor} on Python {py}: 6.0's sdist cannot "
            f"build under Cython 3 and has no wheels for 3.12+"
        )


def test_changelog_documents_the_clean_data_loss_fixes():
    """The project's release-notes bar is 'what stopped going wrong'. The
    clean guards (refusing non-fieldtest projects, honest deletion prompts,
    --results scoped to known artifacts) are the most user-protective fixes in
    0.3.0 and shipped undocumented — this pins their presence."""
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text()
    section = changelog.split("## Changes from v0.2.2")[0]
    assert re.search(r"`fieldtest clean`", section), (
        "CHANGELOG's 0.3.0 notes never mention the fieldtest clean "
        "data-loss fixes"
    )
    assert "refuses" in section and "fieldtest project" in section, (
        "CHANGELOG should say clean now refuses to run outside a "
        "fieldtest project"
    )
