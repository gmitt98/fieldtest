"""
fieldtest/providers/gemini.py

GeminiAdapter — judge LLM provider for Google Gemini models.
Reads GEMINI_API_KEY from env.
"""
from __future__ import annotations

import json
import os

from fieldtest.providers.base import (
    JudgeGenerationConfig,
    ProviderAdapter,
    RetryPolicy,
    _parse_last_json_object,
    call_dropping_unsupported,
    make_is_retryable,
    with_retry,
)


class GeminiAdapter(ProviderAdapter):
    def call(
        self,
        model: str,
        prompt: str,
        gen: JudgeGenerationConfig,
        retry: RetryPolicy,
    ) -> dict:
        """
        Call Gemini API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Supports temperature, seed and max_output_tokens. Retries rate limits
        and server errors.
        """
        unsupported: list[str] = []
        try:
            from google import genai as _genai
            from google.genai import errors as _genai_errors
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
        except Exception as e:
            return {"error": str(e)}

        def _once() -> dict:
            # Config fields are nested for Gemini, so the shared dropper works
            # on the outer kwargs and the config is rebuilt from what survives.
            def _invoke(k: dict):
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_genai_types.GenerateContentConfig(
                        max_output_tokens=gen.max_tokens,
                        **({"temperature": k["temperature"]} if "temperature" in k else {}),
                        **({"seed": k["seed"]} if "seed" in k else {}),
                    ),
                )

            params = {"temperature": gen.temperature}
            if gen.seed is not None:
                params["seed"] = gen.seed

            response = call_dropping_unsupported(_invoke, params, unsupported)
            content = response.text.strip()
            try:
                parsed = _parse_last_json_object(content)
            except json.JSONDecodeError as e:
                # A malformed verdict is an answer, not a transient failure.
                return {"error": f"Judge returned non-JSON response: {e}"}
            if unsupported:
                parsed["unsupported"] = unsupported
            return parsed

        return with_retry(
            _once,
            retry,
            # ServerError only: APIError is also the base of ClientError, so treating it
            # as transient would retry 401s and bad model names forever. 429 still
            # retries — it arrives with a status code the shared check recognizes.
            make_is_retryable((_genai_errors, ("ServerError",))),
        )
