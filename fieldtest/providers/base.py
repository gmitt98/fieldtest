"""
fieldtest/providers/base.py

Abstract ProviderAdapter base class and judge generation config.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Optional

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


class ProviderAdapter(ABC):
    @abstractmethod
    def call(self, model: str, prompt: str, gen: JudgeGenerationConfig) -> dict:
        """
        Call the LLM and return parsed JSON dict.
        Returns {"error": str} on failure — never raises.
        Expected keys in successful response: "answer"/"score" + "reasoning".

        Ignores parameters in `gen` the provider does not support, and names
        them in an optional "unsupported" list on the successful return rather
        than failing. call_judge_llm() collects those for the report header.
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

    # Nothing balanced parsed — let json raise on the whole string so the
    # adapter's existing error message and error path are unchanged.
    return json.loads(content)
