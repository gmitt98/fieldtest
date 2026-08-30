"""
fieldtest/providers/openai_compatible.py

OpenAICompatibleAdapter — any endpoint speaking the OpenAI chat-completions
protocol: vLLM, Ollama, Together, Fireworks, OpenRouter, xAI.

This adds no reach that the environment did not already have. The openai SDK
honours OPENAI_BASE_URL, so OpenAIAdapter already talks to these endpoints.
What it adds is that the endpoint is named in config.yaml: versioned with the
rest of the config, visible to a reader, and in the judge fingerprint. Two runs
against the same model on different endpoints are not the same instrument, and
a shell variable should not be the only record of which one judged.

The request shape and the drop-and-rename path are inherited from OpenAIAdapter
rather than reimplemented, so a rejected parameter behaves identically here.
"""
from __future__ import annotations

import os
from typing import Optional

from fieldtest.providers.openai import OpenAIAdapter


class OpenAICompatibleAdapter(OpenAIAdapter):
    def __init__(self, base_url: str, api_key_env: Optional[str] = None):
        self.base_url    = base_url
        self.api_key_env = api_key_env

    def _client_args(self) -> dict | str:
        """
        Connection settings, or an error string.

        A self-hosted endpoint may need no key at all, so a missing api_key_env
        is a valid configuration. The openai SDK still requires the field to be
        set to something, hence the placeholder.
        """
        if not self.base_url:
            return "openai_compatible requires providers.openai_compatible.base_url"

        if self.api_key_env is None:
            return {"api_key": "not-required", "base_url": self.base_url}

        key = os.environ.get(self.api_key_env)
        if not key:
            return (
                f"{self.api_key_env} not set in environment\n"
                f"  Named by providers.openai_compatible.api_key_env in config.yaml"
            )
        return {"api_key": key, "base_url": self.base_url}
