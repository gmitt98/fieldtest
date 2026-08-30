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
from unittest.mock import patch

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
  model: claude-haiku-4-5
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
    result = CliRunner().invoke(
        main,
        ["score", "--config", str(evals_dir2 / "config.yaml")],
        catch_exceptions=False,
    )

    # An unknown provider is a config error, caught before any judge call —
    # better than the errored rows this test previously expected. It asserted
    # those rows inside `if results:`, and no -data.json is written on a config
    # error, so every assertion in it was unreachable.
    assert result.exit_code == 1
    assert "Unknown provider 'bad_provider'" in result.output
    # The error is where a user learns the limit, so it names both ways out.
    assert "openai_compatible" in result.output
    assert "@provider" in result.output
    assert not list((evals_dir2 / "results").glob("*-data.json"))


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


def test_init_default_hints_template(tmp_path):
    """Default init output mentions --template as a next step."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "--template" in result.output


# ---------------------------------------------------------------------------
# init --template
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_creates_config(tmp_path, template):
    """--template scaffolds config.yaml from templates/ dir."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    config_path = tmp_path / "evals" / "config.yaml"
    assert config_path.exists()
    content = config_path.read_text()
    assert "schema_version: 2" in content
    assert f"--template {template}" in content


@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_creates_dirs(tmp_path, template):
    """--template creates fixture and output dirs."""
    runner = CliRunner()
    runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    assert (tmp_path / "evals" / "fixtures" / "golden").is_dir()
    assert (tmp_path / "evals" / "fixtures" / "variations").is_dir()
    assert (tmp_path / "evals" / "outputs").is_dir()
    assert (tmp_path / "evals" / "results").is_dir()
    assert (tmp_path / "evals" / ".gitignore").exists()


@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_no_fixtures_copied(tmp_path, template):
    """Templates scaffold empty fixture dirs — no demo data."""
    runner = CliRunner()
    runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    golden = tmp_path / "evals" / "fixtures" / "golden"
    assert list(golden.iterdir()) == []


@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_tags_blank(tmp_path, template):
    """Template configs have blank tags — user must fill them in."""
    import yaml
    runner = CliRunner()
    runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    config = yaml.safe_load((tmp_path / "evals" / "config.yaml").read_text())
    for uc in config["use_cases"]:
        for ev in uc["evals"]:
            assert ev["tag"] == "" or ev["tag"] is None, (
                f"Tag should be blank in template, got '{ev['tag']}' for eval '{ev['id']}'"
            )


@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_valid_yaml(tmp_path, template):
    """Template configs parse as valid YAML with expected structure."""
    import yaml
    runner = CliRunner()
    runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    config = yaml.safe_load((tmp_path / "evals" / "config.yaml").read_text())
    assert config["schema_version"] == 2
    assert "system" in config
    assert "use_cases" in config
    assert len(config["use_cases"]) >= 1
    assert len(config["use_cases"][0]["evals"]) >= 1
    assert "defaults" in config


@pytest.mark.parametrize("template", ["chatbot", "rag", "email"])
def test_init_template_validates_with_tags_filled(tmp_path, template):
    """Template configs pass fieldtest validate when tags are filled in."""
    import yaml
    from fieldtest.config import parse_and_validate
    runner = CliRunner()
    runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", template],
        catch_exceptions=False,
    )
    config_path = tmp_path / "evals" / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    # Fill blank tags with 'right' so validation passes
    for uc in config["use_cases"]:
        for ev in uc["evals"]:
            if not ev.get("tag"):
                ev["tag"] = "right"
    config_path.write_text(yaml.dump(config))
    # Should not raise
    parse_and_validate(config_path)


def test_init_template_output_mentions_tags(tmp_path):
    """Template init output tells user to tag evals."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--dir", str(tmp_path / "evals"), "--template", "chatbot"],
        catch_exceptions=False,
    )
    assert "Tag each eval" in result.output


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


# ---------------------------------------------------------------------------
# diff command — dataset_version warning
# ---------------------------------------------------------------------------

def _plant_run(evals_dir: Path, run_id: str, dataset_version: str | None,
               baseline_run_id: str | None = None,
               judge: dict | None = None) -> None:
    """Plant a fake -data.json that the diff command will read."""
    data: dict = {
        "run_id": run_id,
        "set":    "full",
        "summary": {},
        "delta":   {
            "baseline_run_id": baseline_run_id,
            "increased": [],
            "decreased": [],
            "unchanged": [],
        },
    }
    if dataset_version is not None:
        data["dataset_version"] = dataset_version
    if judge is not None:
        data["judge"] = judge
    (evals_dir / "results" / f"{run_id}-data.json").write_text(json.dumps(data))


def test_diff_warns_on_dataset_version_mismatch(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version="v1")
    _plant_run(evals_dir, "run-new", dataset_version="v2", baseline_run_id="run-old")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Dataset version mismatch" in result.output
    assert "v2" in result.output and "v1" in result.output


def test_diff_silent_when_versions_match(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version="v2")
    _plant_run(evals_dir, "run-new", dataset_version="v2", baseline_run_id="run-old")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Dataset version mismatch" not in result.output


def test_diff_silent_when_either_side_unversioned(tmp_path):
    """Backwards compat: unversioned baseline + versioned current → no warning."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version=None)
    _plant_run(evals_dir, "run-new", dataset_version="v2", baseline_run_id="run-old")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Dataset version mismatch" not in result.output


def test_score_writes_dataset_version_to_data_json(tmp_path):
    """Verify dataset_version round-trips from config → data.json."""
    config_with_version = MINIMAL_CONFIG.replace(
        "      runs: 2\n",
        "      runs: 2\n      version: v3\n",
        1,  # only first occurrence (under fixtures, not defaults)
    )
    evals_dir = _setup_project(tmp_path, config=config_with_version)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir, set_name="full")
    assert result.exit_code == 0

    data_files = list((evals_dir / "results").glob("*-data.json"))
    assert len(data_files) == 1
    data = json.loads(data_files[0].read_text())
    assert data.get("dataset_version") == "v3"


def test_score_data_json_has_null_version_when_unset(tmp_path):
    """Existing configs without `version` write null — schema stays consistent."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir, set_name="full")
    assert result.exit_code == 0

    data_files = list((evals_dir / "results").glob("*-data.json"))
    data = json.loads(data_files[0].read_text())
    assert "dataset_version" in data  # field always present
    assert data["dataset_version"] is None


# ---------------------------------------------------------------------------
# diff command — judge provenance warning (spec 01)
# ---------------------------------------------------------------------------

_JUDGE_HAIKU = {
    "provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 0.0,
    "seed": None, "overrides": {}, "fingerprint": "aaaaaaaa",
}
_JUDGE_SONNET = {
    "provider": "anthropic", "model": "claude-sonnet-5", "temperature": 0.0,
    "seed": None, "overrides": {}, "fingerprint": "bbbbbbbb",
}


def test_diff_warns_on_judge_mismatch_with_explicit_baseline(tmp_path):
    """Same outputs, different judge — the instrument changed, not the system."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", None, judge=_JUDGE_HAIKU)
    _plant_run(evals_dir, "run-new", None, baseline_run_id="run-old", judge=_JUDGE_SONNET)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Judge mismatch" in result.output
    assert "claude-haiku-4-5 → claude-sonnet-5" in result.output


def test_diff_silent_when_judge_matches(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", None, judge=_JUDGE_HAIKU)
    _plant_run(evals_dir, "run-new", None, baseline_run_id="run-old", judge=_JUDGE_HAIKU)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "Judge mismatch" not in result.output


def test_diff_notes_baseline_predating_judge_tracking(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", None)  # no judge key
    _plant_run(evals_dir, "run-new", None, baseline_run_id="run-old", judge=_JUDGE_HAIKU)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "predates judge tracking" in result.output


def test_history_shows_judge_model(tmp_path):
    """A rate series is unreadable if the instrument changed mid-series."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", None, judge=_JUDGE_HAIKU)
    _plant_run(evals_dir, "run-new", None, judge=_JUDGE_SONNET)

    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "JUDGE" in result.output
    assert "claude-haiku-4-5" in result.output
    assert "claude-sonnet-5" in result.output


def test_validate_prints_projected_call_count(tmp_path):
    """Cost is multiplicative — a user should meet the 3x bill before paying it."""
    config = """\
schema_version: 2
system:
  name: test system
  domain: test domain
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: ev1
        tag: right
        type: llm
        description: checks something
        pass_criteria: it is fine
        fail_criteria: it is not
    fixtures:
      directory: fixtures/
      judge_runs: 3
      sets:
        full: [fix1, fix2]
"""
    evals_dir = _setup_project(tmp_path, config=config)
    for fid in ("fix1", "fix2"):
        (evals_dir / "fixtures").mkdir(exist_ok=True)
        (evals_dir / "fixtures" / f"{fid}.yaml").write_text(
            f"id: {fid}\ninputs:\n  q: x\n"
        )

    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    # 2 fixtures × 5 runs × 3 judge_runs × 1 llm eval
    assert "30 judge call(s)" in result.output
    assert "judge_runs: 3" in result.output


def test_validate_omits_call_count_without_llm_evals(tmp_path):
    """A regex-only project makes no judge calls; projecting a bill would be wrong."""
    evals_dir = _setup_project(tmp_path)
    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "judge call(s)" not in result.output


def test_validate_reports_label_coverage(tmp_path):
    """A user should be able to see how thin the ground truth is."""
    config = """\
schema_version: 2
system:
  name: test system
  domain: test domain
defaults:
  runs: 3
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: ev1
        tag: right
        type: llm
        description: checks something
        pass_criteria: it is fine
        fail_criteria: it is not
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
"""
    evals_dir = _setup_project(tmp_path, config=config)
    (evals_dir / "fixtures").mkdir(exist_ok=True)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(
        "id: fix1\ninputs:\n  q: x\nlabels:\n  ev1:\n    1: pass\n    2: fail\n"
    )

    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "human labels:" in result.output
    assert "ev1: 2 labeled run(s)" in result.output


def test_validate_flags_bad_label(tmp_path):
    config = """\
schema_version: 2
system:
  name: test system
  domain: test domain
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: ev1
        tag: right
        type: llm
        description: checks something
        pass_criteria: it is fine
        fail_criteria: it is not
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
"""
    evals_dir = _setup_project(tmp_path, config=config)
    (evals_dir / "fixtures").mkdir(exist_ok=True)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(
        "id: fix1\ninputs:\n  q: x\nlabels:\n  ghost:\n    1: pass\n"
    )

    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "unknown eval 'ghost'" in result.output


def test_validate_silent_about_labels_when_none(tmp_path):
    evals_dir = _setup_project(tmp_path)
    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "human labels:" not in result.output


# ---------------------------------------------------------------------------
# calibrate command (spec 08)
# ---------------------------------------------------------------------------

_CALIBRATION_CONFIG = """\
schema_version: 2
system:
  name: test system
  domain: test domain
calibration:
  panel:
    - { provider: anthropic, model: claude-haiku-4-5 }
    - { provider: openai,    model: gpt-5 }
use_cases:
  - id: uc1
    description: test use case
    evals:
      - id: ev1
        tag: safe
        type: llm
        description: checks something
        pass_criteria: it is fine
        fail_criteria: it is not
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
"""


def test_calibrate_errors_without_panel(tmp_path):
    evals_dir = _setup_project(tmp_path)
    result = CliRunner().invoke(
        main, ["calibrate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "calibration" in result.output
    assert "panel:" in result.output


def test_dry_run_makes_no_provider_calls(tmp_path):
    evals_dir = _setup_project(tmp_path, config=_CALIBRATION_CONFIG)
    (evals_dir / "fixtures").mkdir(exist_ok=True)
    (evals_dir / "fixtures" / "fix1.yaml").write_text("id: fix1\ninputs:\n  q: x\n")

    with patch("fieldtest.runner.score") as mock_score:
        result = CliRunner().invoke(
            main,
            ["calibrate", "--dry-run", "--config", str(evals_dir / "config.yaml")],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert mock_score.call_count == 0
    assert "Dry run — nothing called." in result.output
    # 1 fixture × 5 runs × 1 eval × 2 judges
    assert "10 judge call(s)" in result.output


def test_calibrate_writes_artifacts(tmp_path):
    from fieldtest.config import ResultRow

    evals_dir = _setup_project(tmp_path, config=_CALIBRATION_CONFIG)
    (evals_dir / "fixtures").mkdir(exist_ok=True)
    (evals_dir / "fixtures" / "fix1.yaml").write_text("id: fix1\ninputs:\n  q: x\n")

    rows = [
        ResultRow(use_case="uc1", eval_id="ev1", tag="safe", type="llm",
                  fixture_id="fix1", run=i, passed=(i != 2))
        for i in (1, 2, 3)
    ]

    with patch("fieldtest.runner.score", return_value=("r", rows)):
        result = CliRunner().invoke(
            main, ["calibrate", "--config", str(evals_dir / "config.yaml")],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    written = list((evals_dir / "results").glob("*-calibration.md"))
    assert len(written) == 1
    assert "Judge Calibration" in written[0].read_text()
    # Calibration output must not look like a scoring result.
    assert not list((evals_dir / "results").glob("*-data.json"))


# ---------------------------------------------------------------------------
# diff --baseline must actually recompute (ultrareview bug_003)
# ---------------------------------------------------------------------------

def _plant_scored_run(evals_dir: Path, run_id: str, failure_rate: float,
                      judge: dict | None = None, baseline_run_id: str | None = None) -> None:
    """A run with a real summary, so build_delta has something to compare."""
    data = {
        "schema_version": 2,
        "run_id": run_id,
        "set": "full",
        "summary": {"uc1": {"right": {"ev1": {
            "failure_rate": failure_rate,
            "failure_rate_ci": [0.0, 1.0],
            "total_runs": 5,
            "error_count": 0,
        }}}},
        "delta": {
            "baseline_run_id": baseline_run_id,
            "increased": [], "decreased": [], "unchanged": [],
            "baseline_pre_judge": False, "baseline_judge_runs": 1,
        },
    }
    if judge is not None:
        data["judge"] = judge
    (evals_dir / "results" / f"{run_id}-data.json").write_text(json.dumps(data))


def test_diff_explicit_baseline_is_actually_used(tmp_path):
    """
    The stored delta was frozen at score time against whatever find_baseline()
    auto-detected. Reusing it made --baseline a silent no-op.
    """
    evals_dir = _setup_project(tmp_path)
    _plant_scored_run(evals_dir, "run-old", failure_rate=0.0, judge=_JUDGE_HAIKU)
    # run-new auto-detected nothing, so its stored delta is empty.
    _plant_scored_run(evals_dir, "run-new", failure_rate=0.8, judge=_JUDGE_HAIKU,
                      baseline_run_id=None)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--baseline", "run-old",
         "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    # Recomputed against run-old: the failure rate went 0.0 → 0.8.
    assert "run-old" in result.output
    assert "ev1" in result.output


def test_diff_explicit_baseline_warns_on_judge_mismatch(tmp_path):
    """Spec 01 §2.5 — and it has to fire through the actual flag."""
    evals_dir = _setup_project(tmp_path)
    _plant_scored_run(evals_dir, "run-old", failure_rate=0.0, judge=_JUDGE_HAIKU)
    _plant_scored_run(evals_dir, "run-new", failure_rate=0.2, judge=_JUDGE_SONNET,
                      baseline_run_id=None)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--baseline", "run-old",
         "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )

    assert "Judge mismatch" in result.output
    assert "claude-haiku-4-5 → claude-sonnet-5" in result.output


def test_diff_explicit_baseline_missing_is_an_error(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_scored_run(evals_dir, "run-new", failure_rate=0.2, judge=_JUDGE_HAIKU)

    result = CliRunner().invoke(
        main,
        ["diff", "run-new", "--baseline", "nope",
         "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
    assert "Baseline not found" in result.output


# ---------------------------------------------------------------------------
# Pre-release review findings
# ---------------------------------------------------------------------------

def test_scaffolded_project_uses_a_pinnable_judge(tmp_path):
    """
    init handed every new project claude-sonnet-5, which rejects temperature —
    so a first run reported "judge parameters ignored by provider" and the judge
    was not pinned, which is the guarantee spec 02 exists to provide.
    """
    from fieldtest.config import parse_and_validate

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        cfg = parse_and_validate(Path("evals/config.yaml"))

    # 5-series models removed sampling parameters; the scaffold must not pick one.
    assert "-5" not in cfg.defaults.model.rsplit("-", 1)[-1] or "haiku" in cfg.defaults.model
    assert cfg.defaults.model == "claude-haiku-4-5"


@pytest.mark.parametrize("template", ["chatbot", "email", "rag"])
def test_templates_use_a_pinnable_judge(tmp_path, template):
    """
    Read the YAML rather than validating it: templates ship blank tags on
    purpose, so a template config is deliberately incomplete until the user
    decides what is right, good or safe. The judge model still has to be one
    that can be pinned.
    """
    import yaml

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(main, ["init", "--template", template], catch_exceptions=False)
        raw = yaml.safe_load(Path("evals/config.yaml").read_text())
    assert raw["defaults"]["model"] == "claude-haiku-4-5"


def test_validate_omits_a_zero_call_projection(tmp_path):
    """A dict whose only value is 0 is truthy — it printed "≈ 0 judge call(s)"."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(main, ["init"], catch_exceptions=False)
        result = runner.invoke(
            main, ["validate", "--config", "evals/config.yaml"], catch_exceptions=False
        )
    assert "judge call(s)" not in result.output


def test_diff_refuses_a_baseline_that_is_the_current_run(tmp_path):
    """A mistyped run id otherwise reports a clean all-unchanged diff."""
    evals_dir = _setup_project(tmp_path)
    _plant_scored_run(evals_dir, "run-a", failure_rate=0.2, judge=_JUDGE_HAIKU)

    result = CliRunner().invoke(
        main,
        ["diff", "run-a", "--baseline", "run-a", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
    assert "same run" in result.output


# ---------------------------------------------------------------------------
# validate reports the provider surface (spec 11)
#
# Before the run, not twenty errored rows into it.
# ---------------------------------------------------------------------------

def _run_validate(evals_dir: Path):
    return CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )


def test_validate_reports_unset_provider_env_vars(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    evals_dir = _setup_project(tmp_path)
    result = _run_validate(evals_dir)
    assert result.exit_code == 0
    assert "⚠ provider 'anthropic' — ANTHROPIC_API_KEY NOT set" in result.output


def test_validate_reports_a_set_provider_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")
    evals_dir = _setup_project(tmp_path)
    result = _run_validate(evals_dir)
    assert "provider 'anthropic' — ANTHROPIC_API_KEY set" in result.output
    assert "NOT set" not in result.output


def test_validate_reports_the_endpoint_for_a_compatible_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("VLLM_KEY", raising=False)
    evals_dir = _setup_project(tmp_path)
    cfg = evals_dir / "config.yaml"
    cfg.write_text(
        cfg.read_text()
        + "providers:\n"
          "  openai_compatible:\n"
          "    base_url: http://localhost:8000/v1\n"
          "    api_key_env: VLLM_KEY\n"
    )
    cfg.write_text(cfg.read_text().replace("provider: anthropic", "provider: openai_compatible"))
    result = _run_validate(evals_dir)
    assert "http://localhost:8000/v1" in result.output
    assert "VLLM_KEY NOT set" in result.output


def test_validate_reports_a_registered_provider(tmp_path):
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "providers.py").write_text(
        "from fieldtest import provider\n\n"
        '@provider("cli-registered-service")\n'
        "def call(model, prompt, gen, retry):\n"
        "    return {'answer': 'Pass', 'reasoning': 'ok'}\n"
    )
    cfg = evals_dir / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace("provider: anthropic", "provider: cli-registered-service")
    )
    result = _run_validate(evals_dir)
    assert "registered in evals/providers.py" in result.output


def test_score_finds_the_config_when_run_from_inside_evals(tmp_path, monkeypatch):
    """
    Reading the fixtures and outputs means cd-ing into evals/, and the docs send
    people there. Running score from that directory failed with "Config not
    found: evals/config.yaml" while config.yaml sat right there.
    """
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)

    monkeypatch.chdir(evals_dir)
    result = CliRunner().invoke(main, ["score", "--set", "full"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_evals_config_still_wins_from_the_project_root(tmp_path, monkeypatch):
    """The fallback must not shadow the normal layout."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    # A decoy in the project root; evals/config.yaml must still be used.
    (tmp_path / "config.yaml").write_text("not: a valid fieldtest config\n")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["score", "--set", "full"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_config_not_found_error_says_you_may_be_inside_evals(tmp_path, monkeypatch):
    """When neither path resolves, the message should name the likely cause."""
    from fieldtest.config import parse_and_validate
    from fieldtest.errors import ConfigError

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("schema_version: 1\n")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(Path("evals/config.yaml"))
    assert "run from the" in str(exc.value)
    assert "--config config.yaml" in str(exc.value)


def test_every_command_has_a_readme_reference_entry():
    """
    The CLI Reference listed nine of ten commands — `calibrate`, a headline
    feature, had a prose section but no entry, so anyone scanning the reference
    would conclude it did not exist.
    """
    import re
    from pathlib import Path

    from fieldtest.cli import main

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    reference = readme[readme.index("## CLI Reference"):]
    documented = set(re.findall(r"^### `fieldtest ([a-z-]+)", reference, re.M))

    commands = set(main.commands)
    missing = sorted(commands - documented)
    assert not missing, f"commands with no CLI Reference entry: {missing}"


def test_every_command_option_is_documented():
    """
    A flag that exists and is undocumented is a feature nobody can find. Read
    from click rather than from a list someone has to remember to update.
    """
    from pathlib import Path

    import click

    from fieldtest.cli import main

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()

    undocumented = []
    for name, cmd in main.commands.items():
        subs = cmd.commands.items() if isinstance(cmd, click.Group) else [(name, cmd)]
        for sub_name, sub in subs:
            for param in sub.params:
                for opt in getattr(param, "opts", []):
                    if opt.startswith("--") and opt != "--help" and opt not in readme:
                        undocumented.append(f"{name} {sub_name} {opt}".replace(f"{name} {name} ", f"{name} "))
    assert not undocumented, f"options absent from the README: {undocumented}"


def test_the_website_mentions_every_command_and_config_key():
    """
    The site is a landing page, not a reference — but a command or setting it
    never names is one a reader has no way to discover.

    It was missing `validate`, `diff` and `history`, and six config keys
    including binary/scale/anchors — the shape of a scored eval, which the
    hero claims as a headline feature ("scored as distributions").
    """
    from pathlib import Path

    from fieldtest.cli import main
    from fieldtest.config import (CalibrationConfig, Config, Defaults, Eval,
                                  FixturesConfig, ProviderSettings, UseCase)

    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()

    missing_cmds = [
        c for c in main.commands
        if f"fieldtest {c}" not in site and f">{c}</span>" not in site
    ]
    assert not missing_cmds, f"commands the website never names: {missing_cmds}"

    groups = {
        "defaults": Defaults, "fixtures": FixturesConfig, "eval": Eval,
        "calibration": CalibrationConfig, "providers": ProviderSettings,
        "config": Config, "use_case": UseCase,
    }
    missing_keys = [
        f"{group}.{field}"
        for group, model in groups.items()
        for field in model.model_fields
        if field not in site
    ]
    assert not missing_keys, f"config keys the website never names: {missing_keys}"


def test_the_website_command_list_is_the_real_help_output():
    """
    The site reproduces `fieldtest --help` verbatim. Adding the `help` command
    made it stale immediately; compare rather than trust.
    """
    import re
    from pathlib import Path

    from click.testing import CliRunner

    from fieldtest.cli import main

    real = CliRunner().invoke(main, ["--help"], catch_exceptions=False).output
    real_block = real[real.index("Commands:"):].strip()

    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    start = site.index("Commands:", site.index('id="commands"'))
    shown = re.sub(r"<[^>]+>", "", site[start:site.index("</pre>", start)]).strip()

    assert shown == real_block, (
        "docs/index.html no longer matches `fieldtest --help`:\n"
        f"--- real ---\n{real_block}\n--- site ---\n{shown}"
    )


@pytest.mark.parametrize("args", [
    ["calibrate", "--help"],
    ["--help", "calibrate"],
    ["help", "calibrate"],
])
def test_all_three_help_forms_show_the_same_command(args):
    """
    `fieldtest --help calibrate` printed the general help and dropped the
    command name silently — an answer to a question nobody asked. All three
    forms people actually type now reach the same place.
    """
    from click.testing import CliRunner

    from fieldtest.cli import main

    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "calibrate [OPTIONS]" in result.output
    assert "--dry-run" in result.output


@pytest.mark.parametrize("args", [["--help", "nope"], ["help", "nope"]])
def test_an_unknown_name_in_a_help_form_exits_nonzero(args):
    """Naming what exists beats printing the general help and hoping."""
    from click.testing import CliRunner

    from fieldtest.cli import main

    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 2
    assert "No such command 'nope'" in result.output
    assert "calibrate" in result.output


def test_plain_help_still_lists_the_commands():
    """The change must not break the ordinary form."""
    from click.testing import CliRunner

    from fieldtest.cli import main

    for args in (["--help"], ["help"]):
        result = CliRunner().invoke(main, args, catch_exceptions=False)
        assert result.exit_code == 0
        assert "Commands:" in result.output
        assert "calibrate" in result.output


def test_a_blank_tag_error_says_it_is_a_template_blank(tmp_path, monkeypatch):
    """
    `fieldtest init --template rag` then `fieldtest validate` used to report
    "Input should be 'right', 'good' or 'safe'" — a validation failure, when it
    is really the scaffold's instruction. Deciding the tag is the point of the
    template; the message should say so.
    """
    from fieldtest.config import parse_and_validate
    from fieldtest.errors import ConfigError

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", "--template", "rag"],
                                catch_exceptions=False)
    assert result.exit_code == 0, result.output

    with pytest.raises(ConfigError) as exc:
        parse_and_validate(tmp_path / "evals" / "config.yaml")
    message = str(exc.value)
    assert "tag is blank" in message
    assert "Templates ship it blank on purpose" in message
    assert "right" in message and "good" in message and "safe" in message


def test_a_genuinely_wrong_tag_keeps_the_ordinary_error(tmp_path):
    """A typo is not a template blank and should not be told it is."""
    from fieldtest.config import parse_and_validate
    from fieldtest.errors import ConfigError

    evals_dir = _setup_project(tmp_path)
    cfg = evals_dir / "config.yaml"
    cfg.write_text(cfg.read_text().replace("tag: right", "tag: correctness"))

    with pytest.raises(ConfigError) as exc:
        parse_and_validate(cfg)
    assert "tag is blank" not in str(exc.value)
    assert "should be" in str(exc.value)


def test_site_github_links_point_at_files_that_exist():
    """
    The site links the walkthrough on GitHub. A renamed or moved file would
    leave a 404 that nothing on this machine notices, because the link resolves
    against the published repo rather than the checkout.

    Uses /blob/HEAD/ rather than /blob/master/, so a default-branch rename does
    not break it either.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    site = (root / "docs" / "index.html").read_text()

    links = re.findall(
        r'href="https://github\.com/[\w.-]+/[\w.-]+/blob/([\w.-]+)/([^"#]+)', site
    )
    assert links, "no GitHub file links on the site — did the walkthrough link move?"

    for ref, path in links:
        assert ref == "HEAD", (
            f"link pins the branch name '{ref}'; use HEAD so a rename cannot break it"
        )
        assert (root / path).is_file(), f"site links {path}, which is not in the repo"


def test_the_website_never_names_a_command_that_does_not_exist():
    """
    The inverse of the coverage check, and the direction that was missing. A
    block titled "All commands" listed `fieldtest list`, which has never been a
    command — the real one is `history`. Checking that every real command is
    mentioned could not catch it.

    Reads only invocations in command styling, so prose like "fieldtest ships
    with…" is not mistaken for a command.
    """
    import re
    from pathlib import Path

    from fieldtest.cli import main

    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    invoked = set(
        re.findall(r'<span class="t-cmd">fieldtest ([a-z][a-z-]*)', site)
    ) | set(re.findall(r"<code>fieldtest ([a-z][a-z-]*)", site))

    unknown = sorted(invoked - set(main.commands))
    assert not unknown, f"the website invokes commands that do not exist: {unknown}"


def test_the_nav_follows_the_order_of_the_page():
    """
    The nav listed Judge before Config while the page has Config first, so
    clicking through it jumped backwards.
    """
    import re
    from pathlib import Path

    site = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    nav = re.findall(r'class="nav-link" href="#([a-z]+)"', site)
    dom = re.findall(r'<section[^>]*id="([a-z]+)"', site)
    assert nav == dom, f"nav order {nav} does not match section order {dom}"


def test_the_site_does_not_claim_the_optimize_skill_ships_in_the_package():
    """
    `/optimize` is tracked in .claude/commands/ and is not in the wheel, so a
    pip user typing it gets nothing. The site said fieldtest "ships with" it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    site = (root / "docs" / "index.html").read_text()

    assert (root / ".claude" / "commands" / "optimize.md").is_file(), (
        "the optimize command is gone; the site still describes it"
    )
    assert "ships with a built-in" not in site
    assert "not part of the pip package" in site
