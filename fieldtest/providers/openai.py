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
    RetryPolicy,
    _parse_last_json_object,
    call_dropping_unsupported,
    make_is_retryable,
    with_retry,
)


class OpenAIAdapter(ProviderAdapter):
    def call(
        self,
        model: str,
        prompt: str,
        gen: JudgeGenerationConfig,
        retry: RetryPolicy,
    ) -> dict:
        """
        Call OpenAI API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Supports temperature, seed, and max_tokens.
        Retries rate limits, server errors, and connection timeouts.
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
        except Exception as e:
            return {"error": str(e)}

        unsupported: list[str] = []
        kwargs = {
            "model":       model,
            "max_tokens":  gen.max_tokens,
            "temperature": gen.temperature,
            "messages":    [{"role": "user", "content": prompt}],
        }
        if gen.seed is not None:
            kwargs["seed"] = gen.seed

        def _once() -> dict:
            # Reasoning models reject sampling parameters; drop what this model
            # refuses rather than erroring every judge call.
            response = call_dropping_unsupported(
                lambda k: client.chat.completions.create(**k),
                kwargs,
                unsupported,
                # Reasoning models (o1, o3, o4, GPT-5.x) reject max_tokens and
                # require max_completion_tokens. Renaming keeps the output bound
                # that spec 02 §2.4 requires; dropping it would remove it.
                renames={"max_tokens": "max_completion_tokens"},
            )
            content = response.choices[0].message.content.strip()
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
            make_is_retryable((
                _openai,
                ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"),
            )),
        )
