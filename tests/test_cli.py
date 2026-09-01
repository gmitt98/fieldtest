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

import fieldtest
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
               judge: dict | None = None,
               summary: dict | None = None,
               fixture_count: int | None = None,
               no_baseline_reason: str | None = None) -> None:
    """Plant a fake -data.json that the diff command will read."""
    data: dict = {
        "run_id": run_id,
        "set":    "full",
        "summary": summary if summary is not None else {},
        "delta":   {
            "baseline_run_id": baseline_run_id,
            "increased": [],
            "decreased": [],
            "unchanged": [],
        },
    }
    if no_baseline_reason is not None:
        data["delta"]["no_baseline_reason"] = no_baseline_reason
    if dataset_version is not None:
        data["dataset_version"] = dataset_version
    if judge is not None:
        data["judge"] = judge
    if fixture_count is not None:
        data["fixture_count"] = fixture_count
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

def test_scaffolded_project_uses_a_pinnable_judge(tmp_path, monkeypatch):
    """
    init handed every new project claude-sonnet-5, which rejects temperature —
    so a first run reported "judge parameters ignored by provider" and the judge
    was not pinned, which is the guarantee spec 02 exists to provide.
    """
    from fieldtest.config import parse_and_validate

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    cfg = parse_and_validate(tmp_path / "evals" / "config.yaml")

    # 5-series models removed sampling parameters; the scaffold must not pick one.
    assert "-5" not in cfg.defaults.model.rsplit("-", 1)[-1] or "haiku" in cfg.defaults.model
    assert cfg.defaults.model == "claude-haiku-4-5"


@pytest.mark.parametrize("template", ["chatbot", "email", "rag"])
def test_templates_use_a_pinnable_judge(tmp_path, monkeypatch, template):
    """
    Read the YAML rather than validating it: templates ship blank tags on
    purpose, so a template config is deliberately incomplete until the user
    decides what is right, good or safe. The judge model still has to be one
    that can be pinned.
    """
    import yaml

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    runner.invoke(main, ["init", "--template", template], catch_exceptions=False)
    raw = yaml.safe_load((tmp_path / "evals" / "config.yaml").read_text())
    assert raw["defaults"]["model"] == "claude-haiku-4-5"


def test_validate_omits_a_zero_call_projection(tmp_path, monkeypatch):
    """A dict whose only value is 0 is truthy — it printed "≈ 0 judge call(s)"."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
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


def test_documented_template_names_are_real_choices():
    """
    README documented `fieldtest init --template extraction` twice. There is no
    extraction template — extraction is a *demo example*. The documented command
    exits 2.
    """
    import re
    from pathlib import Path

    from fieldtest.templates import AVAILABLE_TEMPLATES

    root = Path(__file__).resolve().parent.parent
    for doc in ("README.md", "docs/index.html"):
        named = set(re.findall(r"--template ([a-z]+)", (root / doc).read_text()))
        unknown = sorted(named - set(AVAILABLE_TEMPLATES))
        assert not unknown, f"{doc} documents templates that do not exist: {unknown}"


def test_documented_commands_parse():
    """
    A recipe documented `fieldtest diff RUN1 RUN2`, which the CLI has never
    accepted — diff takes one RUN_ID plus --baseline. Every fieldtest command
    line in the docs is parsed against click without executing it.
    """
    import shlex
    from pathlib import Path

    import click

    from fieldtest.cli import main

    root = Path(__file__).resolve().parent.parent
    docs = ["README.md", "docs/walkthrough.md"] + [
        str(p.relative_to(root)) for p in (root / "docs" / "recipes").glob("*.md")
    ]

    bad = []
    for doc in docs:
        in_block = False
        for raw in (root / doc).read_text().splitlines():
            if raw.strip().startswith("```"):
                in_block = not in_block
                continue
            if not in_block:
                continue          # prose starting "fieldtest …" is not a command
            line = raw.strip().lstrip("$ ").strip()
            if not line.startswith("fieldtest "):
                continue
            line = line.split("#")[0].strip()
            try:
                argv = shlex.split(line)[1:]
            except ValueError:
                continue
            if not argv or argv[0].startswith("-"):
                continue
            cmd = main.commands.get(argv[0])
            if cmd is None:
                bad.append(f"{doc}: unknown command in {line!r}")
                continue
            if isinstance(cmd, click.Group):
                continue
            # Count positional arguments the command accepts.
            max_args = sum(
                1 for p in cmd.params if isinstance(p, click.Argument)
            )
            # Drop values that belong to preceding options.
            cleaned, skip = [], False
            for i, tok in enumerate(argv[1:]):
                if skip:
                    skip = False
                    continue
                if tok.startswith("--"):
                    param = next((p for p in cmd.params if tok in p.opts), None)
                    if tok == "--help":
                        continue
                    if param is not None and not getattr(param, "is_flag", False):
                        skip = True
                    elif param is None:
                        bad.append(f"{doc}: unknown option {tok} in {line!r}")
                    continue
                cleaned.append(tok)
            if len(cleaned) > max_args:
                bad.append(
                    f"{doc}: {line!r} passes {len(cleaned)} positionals, "
                    f"{argv[0]} takes {max_args}"
                )
    assert not bad, "documented commands the CLI would reject:\n  " + "\n  ".join(bad)


def test_version_flag_reports_the_packaged_version():
    """
    There was no way to ask which version was installed. A QA plan step said to
    run `fieldtest --version` before anything else; the command did not exist.

    Reads installed metadata rather than pyproject, so it reports what is
    actually importable — an editable install with stale metadata will say so,
    which is the truth worth telling.
    """
    from click.testing import CliRunner

    from fieldtest.cli import main

    result = CliRunner().invoke(main, ["--version"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "fieldtest, version" in result.output

    from importlib.metadata import version
    assert version("fieldtest") in result.output


def test_history_says_when_older_result_files_are_not_listed(tmp_path):
    """
    `history` globs `*-data.json`. A long-lived project can have most of its
    history in the pre-0.2 naming — one real project had 24 of 32 — and listing
    the eight it can read, with no word about the rest, reads as "that is all
    there is".
    """
    import json

    evals_dir = _setup_project(tmp_path)
    results = evals_dir / "results"
    (results / "2026-01-01T00-00-00-aaaa-data.json").write_text(json.dumps({
        "run_id": "2026-01-01T00-00-00-aaaa", "set": "full",
        "fixture_count": 1, "summary": {},
    }))
    for name in ("2025-12-01T00-00-00-old1.json", "2025-12-02T00-00-00-old2.json"):
        (results / name).write_text("{}")

    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "2026-01-01T00-00-00-aaaa" in result.output
    assert "2 older result file(s)" in result.output


def test_history_stays_quiet_when_every_run_is_readable(tmp_path):
    """The note must not appear for a project with no legacy files."""
    import json

    evals_dir = _setup_project(tmp_path)
    (evals_dir / "results" / "2026-01-01T00-00-00-aaaa-data.json").write_text(
        json.dumps({"run_id": "2026-01-01T00-00-00-aaaa", "set": "full",
                    "fixture_count": 1, "summary": {}})
    )
    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "older result file" not in result.output


def test_validate_warns_about_sets_that_cannot_be_scored(tmp_path):
    """
    A set declared in one use case and not another cannot be scored: resolve_set
    raises for the use case that lacks it. A real project had three of its five
    sets in that state and nothing said so until the command was spent.
    """
    import textwrap

    two_use_cases = MINIMAL_CONFIG.replace(
        "defaults:",
        textwrap.dedent("""\
              - id: uc2
                description: second use case
                evals:
                  - id: ev_regex2
                    tag: right
                    type: regex
                    description: checks for Go
                    pattern: "Go"
                    match: true
                fixtures:
                  directory: fixtures/
                  sets:
                    full: [fix3]
            defaults:"""),
    )
    evals_dir = _setup_project(tmp_path, config=two_use_cases)
    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "set 'smoke' is declared in 'uc1' but not in 'uc2'" in result.output
    assert "`--set smoke` will fail" in result.output
    # `full` exists in both and must not be flagged.
    assert "set 'full' is declared" not in result.output


def test_validate_stays_quiet_when_every_set_is_shared(tmp_path):
    evals_dir = _setup_project(tmp_path)
    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "will fail" not in result.output


# ---------------------------------------------------------------------------
# view and demo failure paths (Track D)
#
# Neither command had a single CLI test. Between them they hold seven reachable
# failures, all of which a user meets before they meet anything else — `view` is
# what you run after a score, and `demo` is the first command in the README.
# ---------------------------------------------------------------------------

def test_view_names_the_report_it_could_not_find(tmp_path):
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "results").mkdir(exist_ok=True)
    result = CliRunner().invoke(
        main, ["view", "nosuchrun", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "nosuchrun-report.html" in result.output


def test_view_with_no_results_directory_says_what_to_run(tmp_path):
    evals_dir = _setup_project(tmp_path)
    import shutil
    shutil.rmtree(evals_dir / "results")
    result = CliRunner().invoke(
        main, ["view", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "No results found" in result.output
    assert "fieldtest score" in result.output


def test_view_with_results_but_no_html_says_so(tmp_path):
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "results").mkdir(exist_ok=True)
    (evals_dir / "results" / "2026-01-01T00-00-00-aaaa-data.json").write_text("{}")
    result = CliRunner().invoke(
        main, ["view", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "No HTML reports found" in result.output


def test_view_opens_the_newest_report(tmp_path, monkeypatch):
    """The success path, which was also untested."""
    import time

    evals_dir = _setup_project(tmp_path)
    results = evals_dir / "results"
    results.mkdir(exist_ok=True)
    older = results / "2026-01-01T00-00-00-aaaa-report.html"
    newer = results / "2026-02-02T00-00-00-bbbb-report.html"
    older.write_text("<p>old</p>")
    time.sleep(0.01)
    newer.write_text("<p>new</p>")

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    result = CliRunner().invoke(
        main, ["view", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert opened and newer.name in opened[0]


def test_demo_refuses_an_existing_target_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fieldtest-demo").mkdir()
    result = CliRunner().invoke(
        main, ["demo", "--offline"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_demo_live_without_a_key_points_at_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("ANTHROPIC_API_KEY",):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(
        main, ["demo", "--example", "rag"], catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
    assert "--offline" in result.output


def test_diff_with_an_unknown_run_id_names_the_file(tmp_path):
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "results" / "2026-01-01T00-00-00-aaaa-data.json").write_text("{}")
    result = CliRunner().invoke(
        main, ["diff", "nosuchrun", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "Run not found:" in result.output
    assert "nosuchrun-data.json" in result.output


def test_demo_reports_a_failing_score_rather_than_claiming_success(tmp_path, monkeypatch):
    """The subprocess is the whole demo; a silent nonzero would read as success."""
    import subprocess as _sp

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-used")

    class _Failed:
        returncode = 1

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Failed())
    result = CliRunner().invoke(main, ["demo"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "fieldtest score failed" in result.output


def test_an_unexpected_error_prints_a_traceback_and_where_to_file_it():
    """FieldtestError prints one line; anything else is a bug and says so."""
    import click as _click
    from fieldtest.cli_common import _handle_error
    from fieldtest.errors import ConfigError

    runner = CliRunner()

    @_click.command()
    def boom():
        try:
            raise ValueError("something we did not anticipate")
        except Exception as e:
            _handle_error(e)

    result = runner.invoke(boom, [], catch_exceptions=False)
    assert result.exit_code == 1
    assert "Traceback" in result.output
    assert "something we did not anticipate" in result.output
    # The URL must be the real repo: this printed galenmittermann/fieldtest
    # (HTTP 404) while pyproject.toml pointed at gmitt98/fieldtest.
    assert "github.com/gmitt98/fieldtest/issues" in result.output

    @_click.command()
    def expected():
        try:
            raise ConfigError("Config error at x: tag is blank")
        except Exception as e:
            _handle_error(e)

    result = runner.invoke(expected, [], catch_exceptions=False)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "github.com" not in result.output
    assert "tag is blank" in result.output


# ---------------------------------------------------------------------------
# Advertised choices vs what ships (Track D)
#
# `--example` and `--template` are click.Choice-bound, so their "not found"
# branches are unreachable *as long as* every advertised name has a directory
# in the package. These pin that, because the failure mode is a user picking a
# documented option and getting an error instead.
# ---------------------------------------------------------------------------

def test_every_demo_choice_has_a_directory():
    import click as _click
    from fieldtest.cli_project import demo_cmd

    choices = next(
        p.type.choices for p in demo_cmd.params
        if p.name == "example" and isinstance(p.type, _click.Choice)
    )
    demo_root = Path(fieldtest.__file__).parent / "demo"
    missing = [c for c in choices if not (demo_root / c).is_dir()]
    assert not missing, f"--example offers {missing}, which do not ship"


def test_every_template_choice_has_a_file_and_the_help_names_them_all():
    from fieldtest.cli_project import init_cmd
    from fieldtest.templates import AVAILABLE_TEMPLATES

    param = next(p for p in init_cmd.params if p.name == "template")
    tpl_root = Path(fieldtest.__file__).parent / "templates"
    missing = [c for c in param.type.choices if not (tpl_root / f"{c}.yaml").is_file()]
    assert not missing, f"--template offers {missing}, which do not ship"

    # The help text lists them by hand, so it drifts silently when one is added.
    for name in AVAILABLE_TEMPLATES:
        assert name in param.help, f"--template help does not mention '{name}'"


def test_the_wheel_ships_the_data_directories_the_cli_reaches_for():
    """demo/, templates/ and datasets/ are data, not modules — easy to drop."""
    pkg = Path(fieldtest.__file__).parent
    for sub in ("demo", "templates", "datasets"):
        assert (pkg / sub).is_dir(), f"fieldtest/{sub}/ is missing"
        assert any((pkg / sub).iterdir()), f"fieldtest/{sub}/ is empty"


def test_validate_counts_a_fixture_in_two_sets_once(tmp_path):
    """It summed set lengths, so the shipped 3-fixture dataset reported 4."""
    # MINIMAL_CONFIG already lists fix1 in both `smoke` and `full`; the count
    # read 3 for two fixtures and nothing asserted it.
    assert "smoke: [fix1]" in MINIMAL_CONFIG and "full: [fix1, fix2]" in MINIMAL_CONFIG
    evals_dir = _setup_project(tmp_path)
    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "2 explicitly listed fixture(s)" in result.output


def test_requires_python_the_classifiers_and_the_ci_matrix_agree():
    """
    Three files answered "which interpreters are supported" differently:
    requires-python said >= 3.10, the classifiers stopped at 3.12, and CI ran
    3.14 alone. Only the matrix makes any of it true, so all three are compared
    against each other.

    Read with a regex rather than tomllib, which does not exist on 3.10 — the
    oldest interpreter this very test is asserting support for.
    """
    import re

    root = Path(fieldtest.__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()
    workflow = (root / ".github" / "workflows" / "test.yml").read_text()

    floor = re.search(r'requires-python\s*=\s*"[^"]*>=\s*3\.(\d+)', pyproject)
    assert floor, "cannot read requires-python from pyproject.toml"
    lowest = int(floor.group(1))

    claimed = sorted(
        int(m) for m in
        re.findall(r'"Programming Language :: Python :: 3\.(\d+)"', pyproject)
    )
    assert claimed, "pyproject lists no Python version classifiers"

    block = re.search(r"python: \[([^\]]+)\]", workflow)
    assert block, "the CI matrix no longer lists python versions"
    tested = sorted(int(m) for m in re.findall(r"3\.(\d+)", block.group(1)))

    assert claimed[0] == lowest, (
        f"requires-python admits 3.{lowest}, the classifiers start at 3.{claimed[0]}"
    )
    assert claimed == list(range(claimed[0], claimed[-1] + 1)), (
        f"the classifiers skip a version: {claimed}"
    )
    assert claimed == tested, (
        f"classifiers claim {claimed}, CI runs {tested}"
    )


# ---------------------------------------------------------------------------
# Every command the docs tell a reader to type (Track A, made permanent)
#
# Doc drift is the failure mode this project has actually had: `--template
# extraction`, `fieldtest diff RUN1 RUN2`, `fieldtest list`, "four output files"
# when there are five. Each was found by hand, twice. This finds them all, on
# every push, and fails rather than waiting for a reader to hit one.
# ---------------------------------------------------------------------------

def _documentation_files():
    root = Path(fieldtest.__file__).resolve().parent.parent
    files = [root / n for n in
             ("README.md", "CHANGELOG.md", "docs/walkthrough.md",
              "docs/index.html", "docs/philosophy.md")]
    # docs/specs are design records, not instructions. They legitimately discuss
    # commands that do not exist ("if a `fieldtest scan` is ever built") and
    # propose ones that were never accepted. What a reader follows is here.
    files += sorted(root.glob("fieldtest/datasets/*/README.md"))
    files += sorted(root.glob("fieldtest/demo/*/README.md"))
    return [f for f in files if f.exists()]


def _typed_command_lines(path: Path) -> list[str]:
    """
    Lines a reader would type, from code contexts only.

    Prose is excluded ("fieldtest does not run your system" is not an
    invocation), and so are python blocks, where `from fieldtest import rule`
    is an import rather than a command.
    """
    import html as htmlmod
    import re

    text = path.read_text()
    blocks = []
    for fence, body in re.findall(r"```([a-z]*)\n(.*?)```", text, re.S):
        if fence != "python":
            blocks.append(body)
    blocks += re.findall(r"`([^`\n]+)`", text)
    if path.suffix == ".html":
        blocks += [htmlmod.unescape(b) for b in
                   re.findall(r"<(?:code|pre)[^>]*>(.*?)</(?:code|pre)>", text, re.S)]

    out = []
    for block in blocks:
        for line in block.splitlines():
            line = line.strip().lstrip("$ ").strip()
            # A command is typed at the start of a line, not mentioned mid-sentence.
            if line.startswith("fieldtest ") and "←" not in line:
                out.append(line)
    return out


def _cli_surface():
    """
    Commands, their flags, and each flag's allowed values — from the click
    objects, not from parsing --help. Parsing --help missed a flag written
    after a positional argument, and missed flag *values* entirely, which is
    the shape of the `--template extraction` defect that shipped twice.
    """
    import click

    surface = {}
    for name, cmd in main.commands.items():
        flags, choices, subs = set(), {}, set()
        for p in cmd.params:
            flags.update(o for o in getattr(p, "opts", []) if o.startswith("--"))
            flags.update(o for o in getattr(p, "secondary_opts", []) if o.startswith("--"))
            ptype = getattr(p, "type", None)
            if isinstance(ptype, click.Choice):
                for o in getattr(p, "opts", []):
                    if o.startswith("--"):
                        choices[o] = set(ptype.choices)
        sub_detail = {}
        if isinstance(cmd, click.Group):
            subs = set(cmd.commands)
            for sname, scmd in cmd.commands.items():
                sflags, schoices = set(), {}
                for p in scmd.params:
                    sflags.update(o for o in getattr(p, "opts", []) if o.startswith("--"))
                    stype = getattr(p, "type", None)
                    if isinstance(stype, click.Choice):
                        for o in getattr(p, "opts", []):
                            if o.startswith("--"):
                                schoices[o] = set(stype.choices)
                sub_detail[sname] = {"flags": sflags, "choices": schoices}
        surface[name] = {"flags": flags, "choices": choices,
                         "subs": subs, "sub_detail": sub_detail}
    # --help is added by click on every command and never appears in params.
    global_flags = {"--help"} | {o for p in main.params
                                 for o in getattr(p, "opts", []) if o.startswith("--")}
    return surface, global_flags


def test_every_command_the_docs_tell_you_to_type_exists():
    import shlex

    surface, global_flags = _cli_surface()
    assert {"score", "validate", "init", "view"} <= set(surface), sorted(surface)

    problems, checked = [], 0
    for doc in _documentation_files():
        for line in _typed_command_lines(doc):
            try:
                tokens = shlex.split(line, comments=True)
            except ValueError:
                continue
            if len(tokens) < 2:
                continue
            cmd = tokens[1]
            checked += 1
            if cmd.startswith("-"):
                # `fieldtest --version`, `fieldtest --help <command>`
                if cmd not in global_flags:
                    problems.append(f"{doc.name}: no global {cmd} — {line!r}")
                continue
            if cmd not in surface:
                problems.append(f"{doc.name}: '{cmd}' is not a command — {line!r}")
                continue

            spec = surface[cmd]
            rest = tokens[2:]

            # A group's flags live on its subcommand: `dataset use --dest`.
            if spec["subs"] and rest and not rest[0].startswith("-"):
                sub = spec["sub_detail"].get(rest[0])
                if sub:
                    spec = {**spec, "flags": spec["flags"] | sub["flags"],
                            "choices": {**spec["choices"], **sub["choices"]}}
            for i, tok in enumerate(rest):
                if not tok.startswith("--"):
                    continue
                flag, _, inline = tok.partition("=")
                if flag not in spec["flags"] and flag not in global_flags:
                    problems.append(
                        f"{doc.name}: '{cmd}' has no {flag} — {line!r}")
                    continue
                allowed = spec["choices"].get(flag)
                if allowed:
                    value = inline or (rest[i + 1] if i + 1 < len(rest) else None)
                    if value and not value.startswith("-") and value not in allowed:
                        problems.append(
                            f"{doc.name}: '{cmd} {flag} {value}' — allowed: "
                            f"{sorted(allowed)} — {line!r}")

            # `dataset use expense-report`: the subcommand has to exist too.
            if spec["subs"] and rest and not rest[0].startswith("-"):
                if rest[0] not in spec["subs"]:
                    problems.append(
                        f"{doc.name}: '{cmd} {rest[0]}' — {cmd} offers "
                        f"{sorted(spec['subs'])} — {line!r}")

    assert checked >= 100, f"only {checked} documented invocations found — parser broke?"
    assert not problems, "documentation names things the CLI does not have:\n  " + \
        "\n  ".join(problems)


def test_every_command_the_cli_offers_is_documented_somewhere():
    """A command nobody can find is the same as one that does not exist."""
    import re
    import subprocess

    root = Path(fieldtest.__file__).resolve().parent.parent
    ft = str(root / ".venv" / "bin" / "fieldtest")
    if not Path(ft).exists():
        ft = "fieldtest"

    top = subprocess.run([ft, "--help"], capture_output=True, text=True).stdout
    commands = set(re.findall(r"^\s{2}(\w[\w-]*)", top, re.M)) - {"fieldtest", "help"}

    shown = set()
    for doc in _documentation_files():
        for line in _typed_command_lines(doc):
            m = re.match(r"fieldtest\s+([a-z][\w-]*)", line)
            if m:
                shown.add(m.group(1))

    missing = sorted(commands - shown)
    assert not missing, f"the CLI offers these and no doc demonstrates them: {missing}"


# ---------------------------------------------------------------------------
# Commands run without --config (the release audit's blocker)
#
# `fieldtest view` crashed with a TypeError and a "please file a bug" for every
# user who followed the demo's own closing line. The option defaults to None so
# the fallback runs, and the call was never added. Every test written for view
# passed --config, so the whole command was green and broken at once.
# ---------------------------------------------------------------------------

def test_view_without_config_opens_the_report(tmp_path, monkeypatch):
    evals_dir = _setup_project(tmp_path)
    results = evals_dir / "results"
    results.mkdir(exist_ok=True)
    (results / "2026-01-01T00-00-00-aaaa-report.html").write_text("<p>r</p>")

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["view"], catch_exceptions=False)
    assert "Traceback" not in result.output, result.output
    assert result.exit_code == 0, result.output
    assert opened, "view did not open anything"


@pytest.mark.parametrize("command", ["validate", "history", "diff", "view"])
def test_no_config_taking_command_crashes_without_config(tmp_path, monkeypatch, command):
    """
    A command may fail without --config; it may not raise. The distinction is
    the whole point: `Path(None)` is a bug, "no results found" is an answer.
    """
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "results").mkdir(exist_ok=True)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, [command], catch_exceptions=False)
    assert "Traceback" not in result.output, f"{command} raised:\n{result.output}"
    assert "file a bug" not in result.output, f"{command} reported a bug:\n{result.output}"


def test_every_command_resolves_its_config_default_the_same_way():
    """
    view was the only one of seven that did not call _default_config_path().
    It read `Path(config_path)` directly on an option whose default is None.
    """
    import inspect
    import re

    from fieldtest import cli, cli_project, cli_reports

    offenders = []
    for module in (cli, cli_project, cli_reports):
        src = inspect.getsource(module)
        for m in re.finditer(r"\ndef (\w+)\((.*?)\n(.*?)(?=\ndef |\Z)", src, re.S):
            name, body = m.group(1), m.group(3)
            if "config_path" not in m.group(2) and "config_path" not in body:
                continue
            uses_direct = re.search(r"Path\(config_path\)(?!\s+if)", body)
            if uses_direct and "_default_config_path" not in body:
                offenders.append(name)

    assert not offenders, (
        f"these read Path(config_path) without the None fallback: {offenders}")
# The numbers `fieldtest history` prints
#
# The history tests assert the header and two judge model names. None reads a
# rate, so the RIGHT / GOOD / SAFE columns could go blank on every row.
# ---------------------------------------------------------------------------

def _history_row(output: str, run_id: str) -> list[str]:
    """The one line for a run, split into columns."""
    line = next(l for l in output.splitlines() if l.startswith(run_id))
    return line.split()


def test_history_prints_the_tag_pass_rates(tmp_path):
    """
    The three columns are pass rates per tag, matching the run's own Tag Health
    table under the same RIGHT / GOOD / SAFE headings. They used to be failure
    rates, so `history` reported 12% for a run its report called 95% — the same
    label meaning opposite things in two artifacts of one tool.

    An eval whose rate is None — every judge call errored — has no rate to pool
    and must stay out rather than emptying the column.
    """
    evals_dir = _setup_project(tmp_path)
    _plant_run(
        evals_dir, "2026-08-27T10-39-43-e327", None,
        summary={
            "uc1": {
                "right": {"ev1": {"failure_rate": 0.2},
                          "ev_all_errored": {"failure_rate": None}},
                "good":  {"ev2": {"failure_rate": 0.0}},
                "safe":  {"ev3": {"failure_rate": 0.5}},
            }
        },
    )

    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    columns = _history_row(result.output, "2026-08-27T10-39-43-e327")
    # 0.2 → 80% pass, 0.0 → 100%, 0.5 → 50%.
    assert columns[-3:] == ["80%", "100%", "50%"]


def test_history_dashes_a_tag_with_no_rates(tmp_path):
    """A dash has to keep meaning "nothing to report" for a rate to mean anything."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(
        evals_dir, "2026-08-27T10-39-43-e327", None,
        summary={"uc1": {"right": {"ev1": {"failure_rate": 0.5}}}},
    )

    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )

    columns = _history_row(result.output, "2026-08-27T10-39-43-e327")
    assert columns[-3:] == ["50%", "—", "—"]


def test_allow_partial_on_a_complete_run_is_not_reported_as_partial(tmp_path):
    """
    --allow-partial is permission to continue, not a claim that something was
    missing. The negative HTML case only ever ran without the flag, so the
    conjunction that decides this was never evaluated with both operands set.
    """
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)

    result = _run_score(evals_dir, extra_args=["--allow-partial"])
    assert result.exit_code == 0

    data = json.loads(
        next((evals_dir / "results").glob("*-data.json")).read_text()
    )
    assert data["partial"] is False
    assert data["partial_details"] == []

    report = next((evals_dir / "results").glob("*-report.md")).read_text()
    assert "PARTIAL" not in report
# Edge-case error handling (0.3.0 adversarial audit — edges lens)
#
# The bar: a user error produces a clear message, never a traceback with
# "please file a bug", and never a silently wrong result.
# ---------------------------------------------------------------------------

CONFIG_WITH_LLM_EVAL = """\
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
      - id: never_leaks
        tag: safe
        type: llm
        description: no pii
        pass_criteria: no pii present
        fail_criteria: pii present
    fixtures:
      directory: fixtures/
      sets:
        smoke: [fix1]
        full: [fix1, fix2]
      runs: 2
      judge_runs: {judge_runs}
defaults:
  provider: anthropic
  model: claude-haiku-4-5
  runs: 2
"""


def test_judge_runs_zero_is_rejected_not_silently_dropped(tmp_path):
    """judge_runs: 0 used to delete every LLM eval from the run — a declared
    `safe` guardrail vanished from the report with exit 0."""
    evals_dir = _setup_project(
        tmp_path, config=CONFIG_WITH_LLM_EVAL.format(judge_runs=0)
    )
    _write_outputs(evals_dir, "fix1", runs=2)
    result = _run_score(evals_dir, set_name="smoke")
    assert result.exit_code == 1
    assert "judge_runs" in result.output
    assert "Please file a bug" not in result.output
    # No green empty result set written
    assert not list((evals_dir / "results").glob("*-data.json"))


def test_runs_zero_is_rejected_not_a_green_empty_result(tmp_path):
    """runs: 0 used to write a non-partial result set measuring nothing, which
    passed every documented CI gate."""
    config = MINIMAL_CONFIG.replace("      runs: 2\n", "      runs: 0\n")
    evals_dir = _setup_project(tmp_path, config=config)
    result = _run_score(evals_dir, set_name="full")
    assert result.exit_code == 1
    assert "runs" in result.output
    assert "Please file a bug" not in result.output
    assert not list((evals_dir / "results").glob("*-data.json"))


def test_concurrency_zero_is_a_clean_error(tmp_path):
    """--concurrency 0 used to reach ThreadPoolExecutor and print a raw
    ValueError traceback plus the bug-report URL."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir, extra_args=["--concurrency", "0"])
    assert result.exit_code == 1
    assert "concurrency" in result.output
    assert "Traceback" not in result.output
    assert "Please file a bug" not in result.output


def test_non_utf8_output_file_is_a_clean_error_naming_the_file(tmp_path):
    """A binary/latin-1 output used to print a raw UnicodeDecodeError traceback
    that never named the offending file."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    (evals_dir / "outputs" / "fix1" / "run-1.txt").write_bytes(b"hi \xff\xfe binary")
    result = _run_score(evals_dir)
    assert result.exit_code == 1
    assert "run-1.txt" in result.output
    assert "Traceback" not in result.output
    assert "Please file a bug" not in result.output


def test_readonly_results_dir_is_a_clean_error(tmp_path):
    """A read-only results/ used to complete the whole run, then print a raw
    PermissionError traceback and discard every judge verdict."""
    import os
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    results_dir = evals_dir / "results"
    results_dir.chmod(0o500)
    try:
        result = _run_score(evals_dir)
    finally:
        results_dir.chmod(0o700)
    assert result.exit_code == 1
    assert "could not be written" in result.output
    assert "Traceback" not in result.output
    assert "Please file a bug" not in result.output


def test_glob_set_loads_fixtures_from_subdirectories(tmp_path):
    """The scaffolded layout — directory: fixtures/ with a golden/* set — could
    never load its fixtures: resolve_set returns bare stems and the runner
    looked only at fixtures/<stem>.yaml."""
    config = MINIMAL_CONFIG.replace(
        "        smoke: [fix1]\n", "        smoke: [fix1]\n        regression: golden/*\n"
    )
    evals_dir = _setup_project(tmp_path, config=config)
    (evals_dir / "fixtures" / "golden").mkdir()
    (evals_dir / "fixtures" / "golden" / "fix3.yaml").write_text(
        "id: fix3\ninputs:\n  text: sub\n"
    )
    _write_outputs(evals_dir, "fix3", runs=2)
    result = _run_score(evals_dir, set_name="regression")
    assert result.exit_code == 0, result.output
    assert "fix3" in result.output


def test_duplicate_fixture_stem_across_subdirs_is_a_clean_error(tmp_path):
    """Two fixtures with the same stem in different subdirectories cannot be
    told apart by a bare id — say so instead of picking one."""
    config = MINIMAL_CONFIG.replace(
        "        smoke: [fix1]\n", "        smoke: [fix3]\n"
    )
    evals_dir = _setup_project(tmp_path, config=config)
    for sub in ("golden", "variations"):
        (evals_dir / "fixtures" / sub).mkdir()
        (evals_dir / "fixtures" / sub / "fix3.yaml").write_text(
            "id: fix3\ninputs:\n  text: dup\n"
        )
    _write_outputs(evals_dir, "fix3", runs=2)
    result = _run_score(evals_dir, set_name="smoke")
    assert result.exit_code == 1
    assert "ambiguous" in result.output
    assert "Please file a bug" not in result.output


def test_labels_survive_a_fixture_id_that_differs_from_its_filename(tmp_path):
    """Labels were keyed by filename stem while every judge stamps rows with
    the fixture's internal id — a renamed file silently discarded every human
    label that validate had just counted."""
    evals_dir = _setup_project(tmp_path)
    (evals_dir / "fixtures" / "fix1.yaml").write_text(
        "id: totally-different\n"
        "inputs:\n"
        "  text: hello world\n"
        "labels:\n"
        "  ev_regex:\n"
        "    1: fail\n"
        "    2: fail\n"
    )
    _write_outputs(evals_dir, "fix1", runs=2)  # contains "Go" → judge passes
    result = _run_score(evals_dir, set_name="smoke")
    assert result.exit_code == 0
    data = json.loads(
        next((evals_dir / "results").glob("*-data.json")).read_text(encoding="utf-8")
    )
    stats = data["summary"]["uc1"]["right"]["ev_regex"]
    assert stats.get("labeled_runs") == 2
    assert stats.get("judge_false_pass") == 2


def test_html_report_survives_a_script_tag_in_a_judge_detail(tmp_path):
    """A literal </script> in a rule/judge detail used to terminate the
    embedded RUN_DATA block, execute the injected markup, and dump the rest of
    the page's JS as visible text."""
    evals_dir = _setup_project(tmp_path, config=MINIMAL_CONFIG_RULE)
    payload = "</scr" + "ipt><scr" + "ipt>window.PWNED=1</scr" + "ipt><b>x</b>"
    (evals_dir / "rules.py").write_text(
        "from fieldtest import rule\n"
        "@rule('has_content')\n"
        "def check(output, inputs):\n"
        f"    return {{'passed': False, 'detail': {payload!r}}}\n"
    )
    _write_outputs(evals_dir, "fix1", runs=1)
    result = _run_score(evals_dir)
    assert result.exit_code == 0
    html = next((evals_dir / "results").glob("*-report.html")).read_text(
        encoding="utf-8"
    )
    # Exactly one script element: the injected close tag must not survive raw.
    closer = "</scr" + "ipt>"
    assert html.count(closer) == 1
    assert "<scr" + "ipt>window.PWNED" not in html


def test_html_report_escapes_config_text(tmp_path):
    """A use-case description containing markup landed verbatim in the DOM."""
    config = MINIMAL_CONFIG.replace(
        "    description: test use case\n",
        "    description: desc <script>window.UC_PWNED=1</script> end\n",
    )
    evals_dir = _setup_project(tmp_path, config=config)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    result = _run_score(evals_dir)
    assert result.exit_code == 0
    html = next((evals_dir / "results").glob("*-report.html")).read_text(
        encoding="utf-8"
    )
    assert "<script>window.UC_PWNED" not in html
    assert "&lt;script&gt;window.UC_PWNED" in html


def test_result_rows_are_written_in_deterministic_order(tmp_path):
    """Rows landed in thread-completion order, so two identical runs produced
    byte-different -data.json/-data.csv artifacts. A rule that finishes run 2
    before run 1 forces the completion order to invert."""
    evals_dir = _setup_project(tmp_path, config=MINIMAL_CONFIG_RULE.replace(
        "      runs: 1\n", "      runs: 2\n"
    ))
    (evals_dir / "rules.py").write_text(
        "import time\n"
        "from fieldtest import rule\n"
        "@rule('has_content')\n"
        "def check(output, inputs):\n"
        "    time.sleep(float(output.strip()))\n"
        "    return {'passed': True, 'detail': 'ok'}\n"
    )
    out_dir = evals_dir / "outputs" / "fix1"
    out_dir.mkdir(parents=True)
    (out_dir / "run-1.txt").write_text("0.5")   # run 1 finishes last
    (out_dir / "run-2.txt").write_text("0.0")
    result = _run_score(evals_dir)
    assert result.exit_code == 0
    data = json.loads(
        next((evals_dir / "results").glob("*-data.json")).read_text(encoding="utf-8")
    )
    runs_in_order = [r["run"] for r in data["rows"]]
    assert runs_in_order == sorted(runs_in_order)


def test_python_dash_m_offers_every_command(tmp_path):
    """Command registration sat below the __main__ guard, so
    `python -m fieldtest.cli demo` reported "No such command 'demo'" while
    the console script offered it."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(fieldtest.__file__).resolve().parents[1])
    proc = subprocess.run(
        [sys.executable, "-m", "fieldtest.cli", "demo", "--help"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "demo" in proc.stdout


def test_the_demos_closing_instructions_actually_work(tmp_path, monkeypatch):
    """
    Twice now the demo has ended by naming a command that fails from the
    directory it leaves you in: first `fieldtest view` crashed outright, then it
    resolved config from the wrong place and found nothing. The closing lines
    are the most-followed instructions the tool prints; run them.
    """
    import re

    monkeypatch.chdir(tmp_path)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    runner = CliRunner()
    result = runner.invoke(
        main, ["demo", "--example", "rag", "--offline"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    tail = result.output.strip().splitlines()[-6:]
    cd_line = next((l for l in tail if l.strip().startswith("cd ")), None)
    assert cd_line, f"the demo does not tell you where to cd:\n{tail}"

    target = tmp_path / cd_line.strip().removeprefix("cd ").strip()
    assert target.is_dir(), f"the demo says 'cd {target.name}' and it does not exist"

    commands = [re.match(r"\s*(fieldtest [a-z]+)", l).group(1).split()[1]
                for l in tail if re.match(r"\s*fieldtest [a-z]+", l)]
    assert commands, f"the demo names no commands to run:\n{tail}"

    monkeypatch.setattr("webbrowser.open", lambda url: True)
    monkeypatch.chdir(target)
    for cmd in commands:
        # A line that names a credential is telling you it needs one; running it
        # without is not a broken instruction. Every other line must just work.
        line = next(l for l in tail if f"fieldtest {cmd}" in l)
        if "API_KEY" in line:
            continue
        r = runner.invoke(main, [cmd], catch_exceptions=False)
        assert "Traceback" not in r.output, f"'{cmd}' raised:\n{r.output}"
        assert "No results found" not in r.output, (
            f"the demo tells you to run '{cmd}' and it finds nothing:\n{r.output}")
        assert r.exit_code == 0, f"'{cmd}' exited {r.exit_code}:\n{r.output}"


# ---------------------------------------------------------------------------
# Destroying the user's work (third audit round)
#
# `clean` deleted more than it named, and did it in directories that were not
# fieldtest projects at all. `outputs/` is gitignored by `init`, so what it took
# was unrecoverable.
# ---------------------------------------------------------------------------

def test_clean_refuses_a_directory_that_is_not_a_fieldtest_project(tmp_path, monkeypatch):
    """
    _default_config_path() falls back to ./config.yaml, and `config.yaml` beside
    an `outputs/` directory describes most ML projects ever written.
    `clean --outputs` deleted their checkpoints and exited 0.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("database:\n  host: localhost\n")
    (tmp_path / "outputs" / "checkpoints").mkdir(parents=True)
    weights = tmp_path / "outputs" / "checkpoints" / "model.bin"
    weights.write_text("model weights")

    result = CliRunner().invoke(main, ["clean", "--outputs"], catch_exceptions=False)
    assert result.exit_code == 1, result.output
    assert weights.exists(), "clean deleted a non-fieldtest project's outputs"
    assert "not a fieldtest config" in result.output


def test_clean_names_every_file_it_will_delete(tmp_path, monkeypatch):
    """It counted only *.txt and then rmtree'd the whole tree."""
    evals = _setup_project(tmp_path)
    outputs = evals / "outputs"
    (outputs / "fix1").mkdir(parents=True, exist_ok=True)
    (outputs / "fix1" / "run-1.txt").write_text("a run")
    (outputs / "notes.md").write_text("MY NOTES")
    (outputs / "manual").mkdir(exist_ok=True)
    (outputs / "manual" / "day1.json").write_text("precious")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["clean"], input="n\n", catch_exceptions=False)
    out = result.output
    assert "3 file(s)" in out, f"the count does not match reality:\n{out}"
    for named in ("notes.md", "day1.json", "run-1.txt"):
        assert named in out, f"clean does not name {named} before deleting it:\n{out}"
    assert (outputs / "notes.md").exists(), "declining still deleted"


def test_clean_results_removes_only_the_five_artifacts_of_a_run(tmp_path, monkeypatch):
    """Globbing {run_id}-* took a write-up the user had named after a run."""
    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    for i in range(1, 26):
        rid = f"2026-01-{i:02d}T10-00-00-aaaa"
        for suffix in ("data.json", "data.csv", "report.md", "report.csv", "report.html"):
            (results / f"{rid}-{suffix}").write_text("{}")
    writeup = results / "2026-01-01T10-00-00-aaaa-my-writeup.md"
    writeup.write_text("MY ANALYSIS")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["clean", "--results", "--keep", "20"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert writeup.exists(), "clean deleted a file it never counted or named"


def test_clean_refuses_a_symlinked_outputs_directory(tmp_path, monkeypatch):
    evals = _setup_project(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "important.txt").write_text("PRECIOUS")
    outputs = evals / "outputs"
    if outputs.exists():
        import shutil
        shutil.rmtree(outputs)
    outputs.symlink_to(real)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["clean", "--outputs"], catch_exceptions=False)
    assert "Traceback" not in result.output
    assert "symlink" in result.output
    assert (real / "important.txt").read_text() == "PRECIOUS"


def test_dataset_use_dest_naming_a_file_says_so(tmp_path, monkeypatch):
    """exists() is true for a file; iterdir() then raised NotADirectoryError."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "myevals"
    target.write_text("my config")

    result = CliRunner().invoke(
        main, ["dataset", "use", "expense-report", "--dest", "myevals"],
        catch_exceptions=False)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "is a file, not a directory" in result.output
    assert target.read_text() == "my config"


def test_init_force_says_it_replaced_your_config(tmp_path, monkeypatch):
    """The output was byte-identical to scaffolding an empty directory."""
    monkeypatch.chdir(tmp_path)
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "config.yaml").write_text("# 300 lines of my carefully written evals\n")

    result = CliRunner().invoke(main, ["init", "--force"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "replaced the existing" in result.output, (
        f"--force overwrote a config and said nothing:\n{result.output}")


def test_clean_keeps_the_most_recent_runs_not_the_alphabetically_last(tmp_path, monkeypatch):
    """
    Result files were ordered by filename. Run ids are timestamps so that looks
    equivalent — until a file that is not a timestamp sits beside them. The
    bundled `demo-offline-*` set sorts above every `2026-…` run because "d" >
    "2", so in a demo directory `clean --results --keep 1` deleted both of the
    user's real runs and kept the shipped one.
    """
    import os
    import time

    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)

    def write(stem: str, when: float):
        for suffix in ("data.json", "data.csv", "report.md", "report.csv", "report.html"):
            f = results / f"{stem}-{suffix}"
            f.write_text("{}")
            os.utime(f, (when, when))

    now = time.time()
    write("demo-offline", now - 3600)          # shipped, oldest
    write("2026-08-31T11-00-00-aaaa", now - 60)
    write("2026-08-31T11-01-00-bbbb", now)     # the newest real run

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["clean", "--results", "--keep", "1"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    survivors = {p.name for p in results.glob("*-data.json")}
    assert survivors == {"2026-08-31T11-01-00-bbbb-data.json"}, (
        f"clean kept {survivors}; the newest run is the one to keep")


def test_history_tag_rates_are_the_pass_rates_the_report_shows(tmp_path, monkeypatch):
    """
    history printed the mean failure rate under headings the report uses for
    pass rate: a run the report called RIGHT 95% appeared as RIGHT 12%.
    """
    import json
    import re

    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    (results / "2026-08-31T12-00-00-aaaa-data.json").write_text(json.dumps({
        "run_id": "2026-08-31T12-00-00-aaaa",
        "set": "full", "fixture_count": 3, "runs": 3,
        "summary": {"uc1": {"right": {
            # 1 failure in 21 outputs → 95% pass
            "a": {"failure_rate": 0.05, "total_runs": 20},
            "b": {"failure_rate": 0.0,  "total_runs": 1},
        }}},
    }))

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["history"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    row = [l for l in result.output.splitlines() if "2026-08-31T12-00-00" in l]
    assert row, result.output
    pct = re.findall(r"(\d+)%", row[0])
    assert pct, f"no percentage in the history row: {row[0]!r}"
    assert pct[0] == "95", (
        f"history shows {pct[0]}% where the pooled pass rate is 95% — "
        f"it is printing the failure rate")


# ---------------------------------------------------------------------------
# A run that measured nothing must not report success
# ---------------------------------------------------------------------------

def _llm_only_project(tmp_path: Path) -> Path:
    evals = tmp_path / "evals"
    (evals / "fixtures").mkdir(parents=True)
    (evals / "outputs" / "f1").mkdir(parents=True)
    (evals / "fixtures" / "f1.yaml").write_text("id: f1\ndescription: d\ninputs:\n  q: hi\n")
    (evals / "outputs" / "f1" / "run-1.txt").write_text("hello there")
    (evals / "config.yaml").write_text("""schema_version: 1
system:
  name: s
  domain: d
use_cases:
  - id: uc1
    description: only llm evals, so nothing scores without a key
    evals:
      - id: is_polite
        tag: good
        type: llm
        description: is the output polite
        pass_criteria: it is polite
        fail_criteria: it is rude
    fixtures:
      directory: fixtures/
      runs: 1
      sets:
        full: [f1]
""")
    return evals


def test_score_fails_when_every_judge_call_errored(tmp_path, monkeypatch):
    """
    The README declines to fail on a high failure rate — the tool measures, you
    judge. But a run where every judge call errored has no rate at all; nothing
    was measured, and exiting 0 told CI that it had been.
    """
    evals = _llm_only_project(tmp_path)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    result = CliRunner().invoke(
        main, ["score", "--config", str(evals / "config.yaml"), "--set", "full"],
        catch_exceptions=False)
    assert result.exit_code == 1, f"a run that scored nothing exited 0:\n{result.output}"
    assert "nothing was scored" in result.output


def test_score_fails_when_the_llm_half_wholly_failed_beside_a_passing_regex(tmp_path, monkeypatch):
    """
    The gate asked "did anything score at all", so one passing regex disarmed it
    while every call to the judge failed. On the bundled email demo that is 27
    of 27 judge calls dead and exit 0, against a README that promises exit 1.
    A deterministic eval is not a judge call and cannot stand in for one.
    """
    evals = _llm_only_project(tmp_path)
    cfg = evals / "config.yaml"
    cfg.write_text(cfg.read_text().replace("""      - id: is_polite""",
"""      - id: says_hello
        tag: right
        type: regex
        description: greets
        pattern: hello
        match: true

      - id: is_polite"""))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    result = CliRunner().invoke(
        main, ["score", "--config", str(cfg), "--set", "full"],
        catch_exceptions=False)
    assert result.exit_code == 1, (
        f"every judge call failed; a passing regex must not disarm the gate:\n"
        f"{result.output}")
    assert "nothing was scored" in result.output


def test_score_succeeds_on_a_clean_offline_run(tmp_path, monkeypatch):
    """The ordinary case must not have become a failure."""
    evals = _setup_project(tmp_path)
    _write_outputs(evals, "fix1", 2)
    _write_outputs(evals, "fix2", 2)
    result = _run_score(evals)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Round 3 — CLI findings
# ---------------------------------------------------------------------------

_NESTED_LABEL_CONFIG = """\
schema_version: 2
system:
  name: test system
  domain: test domain
defaults:
  runs: 2
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
        regression: "golden/*"
"""


def test_validate_sees_labels_in_the_layout_init_scaffolds(tmp_path):
    """
    `fieldtest init` puts fixtures in fixtures/golden/ under `directory:
    fixtures/`. validate_fixture_labels globbed the declared directory flat, so
    it reported a clean config with no labels while `score` over the same
    project printed a Judge vs Human Labels table.
    """
    evals_dir = _setup_project(tmp_path, config=_NESTED_LABEL_CONFIG)
    golden = evals_dir / "fixtures" / "golden"
    golden.mkdir(parents=True, exist_ok=True)
    (golden / "g1.yaml").write_text(
        "id: g1\ninputs:\n  q: x\nlabels:\n  ev1:\n    1: pass\n    2: fail\n"
        "  nonexistent_eval:\n    1: pass\n"
    )

    result = CliRunner().invoke(
        main, ["validate", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert "human labels:" in result.output, result.output
    assert "ev1: 2 labeled run(s)" in result.output, result.output
    assert "unknown eval 'nonexistent_eval'" in result.output, result.output


def _one_run_project(tmp_path):
    """A project with exactly one scored run."""
    evals_dir = _setup_project(tmp_path)
    _write_outputs(evals_dir, "fix1", runs=2)
    _write_outputs(evals_dir, "fix2", runs=2)
    assert _run_score(evals_dir).exit_code == 0
    return evals_dir


def test_diff_says_there_is_no_baseline_rather_than_printing_none(tmp_path):
    """
    delta['baseline_run_id'] is present and None, so the '—' default was
    unreachable and the line read `Baseline:  None`.
    """
    evals_dir = _one_run_project(tmp_path)
    result = CliRunner().invoke(
        main, ["diff", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Baseline:  None" not in result.output, result.output
    assert "no earlier run" in result.output, result.output
    # "No comparable evals found between runs" reads as two runs that shared
    # nothing; there is only one run.
    assert "No comparable evals found between runs" not in result.output, result.output
    assert "only run" in result.output, result.output


def test_diff_names_evals_present_in_only_one_run(tmp_path):
    """
    build_delta skips an eval missing from either side, so an added or removed
    eval vanished from the diff without a word.
    """
    import time

    evals_dir = _one_run_project(tmp_path)
    cfg = evals_dir / "config.yaml"
    cfg.write_text(cfg.read_text().replace(
        """        match: true
    fixtures:""",
        """        match: true
      - id: ev_added
        tag: safe
        type: regex
        description: checks for love
        pattern: "love"
        match: true
    fixtures:"""))
    time.sleep(0.01)
    assert _run_score(evals_dir).exit_code == 0

    result = CliRunner().invoke(
        main, ["diff", "--config", str(cfg)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "uc1/ev_added" in result.output, (
        f"an eval only in the current run was silently dropped:\n{result.output}")


def test_history_does_not_call_a_calibration_file_an_old_format_result(tmp_path):
    """
    `fieldtest calibrate` writes {run_id}-calibration.json into results/.
    history's legacy glob is every *.json that is not *-data.json, so it called
    a file fieldtest had just written one that predates the current naming.
    """
    import json

    evals_dir = _setup_project(tmp_path)
    results = evals_dir / "results"
    (results / "2026-01-01T00-00-00-aaaa-data.json").write_text(json.dumps({
        "run_id": "2026-01-01T00-00-00-aaaa", "set": "full",
        "fixture_count": 1, "summary": {},
    }))
    (results / "2026-01-02T00-00-00-bbbb-calibration.json").write_text(
        json.dumps({"run_id": "2026-01-02T00-00-00-bbbb", "kind": "calibration"})
    )

    result = CliRunner().invoke(
        main, ["history", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "older result file" not in result.output, result.output
    assert "1 calibration run(s)" in result.output, result.output


def test_dataset_use_prints_a_next_step_that_works_for_a_custom_dest(tmp_path, monkeypatch):
    """
    `score` resolves evals/config.yaml, so the bare command printed after
    --dest myevals failed with "Config not found: evals/config.yaml".
    """
    import re

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["dataset", "use", "expense-report", "--dest", "myevals"],
        catch_exceptions=False)
    assert result.exit_code == 0, result.output

    m = re.search(r"Run it now \(no API key needed\):\s+fieldtest (.+)", result.output)
    assert m, result.output
    args = m.group(1).split()

    # Run exactly what it printed.
    run = CliRunner().invoke(main, args, catch_exceptions=False)
    assert "Config not found" not in run.output, (
        f"the printed next step cannot find the dataset it just copied:\n{run.output}")


def test_dataset_use_keeps_the_bare_command_for_the_default_dest(tmp_path, monkeypatch):
    """The default lands on evals/, where `fieldtest score` resolves it."""
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["dataset", "use", "expense-report"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "fieldtest score --set full" in result.output, result.output
    assert "--config" not in result.output, result.output


def test_view_accepts_any_run_id_history_prints(tmp_path, monkeypatch):
    """
    Filename and run id are not always the same string: the bundled demo ships
    demo-offline-*.html whose run_id is a timestamp. `history` printed that id
    and `view <id>` rejected it, so the two commands disagreed about what a run
    is called.
    """
    import json

    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    (results / "demo-offline-data.json").write_text(json.dumps({
        "run_id": "2026-08-27T10-39-39-7dbd", "set": "full",
        "fixture_count": 1, "runs": 1, "summary": {},
    }))
    (results / "demo-offline-report.html").write_text("<p>report</p>")

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    monkeypatch.chdir(tmp_path)

    listed = CliRunner().invoke(main, ["history"], catch_exceptions=False)
    assert "2026-08-27T10-39-39-7dbd" in listed.output, listed.output

    result = CliRunner().invoke(
        main, ["view", "2026-08-27T10-39-39-7dbd"], catch_exceptions=False)
    assert result.exit_code == 0, (
        f"view rejected the id history printed:\n{result.output}")
    assert opened and "demo-offline-report.html" in opened[0]


def test_diff_reads_a_baseline_whose_filename_is_not_its_run_id(tmp_path, monkeypatch):
    """
    diff rebuilt the baseline path from the run id, so with the demo's results
    it loaded nothing and warned "baseline predates judge tracking" about a run
    that records its judge.
    """
    import json

    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)

    judge = {"provider": "anthropic", "model": "m", "fingerprint": "aaaa1111"}
    (results / "demo-offline-data.json").write_text(json.dumps({
        "run_id": "2026-08-27T10-00-00-old", "set": "full", "judge": judge,
        "fixture_count": 1, "runs": 1,
        "summary": {"uc1": {"right": {"e": {"failure_rate": 0.0, "total_runs": 2}}}},
    }))
    (results / "2026-08-28T10-00-00-new-data.json").write_text(json.dumps({
        "run_id": "2026-08-28T10-00-00-new", "set": "full", "judge": judge,
        "fixture_count": 1, "runs": 1,
        "summary": {"uc1": {"right": {"e": {"failure_rate": 0.0, "total_runs": 2}}}},
        "delta": {"baseline_run_id": "2026-08-27T10-00-00-old",
                  "increased": [], "decreased": [], "unchanged": ["e"]},
    }))

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["diff"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "predates judge tracking" not in result.output, (
        f"the baseline records its judge; diff could not find the file:\n{result.output}")


def test_no_test_module_imports_a_python_311_only_stdlib_module():
    """
    `import tomllib` at module level takes a whole test file down on 3.10, which
    this project supports and CI runs. It has now been introduced twice — once
    by me, once by an agent — and each time CI caught it after the fact.
    """
    import ast

    ONLY_311_PLUS = {"tomllib"}
    offenders = []
    for path in sorted((Path(fieldtest.__file__).resolve().parent.parent / "tests").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:            # module level only; guarded imports are fine
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in ONLY_311_PLUS:
                    offenders.append(f"{path.name}:{node.lineno} imports {n}")

    assert not offenders, (
        "these are 3.11+ and the package supports 3.10:\n  " + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Round four: what the fixing broke, and what it missed
# ---------------------------------------------------------------------------

def test_init_force_never_destroys_an_existing_gitignore(tmp_path, monkeypatch):
    """
    --force replaced .gitignore wholesale. A file carrying `.env` and `*.pem`
    became the single line `outputs/`, so the next `git add .` staged the
    user's secrets — and the output said "outputs/ excluded from git", which
    reads as a creation notice.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n*.pem\n")
    (tmp_path / "config.yaml").write_text("model:\n  lr: 3e-4\n")

    result = CliRunner().invoke(main, ["init", "--dir", ".", "--force"],
                                catch_exceptions=False)
    assert result.exit_code == 0, result.output

    lines = {l.strip() for l in (tmp_path / ".gitignore").read_text().splitlines()}
    assert ".env" in lines and "*.pem" in lines, "init destroyed the user's ignores"
    assert "outputs/" in lines, "init did not add its own entry"
    assert "appended" in result.output, "the output does not say it appended"


def test_init_template_force_says_it_replaced_your_config(tmp_path, monkeypatch):
    """The warning existed on the non-template branch only."""
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(main, ["init"], catch_exceptions=False).exit_code == 0
    (tmp_path / "evals" / "config.yaml").write_text("system: mine\ndomain: mine\n")

    result = CliRunner().invoke(
        main, ["init", "--template", "chatbot", "--force"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "replaced the existing" in result.output, (
        f"--template --force overwrote a config silently:\n{result.output}")


def test_clean_works_in_a_freshly_scaffolded_template_project(tmp_path, monkeypatch):
    """
    Templates ship `tag: ""` on purpose, so requiring full validation made
    clean refuse to work in the project `init --template` had just created,
    while claiming there was nothing there to remove.
    """
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(main, ["init", "--template", "chatbot"], catch_exceptions=False)
    outputs = tmp_path / "evals" / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "a.txt").write_text("a run")

    result = CliRunner().invoke(main, ["clean"], input="n\n", catch_exceptions=False)
    assert "nothing here" not in result.output, (
        f"clean refused a real fieldtest project:\n{result.output}")
    assert "a.txt" in result.output, result.output


def test_demo_onto_a_dangling_symlink_says_so(tmp_path, monkeypatch):
    """exists() is False for a dangling link, so copytree raised instead."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b1").symlink_to(tmp_path / "nonexistent")

    result = CliRunner().invoke(main, ["demo", "--offline", "--dir", "b1"],
                                catch_exceptions=False)
    assert "Traceback" not in result.output, result.output
    assert "already exists" in result.output


def test_optional_provider_floors_are_usable_with_current_httpx():
    """
    `openai>=1.0` had the same `proxies` break the anthropic floor was raised
    for: under default resolution the client raises TypeError on construction
    and every judge call becomes an errored row. Bisected to 1.55.3.
    """
    import re

    root = Path(fieldtest.__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text()
    m = re.search(r'"openai>=(\d+)\.(\d+)\.(\d+)"', text)
    assert m, "the openai extra no longer declares a three-part floor"
    assert tuple(int(g) for g in m.groups()) >= (1, 55, 3), (
        f"openai floor {m.group(0)} passes `proxies` to httpx and cannot construct "
        f"a client under httpx>=0.28")


def test_score_succeeds_when_only_some_judge_calls_errored(tmp_path, monkeypatch):
    """
    The distinction the gate exists to draw: a judge that answered sometimes is
    a measurement with holes and exits 0. Only a total outage exits 1.
    """
    from unittest.mock import patch

    evals = _llm_only_project(tmp_path)
    cfg = evals / "config.yaml"
    cfg.write_text(cfg.read_text().replace("      runs: 1", "      runs: 2"))
    (evals / "outputs" / "f1" / "run-2.txt").write_text("hello again")

    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"answer": "Pass", "reasoning": "fine"}
        return {"error": "provider blew up"}

    with patch("fieldtest.judges.llm.call_judge_llm", side_effect=flaky):
        result = CliRunner().invoke(
            main, ["score", "--config", str(cfg), "--set", "full"],
            catch_exceptions=False)

    assert result.exit_code == 0, (
        f"one judge call succeeded; this is a partial measurement:\n{result.output}")


def test_a_deterministic_only_project_never_trips_the_judge_gate(tmp_path, monkeypatch):
    """No llm evals means no judge calls to fail; the gate must stay silent."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    evals = _setup_project(tmp_path)
    _write_outputs(evals, "fix1", 2)
    _write_outputs(evals, "fix2", 2)
    result = _run_score(evals)
    assert result.exit_code == 0, result.output
    assert "judge call(s) failed" not in result.output


# ---------------------------------------------------------------------------
# diff command — why there is no baseline
#
# The reason a baseline was rejected is computed at score time and stored on
# the run's delta as no_baseline_reason; report.py and html.py both print it.
# `diff` asserted "no earlier run to compare against" and "<id> is the only run
# in <dir>" instead — both false whenever the baseline was rejected rather than
# absent (a different set, a bumped dataset version, a changed judge).
# ---------------------------------------------------------------------------

REASON_V1_V2 = "the last run used dataset version v1, this one uses v2"


def test_diff_header_gives_the_stored_reason_there_is_no_baseline(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version="v1")
    _plant_run(evals_dir, "run-new", dataset_version="v2",
               no_baseline_reason=REASON_V1_V2)

    result = CliRunner().invoke(
        main, ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert f"Baseline:  none — {REASON_V1_V2}" in result.output
    assert "no earlier run to compare against" not in result.output


def test_diff_does_not_call_a_run_the_only_one_when_a_baseline_was_rejected(tmp_path):
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version="v1")
    _plant_run(evals_dir, "run-new", dataset_version="v2",
               no_baseline_reason=REASON_V1_V2)

    result = CliRunner().invoke(
        main, ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "is the only run in" not in result.output
    assert f"Nothing to compare — no usable baseline: {REASON_V1_V2}." in result.output


def test_diff_does_not_call_a_run_the_only_one_when_no_reason_was_stored(tmp_path):
    """No stored reason is not evidence that the directory holds one run."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-old", dataset_version=None)
    _plant_run(evals_dir, "run-new", dataset_version=None)

    result = CliRunner().invoke(
        main, ["diff", "run-new", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "is the only run in" not in result.output
    assert "no earlier run to compare against" not in result.output
    assert "1 other run(s) are present" in result.output


def test_diff_still_says_only_run_for_a_genuine_first_run(tmp_path):
    """The honest case must keep its sentence — this is not a blanket removal."""
    evals_dir = _setup_project(tmp_path)
    _plant_run(evals_dir, "run-first", dataset_version=None)

    result = CliRunner().invoke(
        main, ["diff", "run-first", "--config", str(evals_dir / "config.yaml")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Baseline:  none — no earlier run to compare against" in result.output
    assert "run-first is the only run in" in result.output


def test_init_rejects_an_unknown_template_before_scaffolding_anything(tmp_path):
    """
    Pins the invariant that made cli_project's template-existence check dead
    code: click.Choice rejects an unknown name with exit 2 and the command body
    never runs, so nothing is written. NOTE: this test passes with and without
    that check removed — the removal deletes unreachable code and changes no
    behaviour. It guards the removal rather than proving it.
    """
    target = tmp_path / "evals"
    result = CliRunner().invoke(
        main, ["init", "--dir", str(target), "--template", "nosuch"],
        catch_exceptions=False,
    )
    assert result.exit_code == 2, result.output
    assert "is not one of" in result.output
    assert not target.exists(), "a rejected template left the destination scaffolded"


# ---------------------------------------------------------------------------
# Errors that were swallowed, then reported as something untrue (Phase 4)
#
# The coverage census classified 255 unreached regions. Six were REACHABLE and
# misbehaved, and all six are one class: a bare `except Exception` that discards
# the real error and lets the code state something false in its place.
# ---------------------------------------------------------------------------

def test_validate_reports_a_rules_import_failure_not_a_missing_decorator(tmp_path, monkeypatch):
    """
    It said "no @rule('x') registered" when the decorators were there and the
    file had failed to import — sending the user to add what already existed.
    """
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(main, ["dataset", "use", "expense-report"], catch_exceptions=False)
    rules = tmp_path / "evals" / "rules.py"
    rules.write_text("import nonexistent_helper_module\n" + rules.read_text())

    result = CliRunner().invoke(main, ["validate"], catch_exceptions=False)
    assert "did not import" in result.output, result.output
    assert "nonexistent_helper_module" in result.output
    assert "no @rule(" not in result.output, (
        f"validate still blames a missing decorator:\n{result.output}")


def test_validate_warns_about_a_set_score_will_refuse(tmp_path, monkeypatch):
    """
    A malformed set value was dropped from both the projection and the
    warnings, so validate exited 0 and `score --set <that>` then failed. The
    check lived inside the cost projection, which is skipped entirely for a
    use case with no llm evals — so a rules-only project never reached it.
    """
    import re

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(main, ["dataset", "use", "expense-report"], catch_exceptions=False)
    cfg = tmp_path / "evals" / "config.yaml"
    cfg.write_text(re.sub(r"(\n\s+)smoke:", r"\1bogus: golden\1smoke:", cfg.read_text(), count=1))

    result = CliRunner().invoke(main, ["validate"], catch_exceptions=False)
    assert "bogus" in result.output, (
        f"validate blessed a set score refuses:\n{result.output}")
    assert "will fail" in result.output


def test_history_names_a_result_file_it_could_not_read(tmp_path, monkeypatch):
    """
    history's own rule: anything present but unlisted must be counted and
    named. A truncated -data.json was the one case it stayed silent about.
    """
    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    (results / "2026-01-01T00-00-00-good-data.json").write_text(
        '{"run_id": "2026-01-01T00-00-00-good", "set": "full", '
        '"fixture_count": 1, "runs": 1, "summary": {}}')
    (results / "2026-01-02T00-00-00-bad-data.json").write_text("not json {{{")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["history"], catch_exceptions=False)
    assert "could not be read" in result.output, (
        f"an unreadable run vanished silently:\n{result.output}")
    assert "2026-01-02T00-00-00-bad-data.json" in result.output


def test_diff_says_a_baseline_is_unreadable_rather_than_old(tmp_path, monkeypatch):
    """
    Swallowing the parse error left baseline_data empty, and diff then asserted
    the baseline "predates judge tracking" about a run that records its judge.
    """
    import json

    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    judge = {"provider": "anthropic", "model": "m", "fingerprint": "aaaa1111"}
    (results / "baseA-data.json").write_text('{"run_id": "baseA", "sum')
    (results / "2026-01-02T00-00-00-cur-data.json").write_text(json.dumps({
        "run_id": "2026-01-02T00-00-00-cur", "set": "full", "judge": judge,
        "fixture_count": 1, "runs": 1, "summary": {},
        "delta": {"baseline_run_id": "baseA", "increased": [], "decreased": [],
                  "unchanged": []},
    }))

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["diff"], catch_exceptions=False)
    assert "could not be read" in result.output, result.output
    assert "predates judge tracking" not in result.output, (
        f"diff asserted the baseline is old when it is unreadable:\n{result.output}")


def test_diff_refuses_an_unreadable_current_run(tmp_path, monkeypatch):
    """It raised JSONDecodeError as a traceback."""
    evals = _setup_project(tmp_path)
    results = evals / "results"
    results.mkdir(exist_ok=True)
    (results / "2026-01-01T00-00-00-aaaa-data.json").write_text('{"run_id": "x", "sum')

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["diff"], catch_exceptions=False)
    assert "Traceback" not in result.output, result.output
    assert "Cannot read" in result.output
    assert result.exit_code == 1


def test_demo_runs_score_through_this_interpreter_not_the_path(tmp_path):
    """
    subprocess.run(["fieldtest", ...]) resolved the console script from PATH, so
    a working install invoked as `python -m fieldtest.cli`, or a venv whose bin
    is not on PATH, produced a traceback and a bug link — after copytree, so the
    suggested --offline retry then hit the dest-exists guard.
    """
    # demo_cmd is a click Command, not a function, so read the module source.
    from fieldtest import cli_project

    src = Path(cli_project.__file__).read_text()
    assert '["fieldtest", "score"' not in src, (
        "demo resolves the console script from PATH")
    assert "sys.executable" in src, "demo should run score through this interpreter"


def test_no_command_discards_an_error_and_carries_on_silently():
    """
    All six Phase-4 defects were one shape: a bare `except Exception` whose body
    neither re-raises nor tells the user, letting the code state something false
    in its place. This lists the survivors so a new one has to be argued for.

    The allowlist is the point: each entry is a place where silence is the right
    behaviour, and adding to it is a decision someone has to make deliberately.
    """
    import ast

    ALLOWED = {
        # display fallbacks — a malformed run id must not stop the listing
        ("cli_reports.py", "ts_display"),
        ("results/html.py", "timestamp"),
        # repr of an object whose own repr raised, inside an error message
        ("judges/dispatch.py", "unreprable"),
        # retry loop: the exception is carried in `last` and re-raised after
        ("providers/base.py", "last"),
        # a baseline that cannot be read is not a baseline; build_delta returns
        # the empty delta and the report says there is none
        ("results/aggregator.py", "return empty"),
        # scanning for a baseline: an unreadable candidate is skipped, and
        # `history` is the command that reports unreadable files
        ("results/aggregator.py", "continue"),
        # labels are optional; a fixture that will not parse is reported by
        # validate and score, not by the calibration label collector
        ("results/calibration_analysis.py", "continue"),
        ("cli_project.py", "pass"),
        # cost projection only; whether a set resolves is checked for every use
        # case separately, and that check does report
        ("cli.py", "continue"),
    }

    root = Path(fieldtest.__file__).resolve().parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path) or "/demo/" in str(path) or "/datasets/" in str(path):
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or getattr(node.type, "id", "") == "Exception"
            if not broad:
                continue
            body = " ".join(ast.unparse(b) for b in node.body)
            # A handler that references the exception is doing something with
            # it — re-raising, printing it, storing it to report later, or
            # returning it as an error row. Discarding is the case where the
            # exception is never mentioned again.
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            uses_exception = bool(node.name) and node.name in names
            # `append(` counts: recording the file for the command to report
            # later surfaces the fact even when the exception object is dropped.
            surfaces = uses_exception or any(k in body for k in (
                "raise", "click.echo", "sys.exit", "_handle_error", "append("))
            if surfaces:
                continue
            if any(rel == f and marker in body for f, marker in ALLOWED):
                continue
            offenders.append(f"{rel}:{node.lineno}  {body[:60]}")

    assert not offenders, (
        "these discard an error and continue without telling anyone — the shape "
        "of every Phase-4 defect:\n  " + "\n  ".join(offenders))
