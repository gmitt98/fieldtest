"""
fieldtest/providers/anthropic.py

AnthropicAdapter — v1 judge LLM provider.
Reads ANTHROPIC_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str) -> dict:
        """
        Call Anthropic API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        """
        try:
            import anthropic as _anthropic
        except ImportError as e:
            return {"error": f"anthropic package not installed: {e}"}

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY not set in environment"}

        try:
            client = _anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content[0].text.strip()
            return json.loads(content)
        except json.JSONDecodeError as e:
            return {"error": f"Judge returned non-JSON response: {e}"}
        except Exception as e:
            return {"error": str(e)}
