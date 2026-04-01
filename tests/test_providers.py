"""
tests/test_providers.py

Tests for provider adapters and factory.
"""
from unittest.mock import MagicMock, patch

import pytest

from fieldtest.errors import ProviderError
from fieldtest.providers import get_provider_adapter
from fieldtest.providers.anthropic import AnthropicAdapter
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


def test_factory_unknown_provider():
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider_adapter("gemini")


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
