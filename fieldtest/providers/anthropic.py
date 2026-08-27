"""
fieldtest/providers/anthropic.py

AnthropicAdapter — v1 judge LLM provider.
Reads ANTHROPIC_API_KEY from env.
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


class AnthropicAdapter(ProviderAdapter):
    def call(
        self,
        model: str,
        prompt: str,
        gen: JudgeGenerationConfig,
        retry: RetryPolicy,
    ) -> dict:
        """
        Call Anthropic API with prompt. Returns parsed JSON dict.
        Returns {"error": str} on any failure — never raises.
        Anthropic has no seed parameter; a requested seed is dropped and
        reported in "unsupported".

        Retries rate limits, server errors, and HTTP 529. The SDK does not
        auto-retry 529s (unlike 429s), so without this a burst of API load turns
        into judge errors that silently drop out of the pass-rate denominator.
        """
        unsupported = ["seed"] if gen.seed is not None else []

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

        # Sampling parameters were removed on the newest models (Sonnet 5, Opus 5,
        # Fable 5, Opus 4.7/4.8): sending temperature returns 400. Spec 02 §2.5
        # already says the right thing to do — drop what the provider does not
        # support, complete the run, and name it once — so this joins seed rather
        # than failing every call.
        kwargs = {
            "model": model,
            "max_tokens": gen.max_tokens,
            "temperature": gen.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        def _once() -> dict:
            message = call_dropping_unsupported(
                lambda k: client.messages.create(**k), kwargs, unsupported
            )
            content = message.content[0].text.strip()
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
                _anthropic,
                ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"),
            )),
        )
