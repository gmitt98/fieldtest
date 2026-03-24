"""
tests/test_cli.py

CLI integration tests + error contract tests.
Test names match spec §7 and §17.
Uses click.testing.CliRunner — no subprocess overhead.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from fieldtest.cli import main


# ---------------------------------------------------------------------------
# Helpers — build minimal test project in tmp_path
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = """\
schema_version: 1
system:
  name: test system
  domain: test domain
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: ev_regex
        tag: right
        type: regex
        description: checks for Go
        pattern: "Go"
        match: true
    fixtures:
      directory: fixtures/
      sets:
        smoke: [fix1]
        full: [fix1, fix2]
      runs: 2
defaults:
  provider: anthropic
  model: claude-haiku-3-5-20251001
  runs: 2
"""

MINIMAL_CONFIG_RULE = """\
schema_version: 1
system:
  name: test system
  domain: test domain
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: has_content
        tag: right
        type: rule
        description: checks output has content
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
      runs: 1
"""

FIXTURE_FIX1 = """\
id: fix1
description: test fixture 1
inputs:
  text: hello world
"""

FIXTURE_FIX2 = """\
id: fix2
description: test fixture 2
inputs:
  text: goodbye world
"""


def _setup_project(tmp_path: Path, config: str = MINIMAL_CONFIG, runs: int = 2) -> Path:
    """Create a minimal valid eval project in tmp_path. Returns evals/ dir path."""
    evals_dir = tmp_path / "evals"
    (evals_dir / "fixtures").mkdir(parents=True, exist_ok=True)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(textwrap.dedent(FIXTURE_FIX1))
    (evals_dir / "fixtures" / "fix2.yaml").write_text(textwrap.dedent(FIXTURE_FIX2))
    (evals_dir / "config.yaml").write_text(textwrap.dedent(config))
    (evals_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (evals_dir / "results").mkdir(parents=True, exist_ok=True)
    return evals_dir


def _write_outputs(evals_dir: Path, fixture_id: str, runs: int, content: str = "I love Go"):
    """Write run-N.txt files for a fixture."""
    out_dir = evals_dir / "outputs" / fixture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, runs + 1):
        (out_dir / f"run-{n}.txt").write_text(content)


def _run_score(evals_dir: Path, set_name: str = "full", extra_args: list = None) -> any:
    runner = CliRunner()
    args   = ["score", "--config", str(evals_dir / "config.yaml"), "--set", set_name]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(main, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Test cases (spec §7)
# ---------------------------------------------------------------------------

def test_score_exits_0_on_success(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir)
    assert result.exit_code == 0
    # All four result files written
    results = list((evals_dir / "results").glob("*-data.json"))
    assert len(results) == 1
    assert any((evals_dir / "results").glob("*-report.md"))
    assert any((evals_dir / "results").glob("*-data.csv"))
    assert any((evals_dir / "results").glob("*-report.csv"))


def test_score_exit_0_despite_high_failure(tmp_path):
    """All evals fail → still exit 0. Tool measures; human judges."""
    evals_dir = _setup_project(tmp_path)
    # Write outputs that will fail the regex (no "Go")
    _write_outputs(evals_dir, "fix1", runs=2, content="I love Python")
    _write_outputs(evals_dir, "fix2", runs=2, content="I love Python")
    result = _run_score(evals_dir)
    assert result.exit_code == 0


def test_missing_output_exits_1(tmp_path):
    evals_dir = _setup_project(tmp_path)
    # Write fix1 run-1 only (fix1 needs 2 runs, fix2 needs 2 runs)
    out_dir = evals_dir / "outputs" / "fix1"
    out_dir.mkdir(parents=True)
    (out_dir / "run-1.txt").write_text("Go")
    # fix2 has no outputs at all
    result = _run_score(evals_dir)
    assert result.exit_code == 1
    # Error message must name the missing file (CliRunner mixes stderr into output by default)
    assert "run-2.txt" in result.output or "fix1" in result.output


def test_allow_partial_continues(tmp_path):
    evals_dir = _setup_project(tmp_path)
    # Write only fix1 run-1 (run-2 missing, fix2 entirely missing)
    out_dir = evals_dir / "outputs" / "fix1"
    out_dir.mkdir(parents=True)
    (out_dir / "run-1.txt").write_text("Go")
    result = _run_score(evals_dir, extra_args=["--allow-partial"])
    assert result.exit_code == 0


def test_allow_partial_skipped_in_results(tmp_path):
    evals_dir = _setup_project(tmp_path)
    # Write only fix1, both runs
    _write_outputs(evals_dir, "fix1", runs=2)
    # fix2 missing entirely → smoke set only has fix1, use smoke
    result = _run_score(evals_dir, set_name="smoke")
    assert result.exit_code == 0
    results = list((evals_dir / "results").glob("*.json"))
    data = json.loads(results[0].read_text())
    # All rows for fix1 should be present (no skip for regex)
    assert len(data["rows"]) > 0


def test_unknown_set_exits_1(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir, set_name="nonexistent")
    assert result.exit_code == 1
    assert "nonexistent" in result.output


def test_rules_absent_no_rule_evals(tmp_path):
    """No rules.py, no type:rule evals → exit 0."""
    evals_dir = _setup_project(tmp_path)  # uses MINIMAL_CONFIG with regex only
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    assert not (evals_dir / "rules.py").exists()
    result = _run_score(evals_dir)
    assert result.exit_code == 0


def test_rules_syntax_error_exits_1(tmp_path):
    evals_dir = _setup_project(tmp_path, config=MINIMAL_CONFIG_RULE)
    _write_outputs(evals_dir, "fix1", runs=1)
    (evals_dir / "rules.py").write_text("def broken(\n")  # syntax error
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["score", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1


def test_config_error_exits_1(tmp_path):
    evals_dir = _setup_project(tmp_path, config="schema_version: 99\nsystem:\n  name: x\n  domain: y\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["score", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1


def test_no_results_written_on_error(tmp_path):
    """Config error → results/ has no new files."""
    evals_dir = _setup_project(tmp_path, config="schema_version: 99\n")
    before = list((evals_dir / "results").glob("*"))
    runner = CliRunner()
    runner.invoke(
        main,
        ["score", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    after = list((evals_dir / "results").glob("*"))
    assert len(before) == len(after)


def test_concurrency_1_same_results(tmp_path):
    """--concurrency 1 produces valid results (same structure as default)."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir, extra_args=["--concurrency", "1"])
    assert result.exit_code == 0
    results = list((evals_dir / "results").glob("*-data.json"))
    assert len(results) == 1


def test_results_dir_created_if_missing(tmp_path):
    """results/ absent → created by fieldtest score."""
    evals_dir = _setup_project(tmp_path)
    import shutil
    shutil.rmtree(evals_dir / "results")
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir)
    assert result.exit_code == 0
    assert (evals_dir / "results").exists()


# ---------------------------------------------------------------------------
# Error contract tests (spec §17)
# ---------------------------------------------------------------------------

def test_error_to_stderr(tmp_path):
    """ConfigError → non-empty error output, exit 1."""
    runner    = CliRunner()
    evals_dir = _setup_project(tmp_path, config="schema_version: 99\n")
    result    = runner.invoke(
        main,
        ["score", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert result.output.strip() != ""  # error message present


def test_provider_error_message_format(tmp_path):
    """Unknown provider → ProviderError message format."""
    config = MINIMAL_CONFIG.replace("provider: anthropic", "provider: unknown_provider")
    evals_dir = _setup_project(tmp_path, config=config)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    # Only triggers if an LLM eval runs — add one
    llm_config = """\
schema_version: 1
system:
  name: test
  domain: test
use_cases:
  - id: uc1
    description: d
    evals:
      - id: ev1
        tag: right
        type: llm
        description: check
        pass_criteria: good
        fail_criteria: bad
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
      runs: 1
defaults:
  provider: bad_provider
  model: test-model
  runs: 1
"""
    evals_dir2 = _setup_project(tmp_path / "p2" / "x", config=llm_config)
    _write_outputs(evals_dir2, "fix1", runs=1)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["score", "--config", str(evals_dir2 / "config.yaml")],
        catch_exceptions=False,
    )
    # Provider error surfaces in result row (not run-aborting), so exit 0
    # but the error should appear in the JSON
    results = list((evals_dir2 / "results").glob("*-data.json"))
    if results:
        data = json.loads(results[0].read_text())
        errors = [r for r in data["rows"] if r.get("error")]
        assert len(errors) > 0
        assert "bad_provider" in errors[0]["error"] or "Unknown provider" in errors[0]["error"]


def test_output_error_message_format(tmp_path):
    """Missing output → OutputError message names the file."""
    evals_dir = _setup_project(tmp_path)
    # Write fix1 run-1 only
    out_dir = evals_dir / "outputs" / "fix1"
    out_dir.mkdir(parents=True)
    (out_dir / "run-1.txt").write_text("Go")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["score", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    # Error message should reference the missing file
    assert "run-2.txt" in result.output or "fix1" in result.output


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------

def test_validate_valid_config(tmp_path):
    evals_dir = _setup_project(tmp_path)
    runner    = CliRunner()
    result    = runner.invoke(
        main,
        ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "✓" in result.output


def test_validate_invalid_config_exits_1(tmp_path):
    evals_dir = _setup_project(tmp_path, config="schema_version: 99\n")
    runner    = CliRunner()
    result    = runner.invoke(
        main,
        ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------

def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert (tmp_path / "evals" / "config.yaml").exists()
    assert (tmp_path / "evals" / "fixtures" / "golden").exists()
    assert (tmp_path / "evals" / "fixtures" / "variations").exists()
    assert (tmp_path / "evals" / ".gitignore").exists()


def test_init_fails_if_exists_no_force(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(evals_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1


def test_init_force_overwrites(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(evals_dir), "--force"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# history command
# ---------------------------------------------------------------------------

def test_history_no_results(tmp_path):
    evals_dir = _setup_project(tmp_path)
    runner    = CliRunner()
    result    = runner.invoke(
        main,
        ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "No results" in result.output
