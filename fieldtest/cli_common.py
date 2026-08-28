"""
fieldtest/cli_common.py

Helpers shared by every CLI command: error handling, config loading, and the
provider report.

Here rather than in cli.py so the command modules can use them without
importing cli.py, which imports them.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import click

from fieldtest.errors import FieldtestError


def _handle_error(e: Exception) -> None:
    """Print error to stderr and exit 1. Unexpected errors show traceback + bug URL."""
    if isinstance(e, FieldtestError):
        click.echo(str(e), err=True)
        sys.exit(1)
    else:
        click.echo(traceback.format_exc(), err=True)
        click.echo(
            "Please file a bug at https://github.com/galenmittermann/fieldtest/issues",
            err=True,
        )
        sys.exit(1)


def _load_config(config_path: Path):
    """Load and validate config. Calls sys.exit(1) on error."""
    from fieldtest.config import parse_and_validate
    try:
        return parse_and_validate(config_path)
    except Exception as e:
        _handle_error(e)


def _default_config_path() -> Path:
    return Path("evals/config.yaml")


# Environment variable each built-in provider reads. openai_compatible names
# its own in config; a @provider adapter is the user's, so nothing is claimed
# about it beyond that it is registered.
#
# Must match what the adapters actually read. This said GOOGLE_API_KEY for
# gemini while the adapter read GEMINI_API_KEY, so validate reported a
# correctly configured key as missing. Pinned by
# test_provider_env_names_match_what_the_adapters_read.
_PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "gemini":    "GEMINI_API_KEY",
}


def _provider_report(config) -> list[str]:
    """One line per provider the config references, with credential status."""
    import os

    from fieldtest.providers import BUILTIN_PROVIDERS
    from fieldtest.providers.registry import registered_provider_names

    used = {config.defaults.provider}
    used.update(
        ev.provider for uc in config.use_cases for ev in uc.evals if ev.provider
    )
    if config.calibration:
        used.update(j.provider for j in config.calibration.panel)

    registered = registered_provider_names()
    lines = []
    for name in sorted(used):
        settings = config.providers.get(name)
        if name == "openai_compatible" and settings:
            where = f" → {settings.base_url}"
            env   = settings.api_key_env
        elif name in registered and name not in BUILTIN_PROVIDERS:
            lines.append(f"  provider '{name}' — registered in evals/providers.py")
            continue
        else:
            where = ""
            env   = _PROVIDER_ENV.get(name)

        if env is None:
            # A self-hosted endpoint may need no key. Absence is a valid
            # configuration, so this is stated rather than warned about.
            lines.append(f"  provider '{name}'{where} — no API key configured")
        elif os.environ.get(env):
            lines.append(f"  provider '{name}'{where} — {env} set")
        else:
            lines.append(f"  ⚠ provider '{name}'{where} — {env} NOT set")
    return lines
