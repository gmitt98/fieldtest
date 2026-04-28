"""
tests/test_providers.py

Tests for provider adapters and factory.
"""
from unittest.mock import MagicMock, patch

import pytest

from fieldtest.errors import ProviderError
from fieldtest.providers import get_provider_adapter
from fieldtest.providers.anthropic import AnthropicAdapter
from fieldtest.providers.gemini import GeminiAdapter
from fieldtest.providers.openai import OpenAIAdapter


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
    adapter = OpenAIAdapter()

    mock_openai_module = MagicMock()

    with patch.dict("os.environ", {}, clear=True):
        with patch.dict("sys.modules", {"openai": mock_openai_module}):
            import importlib
            import fieldtest.providers.openai as oai_mod
            importlib.reload(oai_mod)
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt")
    assert "error" in result
    assert "OPENAI_API_KEY" in result["error"]


def test_openai_missing_package():
    adapter = OpenAIAdapter()
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"openai": None}):
            result = adapter.call("gpt-4o", "test prompt")
    assert "error" in result
    assert "openai" in result["error"].lower()


def test_openai_successful_call():
    adapter = OpenAIAdapter()

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
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt")

    assert result == {"answer": "Pass", "reasoning": "Looks good"}


def test_openai_strips_markdown_fences():
    adapter = OpenAIAdapter()

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
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt")

    assert result == {"answer": "Fail", "reasoning": "Bad"}


def test_openai_non_json_response():
    adapter = OpenAIAdapter()

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
            result = oai_mod.OpenAIAdapter().call("gpt-4o", "test prompt")

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
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt")
    assert "error" in result
    assert "GEMINI_API_KEY" in result["error"]


def test_gemini_missing_package():
    adapter = GeminiAdapter()
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            result = adapter.call("gemini-2.5-flash", "test prompt")
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
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt")

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
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt")

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
            result = gem_mod.GeminiAdapter().call("gemini-2.5-flash", "test prompt")

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
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")
    assert "error" in result
    assert "ANTHROPIC_API_KEY" in result["error"]


def test_anthropic_missing_package():
    adapter = AnthropicAdapter()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": None}):
            result = adapter.call("claude-haiku-4-5", "test prompt")
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
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

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
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

    assert result == {"answer": "Fail", "reasoning": "Bad"}


def test_anthropic_non_json_response():
    mock_anthropic_module, _, _ = _make_anthropic_module(returns_text="This is not JSON")

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            import importlib
            import fieldtest.providers.anthropic as ant_mod
            importlib.reload(ant_mod)
            result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

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
            with patch.object(ant_mod.time, "sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

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
            with patch.object(ant_mod.time, "sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

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
            with patch.object(ant_mod.time, "sleep") as mock_sleep:
                result = ant_mod.AnthropicAdapter().call("claude-haiku-4-5", "test prompt")

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
