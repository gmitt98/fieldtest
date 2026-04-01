"""
fieldtest/providers/openai.py

OpenAIAdapter — judge LLM provider for OpenAI models.
Reads OPENAI_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str) -> dict:
        """
        Call OpenAI API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        """
        try:
            import openai as _openai
        except ImportError as e:
            return {
                "error": (
                    f"openai package not installed: {e}\n"
                    f"  Install with: pip install fieldtest[openai]"
                )
            }

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"error": "OPENAI_API_KEY not set in environment"}

        try:
            client = _openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content.strip()
            # Strip markdown code fences if present.
            if content.startswith("```"):
                lines = content.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                content = "\n".join(lines).strip()
            return json.loads(content)
        except json.JSONDecodeError as e:
            return {"error": f"Judge returned non-JSON response: {e}"}
        except Exception as e:
            return {"error": str(e)}
