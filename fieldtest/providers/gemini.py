"""
fieldtest/providers/gemini.py

GeminiAdapter — judge LLM provider for Google Gemini models.
Reads GEMINI_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import JudgeGenerationConfig, ProviderAdapter


class GeminiAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str, gen: JudgeGenerationConfig) -> dict:
        """
        Call Gemini API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Gemini has no seed parameter in this contract; a requested seed is
        dropped and reported in "unsupported".
        """
        unsupported = ["seed"] if gen.seed is not None else []
        try:
            from google import genai as _genai
            from google.genai import types as _genai_types
        except ImportError as e:
            return {
                "error": (
                    f"google-genai package not installed: {e}\n"
                    f"  Install with: pip install fieldtest[gemini]"
                )
            }

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY not set in environment"}

        try:
            client = _genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_genai_types.GenerateContentConfig(
                    temperature=gen.temperature,
                    max_output_tokens=gen.max_tokens,
                ),
            )
            content = response.text.strip()
            # Strip markdown code fences if present.
            if content.startswith("```"):
                lines = content.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                content = "\n".join(lines).strip()
            parsed = json.loads(content)
            if unsupported:
                parsed["unsupported"] = unsupported
            return parsed
        except json.JSONDecodeError as e:
            return {"error": f"Judge returned non-JSON response: {e}"}
        except Exception as e:
            return {"error": str(e)}
