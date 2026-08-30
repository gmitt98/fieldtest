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
from typing import TYPE_CHECKING, Optional

import yaml

from fieldtest.errors import ConfigError
from fieldtest.resolve import resolve_runs, use_cases_with_fixtures

if TYPE_CHECKING:  # avoids a cycle: config re-exports these
    from fieldtest.config import Config


# Marks an input whose value names a file to read rather than text to use.
FILE_PREFIX = "file:"


def resolve_file_inputs(
    inputs: dict,
    base_dir: Path,
    where: Path,
) -> tuple[dict, list[str]]:
    """
    Replace `file:`-prefixed input values with the contents of the file.

    Returns (inputs, keys resolved). Raises ConfigError naming the fixture if a
    referenced file is missing, so a broken reference is a validation failure
    rather than a judge that silently reads a path.

    A prefix rather than a heuristic on values that look like paths: a fixture
    whose input is literally "see notes/faq.md" is legitimate, and quietly
    swapping it for file contents would be a worse failure than the one this
    fixes.
    """
    if not isinstance(inputs, dict):
        return inputs, []

    resolved_keys: list[str] = []
    out = dict(inputs)
    for key, value in inputs.items():
        if not isinstance(value, str) or not value.startswith(FILE_PREFIX):
            continue
        rel = value[len(FILE_PREFIX):].strip()
        target = (base_dir / rel).resolve()
        if not target.is_file():
            raise ConfigError(
                f"Config error at {where}: inputs.{key} references "
                f"'{rel}', which does not exist\n"
                f"  looked in {base_dir}"
            )
        try:
            out[key] = target.read_text()
        except Exception as e:
            raise ConfigError(
                f"Config error at {where}: inputs.{key} could not read '{rel}': {e}"
            ) from e
        resolved_keys.append(key)

    return out, resolved_keys


EXPECTED_KEYS = ("contains", "not_contains")


def _check_expected(data: dict, where: Path) -> None:
    """
    Validate the `expected` block a reference eval reads.

    Unchecked, a string here crashed the judge with an AttributeError and a
    "please file a bug"; an unrecognised key was worse, because the judge found
    no checks to run, reported "all checks passed", and a safe-tagged eval
    passed every output it was ever given.
    """
    expected = data.get("expected")
    if expected is None:
        return

    fixture_id = data.get("id", "?")
    if not isinstance(expected, dict):
        raise ConfigError(
            f"Config error at {where}: fixture '{fixture_id}' has `expected` as "
            f"{type(expected).__name__}, but it must be a mapping of "
            f"{' / '.join(EXPECTED_KEYS)}.\n"
            f"  expected:\n"
            f"    contains:\n"
            f"      - \"a string the output must contain\""
        )

    unknown = [k for k in expected if k not in EXPECTED_KEYS]
    if unknown:
        raise ConfigError(
            f"Config error at {where}: fixture '{fixture_id}' has "
            f"expected.{unknown[0]}, which the reference judge does not read. "
            f"Valid keys: {', '.join(EXPECTED_KEYS)}."
        )

    if not any(expected.get(k) for k in EXPECTED_KEYS):
        raise ConfigError(
            f"Config error at {where}: fixture '{fixture_id}' has an `expected` "
            f"block with nothing in it. A reference eval against it would pass "
            f"every output. Remove it, or give it a "
            f"{' or '.join(EXPECTED_KEYS)} list."
        )

    for key in EXPECTED_KEYS:
        val = expected.get(key)
        if val is None:
            continue
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise ConfigError(
                f"Config error at {where}: fixture '{fixture_id}' has "
                f"expected.{key} as {type(val).__name__}, but it must be a list "
                f"of strings."
            )


def load_fixture(fixture_path: Path, base_dir: Optional[Path] = None) -> dict:
    """
    Load a YAML fixture file. Raises ConfigError if the id field is missing.

    When base_dir is given, `file:` inputs are resolved against it, so every
    consumer — judge, rule, report — sees the document rather than its path.
    Resolution happens here rather than at prompt-building time so that a rule
    eval and an LLM eval are handed the same thing.
    """
    try:
        data = yaml.safe_load(fixture_path.read_text())
    except Exception as e:
        raise ConfigError(f"Config error at {fixture_path}: {e}") from e
    if not isinstance(data, dict) or "id" not in data:
        raise ConfigError(f"Config error at {fixture_path}: fixture missing required 'id' field")

    _check_expected(data, fixture_path)

    if base_dir is not None and isinstance(data.get("inputs"), dict):
        data["inputs"], resolved = resolve_file_inputs(
            data["inputs"], base_dir, fixture_path
        )
        if resolved:
            # Kept on the fixture rather than logged: `fieldtest validate`
            # reports it, so a reader can confirm the judge was handed the
            # document and not the path.
            data["_resolved_file_inputs"] = resolved
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
                fixture = load_fixture(fixture_path, base_dir)
            except ConfigError as e:
                # A file: input pointing at nothing is reportable, not
                # skippable: the judge would otherwise be handed a path at the
                # first call, twenty errored rows into a run.
                if "references" in str(e) or "could not read" in str(e):
                    errors.append(f"  ⚠ {str(e).splitlines()[0]}")
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


def summarize_file_inputs(config: "Config", base_dir: Path) -> dict[str, list[str]]:
    """
    Fixture id → the input keys that were read from files.

    Reported by `fieldtest validate` so a reader can confirm the judge is handed
    the document rather than its path — the distinction that made a grounding
    eval score 0.818 while its reasoning said no source was provided.
    """
    found: dict[str, list[str]] = {}
    for uc in use_cases_with_fixtures(config):
        fixture_dir = base_dir / uc.fixtures.directory
        if not fixture_dir.exists():
            continue
        for fixture_path in sorted(fixture_dir.rglob("*.yaml")):
            try:
                fixture = load_fixture(fixture_path, base_dir)
            except ConfigError:
                continue
            resolved = fixture.get("_resolved_file_inputs")
            if resolved:
                found[fixture_path.stem] = resolved
    return found
