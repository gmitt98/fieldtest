"""
fieldtest/resolve.py

Turning config into the concrete values a run needs: how many runs, which
fixtures, which dataset version.

Every function here answers "what did the user actually ask for", where the
answer comes from more than one place in the config and the precedence matters.
Re-exported from fieldtest.config.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fieldtest.errors import ConfigError

if TYPE_CHECKING:  # avoids a cycle: config re-exports these
    from fieldtest.config import Config, UseCase


def use_cases_with_fixtures(config: Config):
    """Use cases that declare a fixtures directory."""
    return [uc for uc in config.use_cases if uc.fixtures is not None]


def resolve_runs(config: Config, use_case: UseCase) -> int:
    """Return effective run count. use_case wins, then defaults, then hardcoded 5."""
    if use_case.fixtures.runs is not None:
        return use_case.fixtures.runs
    return config.defaults.runs  # Defaults model defaults to 5


def resolve_judge_runs(config: Config, use_case: UseCase) -> int:
    """Judge repetitions per output for a use case. Defaults to 1."""
    return use_case.fixtures.judge_runs


def resolve_dataset_version(config: Config) -> Optional[str]:
    """
    Return the dataset version from the first use_case's fixtures.version, or None.
    Mirrors the run-resolution pattern: a single value per run, taken from the
    first use_case (consistent with how `runs` is reported in `data.json`).
    """
    if not config.use_cases:
        return None
    return config.use_cases[0].fixtures.version


def resolve_set(set_name: str, use_case: UseCase, base_dir: Path) -> list[str]:
    """
    Resolve a named fixture set to a flat list of fixture IDs.

    Values:
      list[str]  → those exact IDs
      "dir/*"    → all fixture files in fixtures/<dir>/ subdirectory
      "all"      → all fixture files in fixtures/ (recursive)

    Raises ConfigError if set_name not found in use_case.
    """
    sets = use_case.fixtures.sets
    if set_name not in sets:
        raise ConfigError(
            f"Set '{set_name}' not found in use_case '{use_case.id}'. "
            f"Available sets: {list(sets.keys())}"
        )
    value = sets[set_name]
    fixture_dir = base_dir / use_case.fixtures.directory

    if isinstance(value, list):
        return value

    if value == "all":
        return [p.stem for p in sorted(fixture_dir.rglob("*.yaml"))]

    # "dir/*" glob pattern
    if value.endswith("/*"):
        sub = value[:-2]  # strip /*
        subdir = fixture_dir / sub
        return [p.stem for p in sorted(subdir.glob("*.yaml"))]

    raise ConfigError(
        f"Config error at use_cases.{use_case.id}.fixtures.sets.{set_name}: "
        f"unrecognised set value '{value}'. Expected list, 'all', or 'dir/*'."
    )
