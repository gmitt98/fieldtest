"""
fieldtest/providers/registry.py

@provider decorator + _provider_registry + load_providers().

Mirrors the @rule registry: user code lives in a conventional file next to
config.yaml, is imported once, and registers by name. A registered provider is
a valid value for defaults.provider, for a per-eval override, and for a
calibration panel entry, with no further plumbing.

fieldtest does not have to predict which provider a user needs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from fieldtest.loader import import_user_file
from fieldtest.providers.base import (
    JudgeGenerationConfig,
    ProviderAdapter,
    RetryPolicy,
)

# Module-level registry: {provider_name: callable or ProviderAdapter}
_provider_registry: dict[str, object] = {}

_loaded_provider_files: set[str] = set()


def provider(name: str) -> Callable:
    """
    Register a judge provider by name.

    Usage in the user's evals/providers.py:

        from fieldtest import provider

        @provider("my-inference-service")
        def call(model, prompt, gen, retry) -> dict:
            '''Return the judge's parsed JSON dict, or {"error": str}.'''
            ...

    The signature is the same shape as ProviderAdapter.call() rather than a new
    one. A user who outgrows the decorator registers a ProviderAdapter instance
    instead; a user who never does is not asked to learn a class hierarchy to
    make one HTTP call.
    """
    def decorator(fn):
        _provider_registry[name] = fn
        return fn
    return decorator


def register_provider(name: str, adapter: ProviderAdapter) -> None:
    """Register a ProviderAdapter instance under `name`."""
    _provider_registry[name] = adapter


class _FunctionAdapter(ProviderAdapter):
    """Wraps a @provider-decorated function in the adapter interface."""

    def __init__(self, fn: Callable):
        self._fn = fn

    def call(
        self,
        model: str,
        prompt: str,
        gen: JudgeGenerationConfig,
        retry: RetryPolicy,
    ) -> dict:
        return self._fn(model, prompt, gen, retry)


def get_registered_provider(name: str) -> Optional[ProviderAdapter]:
    """Return a registered adapter for `name`, or None."""
    entry = _provider_registry.get(name)
    if entry is None:
        return None
    if isinstance(entry, ProviderAdapter):
        return entry
    return _FunctionAdapter(entry)


def registered_provider_names() -> set[str]:
    """Names registered so far. Empty until load_providers() has run."""
    return set(_provider_registry)


# The project directory the registry currently reflects.
_registry_project: list[str] = []


def load_providers(providers_path: Path) -> None:
    """
    Import providers.py so @provider decorators register. Raises ConfigError on
    syntax or import error.

    Registrations are scoped to one project. Loading a config from a different
    directory clears them first, including when that project has no
    providers.py at all — otherwise a name registered by the first project
    would still resolve for the second, and `defaults.provider` would silently
    accept a name that project never defined. One process scores one project at
    a time; the calibration panel threads share a directory, so this does not
    fire between them.
    """
    project = str(providers_path.resolve().parent)
    if _registry_project and _registry_project[0] != project:
        _provider_registry.clear()
        _loaded_provider_files.clear()
    _registry_project[:] = [project]

    import_user_file(providers_path, "_fieldtest_providers", _loaded_provider_files)
