"""
fieldtest/providers/base.py

Abstract ProviderAdapter base class.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    @abstractmethod
    def call(self, model: str, prompt: str) -> dict:
        """
        Call the LLM and return parsed JSON dict.
        Returns {"error": str} on failure — never raises.
        Expected keys in successful response: "answer"/"score" + "reasoning".
        """
        ...
