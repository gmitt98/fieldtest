"""
fieldtest/providers/openai.py

OpenAIAdapter — judge LLM provider for OpenAI models.
Reads OPENAI_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import (
    JudgeGenerationConfig,
    ProviderAdapter,
    _parse_last_json_object,
)


class OpenAIAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str, gen: JudgeGenerationConfig) -> dict:
        """
        Call OpenAI API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Supports temperature, seed, and max_tokens.
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
            kwargs = {
                "model":       model,
                "max_tokens":  gen.max_tokens,
                "temperature": gen.temperature,
                "messages":    [{"role": "user", "content": prompt}],
            }
            if gen.seed is not None:
                kwargs["seed"] = gen.seed
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content.strip()
            return _parse_last_json_object(content)
        except json.JSONDecodeError as e:
            return {"error": f"Judge returned non-JSON response: {e}"}
        except Exception as e:
            return {"error": str(e)}
