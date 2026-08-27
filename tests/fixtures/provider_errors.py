"""
tests/fixtures/provider_errors.py

Provider rejection messages, as data rather than as assertions scattered through
tests (spec 12 §3).

fieldtest does not keep a table of which model supports which parameter — that
table was wrong twice in one day, so support is discovered at runtime. What it
does depend on is `rejects_parameter()` recognising a refusal when it sees one,
and that recognition is string matching against wording providers choose and
change without notice.

Keeping the strings here with a source and a confirmation date makes drift
visible in review. When a provider rephrases, this file is where the fix goes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rejection:
    provider: str
    param: str
    message: str
    source: str
    confirmed: str          # ISO date this wording was last seen
    observed_live: bool     # True if fieldtest has seen this exact shape from the API


REJECTIONS = [
    Rejection(
        provider="anthropic",
        param="temperature",
        message="`temperature` is deprecated for this model.",
        source="Anthropic API, claude-sonnet-5 — observed in a live calibration run",
        confirmed="2026-08-26",
        observed_live=True,
    ),
    Rejection(
        provider="openai",
        param="temperature",
        message="Unsupported parameter: 'temperature' is not supported with this model.",
        source="https://platform.openai.com/docs/guides/reasoning",
        confirmed="2026-08-26",
        observed_live=True,
    ),
    Rejection(
        provider="openai",
        param="temperature",
        message=(
            "Unsupported value: 'temperature' does not support 0.0 with this model. "
            "Only the default (1) value is supported."
        ),
        source="OpenAI o1-mini — documented value-form of the same refusal",
        confirmed="2026-08-26",
        observed_live=False,
    ),
    Rejection(
        provider="openai",
        param="max_tokens",
        message=(
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead."
        ),
        source="https://platform.openai.com/docs/guides/reasoning",
        confirmed="2026-08-26",
        observed_live=True,
    ),
    # Gemini's `seed` rejection is deliberately absent. A live probe against
    # gemini-3.7-flash returned unsupported: ['seed'], which proves
    # rejects_parameter() matched something — but the message itself was never
    # captured, and inventing a plausible one would put a string in this file
    # that no provider ever sent. test_live_capture_rejection_wording exists to
    # record it the next time the live tier runs.
]

# Bad requests that name no generation parameter. rejects_parameter() must NOT
# match these, or an unrelated failure gets retried with fields silently removed.
UNRELATED_BAD_REQUESTS = [
    "model not found",
    "Error code: 404 - {'error': {'message': 'The model `nope` does not exist'}}",
    "Incorrect API key provided",
    "This model models/gemini-2.5-flash is no longer available to new users.",
    "Rate limit reached for requests",
]
