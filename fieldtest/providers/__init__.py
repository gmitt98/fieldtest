"""
fieldtest/providers/__init__.py

get_provider_adapter() — factory that returns the correct adapter.

Resolution order: built-in names, then openai_compatible (which needs the
settings block naming its endpoint), then anything a user registered with
@provider. Raises ProviderError if none match.
"""
from __future__ import annotations

from typing import Optional

from fieldtest.errors import ProviderError
from fieldtest.providers.base import ProviderAdapter

BUILTIN_PROVIDERS = {"anthropic", "gemini", "openai", "openai_compatible"}


def get_provider_adapter(
    provider: str,
    settings: Optional[object] = None,
) -> ProviderAdapter:
    """
    Return the appropriate provider adapter.

    `settings` is the ProviderSettings entry for this provider from the config's
    `providers` block, required for openai_compatible and ignored otherwise.
    Raises ProviderError if provider is unknown.
    """
    if provider == "anthropic":
        from fieldtest.providers.anthropic import AnthropicAdapter
        return AnthropicAdapter()

    if provider == "openai":
        from fieldtest.providers.openai import OpenAIAdapter
        return OpenAIAdapter()

    if provider == "gemini":
        from fieldtest.providers.gemini import GeminiAdapter
        return GeminiAdapter()

    if provider == "openai_compatible":
        from fieldtest.providers.openai_compatible import OpenAICompatibleAdapter
        if settings is None:
            raise ProviderError(
                "Provider 'openai_compatible' needs a base_url.\n"
                "  Add to evals/config.yaml:\n"
                "    providers:\n"
                "      openai_compatible:\n"
                "        base_url: https://openrouter.ai/api/v1\n"
                "        api_key_env: OPENROUTER_API_KEY"
            )
        return OpenAICompatibleAdapter(
            base_url=settings.base_url,
            api_key_env=settings.api_key_env,
        )

    from fieldtest.providers.registry import get_registered_provider
    registered = get_registered_provider(provider)
    if registered is not None:
        return registered

    supported = ", ".join(sorted(BUILTIN_PROVIDERS))
    raise ProviderError(
        f"Unknown provider '{provider}'. Supported: {supported}\n"
        f"  Check defaults.provider in evals/config.yaml\n"
        f"  For an endpoint speaking the OpenAI chat-completions protocol\n"
        f"  (vLLM, Ollama, OpenRouter, Together, Fireworks, xAI), use\n"
        f"  'openai_compatible' with a base_url. For anything else, register\n"
        f"  an adapter with @provider in evals/providers.py."
    )
