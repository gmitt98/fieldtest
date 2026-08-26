"""
fieldtest/providers/base.py

Abstract ProviderAdapter base class and judge generation config.
"""
from __future__ import annotations

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
