"""
fieldtest/fixtures.py

Loading fixture files and the human labels inside them.

Split out of config.py, which had grown past the point where the model
definitions and the file-reading helpers belonged in one place. The public
names are re-exported from fieldtest.config, so every existing import still
works.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from fieldtest.errors import ConfigError
from fieldtest.resolve import resolve_runs, use_cases_with_fixtures

if TYPE_CHECKING:  # avoids a cycle: config re-exports these
    from fieldtest.config import Config


def load_fixture(fixture_path: Path) -> dict:
    """Load a YAML fixture file. Raises ConfigError if id field missing."""
    try:
        data = yaml.safe_load(fixture_path.read_text())
    except Exception as e:
        raise ConfigError(f"Config error at {fixture_path}: {e}") from e
    if not isinstance(data, dict) or "id" not in data:
        raise ConfigError(f"Config error at {fixture_path}: fixture missing required 'id' field")
    return data


def extract_labels(fixture: dict) -> dict:
    """
    Human verdicts from a fixture, keyed (eval_id, run) → value.

    Labels are per (eval_id, generator run) because different outputs for the
    same fixture warrant different verdicts. Keying by eval_id alone would assume
    the system is deterministic, which is the assumption fieldtest exists to
    reject.

    Malformed entries are skipped rather than raised: label shape is a config
    error reported by `fieldtest validate`, not a scoring-time failure.
    """
    labels: dict = {}
    raw = fixture.get("labels")
    if not isinstance(raw, dict):
        return labels

    for eval_id, per_run in raw.items():
        if not isinstance(per_run, dict):
            continue
        for run, value in per_run.items():
            if isinstance(run, int):
                labels[(eval_id, run)] = value
    return labels


def validate_fixture_labels(config: Config, base_dir: Path) -> tuple[list[str], dict]:
    """
    Check every fixture's labels against the config. Returns (errors, coverage),
    where coverage maps eval_id → number of labeled runs.

    Reported by `fieldtest validate` rather than raised, so a labeling mistake
    surfaces before a run rather than during one.
    """
    errors: list[str] = []
    coverage: dict = {}

    for uc in use_cases_with_fixtures(config):
        eval_by_id = {ev.id: ev for ev in uc.evals}
        max_runs   = resolve_runs(config, uc)
        fixture_dir = base_dir / uc.fixtures.directory

        if not fixture_dir.exists():
            continue

        for fixture_path in sorted(fixture_dir.glob("*.yaml")):
            try:
                fixture = load_fixture(fixture_path)
            except ConfigError:
                continue

            raw = fixture.get("labels")
            if raw is None:
                continue
            if not isinstance(raw, dict):
                errors.append(f"  ⚠ {fixture_path.name}: 'labels' must be a mapping of eval id → run → verdict")
                continue

            for eval_id, per_run in raw.items():
                ev = eval_by_id.get(eval_id)
                if ev is None:
                    errors.append(
                        f"  ⚠ {fixture_path.name}: label references unknown eval '{eval_id}'"
                    )
                    continue
                if not isinstance(per_run, dict):
                    errors.append(
                        f"  ⚠ {fixture_path.name}: labels for '{eval_id}' must map run number → verdict"
                    )
                    continue

                is_scored = ev.type == "llm" and not ev.binary

                for run, value in per_run.items():
                    if not isinstance(run, int) or run < 1:
                        errors.append(
                            f"  ⚠ {fixture_path.name}: label run key '{run}' for '{eval_id}' "
                            f"must be a run number"
                        )
                        continue
                    if run > max_runs:
                        errors.append(
                            f"  ⚠ {fixture_path.name}: label for '{eval_id}' run {run} "
                            f"exceeds runs: {max_runs}"
                        )
                        continue

                    if is_scored:
                        if not isinstance(value, int) or isinstance(value, bool):
                            errors.append(
                                f"  ⚠ {fixture_path.name}: label for scored eval '{eval_id}' "
                                f"run {run} must be an integer score, got {value!r}"
                            )
                            continue
                        if ev.scale and not (ev.scale[0] <= value <= ev.scale[1]):
                            errors.append(
                                f"  ⚠ {fixture_path.name}: label {value} for '{eval_id}' run {run} "
                                f"is outside scale {ev.scale[0]}–{ev.scale[1]}"
                            )
                            continue
                    else:
                        if value not in ("pass", "fail"):
                            errors.append(
                                f"  ⚠ {fixture_path.name}: label for binary eval '{eval_id}' "
                                f"run {run} must be 'pass' or 'fail', got {value!r}"
                            )
                            continue

                    coverage[eval_id] = coverage.get(eval_id, 0) + 1

    return errors, coverage
