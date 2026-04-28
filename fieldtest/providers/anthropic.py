"""
fieldtest/providers/anthropic.py

AnthropicAdapter — v1 judge LLM provider.
Reads ANTHROPIC_API_KEY from env.
"""
from __future__ import annotations

import json
import os
import time

from fieldtest.providers.base import ProviderAdapter

# Backoff schedule for HTTP 529 OverloadedError. The Anthropic SDK does not
# auto-retry 529s (unlike 429s), so without this every burst of API load
# turns into judge errors that silently drop out of the pass-rate denominator.
_OVERLOAD_BACKOFF_SECONDS = (5, 10, 20, 40, 60, 60)


class AnthropicAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str) -> dict:
        """
        Call Anthropic API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Retries HTTP 529 (OverloadedError) with exponential backoff.
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
        except Exception as e:
            return {"error": str(e)}

        last_overload: Exception | None = None
        for attempt in range(len(_OVERLOAD_BACKOFF_SECONDS) + 1):
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = message.content[0].text.strip()
                # Some models (e.g. Haiku) wrap JSON in markdown code fences.
                # Strip them before parsing so json.loads() doesn't fail.
                if content.startswith("```"):
                    lines = content.split("\n")
                    lines = [l for l in lines if not l.startswith("```")]
                    content = "\n".join(lines).strip()
                return json.loads(content)
            except _anthropic.APIStatusError as e:
                if getattr(e, "status_code", None) == 529 and attempt < len(_OVERLOAD_BACKOFF_SECONDS):
                    last_overload = e
                    time.sleep(_OVERLOAD_BACKOFF_SECONDS[attempt])
                    continue
                return {"error": str(e)}
            except json.JSONDecodeError as e:
                return {"error": f"Judge returned non-JSON response: {e}"}
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"OverloadedError: exhausted retries — {last_overload}"}
