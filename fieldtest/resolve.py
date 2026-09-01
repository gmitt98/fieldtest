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


def validate_run_counts(config: Config) -> None:
    """
    Reject runs/judge_runs below 1.

    runs: 0 would write a green result set that measured nothing, and
    judge_runs: 0 would silently drop every LLM eval from the run. Shared by
    score() and the validate command so `validate` cannot bless a config that
    `score` immediately refuses.
    """
    for uc in config.use_cases:
        runs_v = resolve_runs(config, uc)
        if runs_v < 1:
            raise ConfigError(
                f"Config error at use_cases.{uc.id}.fixtures.runs: must be at "
                f"least 1, got {runs_v}. A run that scores zero outputs would "
                f"report a green, empty result set."
            )
        judge_runs_v = resolve_judge_runs(config, uc)
        if judge_runs_v < 1:
            raise ConfigError(
                f"Config error at use_cases.{uc.id}.fixtures.judge_runs: must be "
                f"at least 1, got {judge_runs_v}. judge_runs below 1 would "
                f"silently drop every LLM eval from the run."
            )


def config_identity(config: Config) -> Optional[str]:
    """
    Which config's evals a run measured, or None if that is unknown.

    A results directory is shared by every config beside it, and the walkthrough
    has the reader score `reference-evals.yaml` into the same one. That run then
    became the automatic baseline for the next `config.yaml` run — a different
    set of evals, asking different questions, silently compared. `set`,
    `dataset_version` and the judge fingerprint each already reject a baseline
    that measured something else; this is the fourth thing that can differ.

    The file name, not a hash of the evals: editing an eval, or adding one, is
    the ordinary use fieldtest exists for and must keep its history. Scoring a
    different config is a different instrument. None for a Config that was
    built rather than loaded, which keeps every candidate comparable.
    """
    return config._source_name


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
