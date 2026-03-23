"""
fieldtest/providers/__init__.py

get_provider_adapter() — factory that returns the correct adapter.
Raises ProviderError for unknown providers.
"""
from __future__ import annotations

from fieldtest.errors import ProviderError
from fieldtest.providers.base import ProviderAdapter


def get_provider_adapter(provider: str) -> ProviderAdapter:
    """
    Return the appropriate provider adapter.
    Raises ProviderError if provider is unknown.
    """
    if provider == "anthropic":
        from fieldtest.providers.anthropic import AnthropicAdapter
        return AnthropicAdapter()

    raise ProviderError(
        f"Unknown provider '{provider}'. v1 supports: anthropic\n"
        f"  Check defaults.provider in evals/config.yaml"
    )
