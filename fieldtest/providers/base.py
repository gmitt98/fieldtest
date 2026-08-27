"""
fieldtest/providers/base.py

Abstract ProviderAdapter base class and judge generation config.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from pydantic import BaseModel


class JudgeGenerationConfig(BaseModel):
    """
    Generation settings for a judge call.

    Defaults ship the instrument locked: temperature 0.0 rather than the
    provider default (typically 1.0), so two runs over the same outputs ask
    the judge the same question under the same conditions. A user who wants
    sampling noise asks for it explicitly via defaults.judge_temperature.
    """
    temperature: float = 0.0
    seed:        Optional[int] = None
    max_tokens:  int = 2048


class RetryPolicy(BaseModel):
    """
    Shared transient-failure policy for every provider.

    max_attempts is the number of RETRIES after the initial call, so the
    defaults reproduce the original Anthropic schedule exactly:
    5, 10, 20, 40, 60, 60 — seven calls, six waits.
    """
    max_attempts:  int   = 6
    initial_delay: float = 5.0
    max_delay:     float = 60.0
    multiplier:    float = 2.0

    def delay_for(self, attempt: int) -> float:
        """Capped exponential backoff for a zero-indexed retry number."""
        return min(self.initial_delay * (self.multiplier ** attempt), self.max_delay)


# Transient HTTP conditions, shared across providers: rate limit, server errors,
# and Anthropic's 529 overload. Anything else — 401 auth, 404 bad model — is a
# standing condition that retrying cannot fix.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})


def _status_code(e: BaseException) -> Optional[int]:
    """HTTP status carried by an SDK exception, under whichever name it uses."""
    for attr in ("status_code", "code", "status"):
        value = getattr(e, attr, None)
        if isinstance(value, int):
            return value
    return None


def _exception_types(module: Any, *names: str) -> tuple:
    """
    Real exception classes from an SDK module, by name.
    Names the SDK does not define are skipped, as are test doubles that are not
    actual types — so isinstance() is never handed something it cannot use.
    """
    found = []
    for name in names:
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            found.append(candidate)
    return tuple(found)


def make_is_retryable(*modules_and_names) -> Callable[[BaseException], bool]:
    """
    Build a provider's is_retryable from (module, *exception_names) pairs plus
    the shared status-code set.
    """
    transient: tuple = ()
    for module, names in modules_and_names:
        transient += _exception_types(module, *names)

    def is_retryable(e: BaseException) -> bool:
        if transient and isinstance(e, transient):
            return True
        code = _status_code(e)
        return code is not None and code in RETRYABLE_STATUS_CODES

    return is_retryable


# Generation parameters a provider may reject outright. Anthropic removed
# sampling on Sonnet 5 / Opus 5 / Fable 5 / Opus 4.7+; OpenAI's reasoning models
# are reported to do the same. Rather than track every provider's rules — which
# change on their schedule, not ours — detect the rejection and degrade.
DROPPABLE_PARAMS = ("temperature", "seed", "top_p", "top_k")

_REJECTION_MARKERS = (
    "deprecated",
    "not supported",
    "unsupported",
    "not permitted",
    "cannot be used",
    "does not support",
)


def rejects_parameter(e: BaseException, name: str) -> bool:
    """
    Whether a provider refused the request because of one named generation
    parameter, rather than for some other reason.

    Matched on the message because providers report this as a generic bad
    request. Deliberately narrow on both halves — the parameter has to be named
    AND the message has to read as a support complaint — so an unrelated 400
    still fails on the first attempt instead of being retried with fields
    silently removed.
    """
    code = _status_code(e)
    if code is not None and not (400 <= code < 500):
        return False
    text = str(e).lower()
    return name in text and any(marker in text for marker in _REJECTION_MARKERS)


def call_dropping_unsupported(
    invoke: Callable[[dict], Any],
    kwargs: dict,
    unsupported: list,
    droppable: tuple = DROPPABLE_PARAMS,
    renames: Optional[dict] = None,
) -> Any:
    """
    Call invoke(kwargs), dropping any generation parameter the provider rejects
    by name and retrying, until it accepts the request or fails for some other
    reason.

    Spec 02 §2.5: where a provider does not support a requested parameter, the
    adapter ignores it and records the fact once per run rather than failing.
    Names collected here reach the report header, so a judge running without the
    parameters you asked for says so instead of looking pinned.

    `renames` handles the case where a provider still accepts the capability
    under a different key — OpenAI's reasoning models reject `max_tokens` and
    require `max_completion_tokens`. That is a rename, not a loss, so it is not
    reported as unsupported: dropping it would leave the judge unbounded, which
    spec 02 §2.4 requires every adapter to prevent.
    """
    renames  = renames or {}
    attempt  = dict(kwargs)

    while True:
        try:
            return invoke(attempt)
        except Exception as e:
            renamed = next(
                (p for p in renames if p in attempt and rejects_parameter(e, p)), None
            )
            if renamed is not None:
                attempt[renames[renamed]] = attempt.pop(renamed)
                continue

            dropped = next(
                (p for p in droppable if p in attempt and rejects_parameter(e, p)),
                None,
            )
            if dropped is None:
                raise
            attempt.pop(dropped)
            if dropped not in unsupported:
                unsupported.append(dropped)


def with_retry(
    fn: Callable[[], dict],
    policy: RetryPolicy,
    is_retryable: Callable[[BaseException], bool],
) -> dict:
    """
    Run fn, retrying on retryable exceptions with capped exponential backoff.
    Returns fn's dict on success, or {"error": str} after exhausting attempts.
    Never raises.

    Errors fn returns as a dict (a non-JSON judge response, say) are answers,
    not failures, and are passed straight through without retry.
    """
    last: Optional[BaseException] = None

    for attempt in range(policy.max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — adapters must never raise
            last = e
            if attempt < policy.max_attempts and is_retryable(e):
                time.sleep(policy.delay_for(attempt))
                continue
            return {"error": str(e)}

    return {"error": str(last)}


class ProviderAdapter(ABC):
    @abstractmethod
    def call(
        self,
        model: str,
        prompt: str,
        gen: JudgeGenerationConfig,
        retry: RetryPolicy,
    ) -> dict:
        """
        Call the LLM and return parsed JSON dict.
        Returns {"error": str} on failure — never raises.
        Expected keys in successful response: "answer"/"score" + "reasoning".

        Ignores parameters in `gen` the provider does not support, and names
        them in an optional "unsupported" list on the successful return rather
        than failing. call_judge_llm() collects those for the report header.

        Applies `retry` to transient failures, so an overloaded provider does
        not silently shrink the sample by erroring out of the denominator.
        """
        ...


# ---------------------------------------------------------------------------
# Judge response parsing
# ---------------------------------------------------------------------------

def _strip_code_fences(content: str) -> str:
    """Some models (e.g. Haiku) wrap JSON in markdown code fences."""
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        content = "\n".join(lines).strip()
    return content


def _iter_top_level_objects(content: str):
    """
    Yield each balanced top-level {...} span in content.
    String contents and escapes are respected, so braces inside a JSON string
    do not open or close a span.
    """
    depth = 0
    start = None
    in_string = False
    escaped = False

    for i, ch in enumerate(content):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield content[start : i + 1]
                    start = None


def _parse_last_json_object(content: str) -> dict:
    """
    Scan for balanced top-level JSON objects and return the last one that parses.
    Raises json.JSONDecodeError if none parse, preserving the existing
    "Judge returned non-JSON response" error path.

    Binding to the last object matters because the judge's own verdict comes
    last. An output that echoes a verdict before the judge produces one must not
    be read as the judge's answer.
    """
    content = _strip_code_fences(content).strip()

    candidates = list(_iter_top_level_objects(content))
    for span in reversed(candidates):
        try:
            parsed = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    # Nothing balanced parsed. Let json raise on the whole string so the
    # adapter's existing error message and error path are unchanged — and hold
    # the return type to dict, because every caller indexes into it. A judge
    # answering with a bare scalar or an object-free array is a malformed
    # verdict, which is the same failure as unparseable text, not a crash.
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError(
            f"expected a JSON object, got {type(parsed).__name__}", content, 0
        )
    return parsed
