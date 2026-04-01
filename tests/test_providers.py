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
