"""fieldtest — structured AI eval practice for any project."""

from fieldtest.judges.registry import rule
from fieldtest.providers.registry import provider, register_provider

__all__ = ["provider", "register_provider", "rule"]
