"""
fieldtest/providers/gemini.py

GeminiAdapter — judge LLM provider for Google Gemini models.
Reads GEMINI_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import ProviderAdapter


class GeminiAdapter(ProviderAdapter):
    def call(self, model: str, prompt: str) -> dict:
        """
        Call Gemini API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        """
        try:
            from google import genai as _genai
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
            )
            content = response.text.strip()
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
