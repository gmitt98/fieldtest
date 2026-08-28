"""
fieldtest/providers/settings.py

Which providers are valid, and the connection settings for the ones that need
them.

Lives under providers/ rather than in config.py because the set of valid names
is a provider concern: it grows when a user registers one, and the built-in
list is what providers/__init__.py knows how to construct. Re-exported from
fieldtest.config, so existing imports are unaffected.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


# The single definition of which providers fieldtest ships. providers/__init__.py
# constructs exactly these and config validates against them, so a second copy
# would let the factory and the validator disagree about what exists.
BUILTIN_PROVIDERS = {"anthropic", "gemini", "openai", "openai_compatible"}

# Kept as an alias: it is in fieldtest.config's __all__ and may be imported by
# users. "Valid" is the weaker claim of the two — at runtime a config may also
# name a provider registered with @provider.
VALID_PROVIDERS = BUILTIN_PROVIDERS


class ProviderSettings(BaseModel):
    """
    Connection settings for one provider, from the config's `providers` block.

    Authentication is by environment variable *name*. The name is config; the
    value never is, so a config file can be committed without leaking a key.

    extra="forbid" here and nowhere else in this module: a config that writes
    `api_key: sk-...` would otherwise have it silently ignored, leaving the
    user with a committed secret and a run that still fails on a missing
    credential. This block is the one place a key would plausibly be typed.
    """
    model_config = ConfigDict(extra="forbid")

    # Required, with no default: guessing a base_url would silently send a
    # user's outputs to an endpoint they did not name.
    base_url:    str
    # Optional: a self-hosted endpoint may need no key at all, and absence of
    # one is a valid configuration rather than an error.
    api_key_env: Optional[str] = None


def _validate_provider_name(v: str, where: str) -> str:
    """
    Accept a built-in name or one registered with @provider.

    The registry is consulted at validation time rather than baked into
    VALID_PROVIDERS, because evals/providers.py is imported just before the
    config is parsed and a registered name must satisfy this check.
    """
    from fieldtest.providers.registry import registered_provider_names

    known = VALID_PROVIDERS | registered_provider_names()
    if v in known:
        return v
    supported = ", ".join(sorted(known))
    raise ValueError(
        f"Unknown provider '{v}'. Supported: {supported}. "
        f"Check {where}. Register your own with @provider in evals/providers.py."
    )
