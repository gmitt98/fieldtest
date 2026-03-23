"""
tests/test_config.py

Tests for config.py — parse_and_validate(), Pydantic models, and validators.
Test names match spec §16 exactly.
"""
import textwrap
from pathlib import Path

import pytest

from fieldtest.config import Config, Defaults, parse_and_validate
from fieldtest.errors import ConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


MINIMAL_VALID = """\
    schema_version: 1
    system:
      name: test system
      domain: test domain
    use_cases:
      - id: uc1
        description: test use case
        evals:
          - id: ev1
            tag: right
            type: regex
            description: checks something
            pattern: "foo"
            match: true
        fixtures:
          directory: fixtures/
          sets:
            full: []
    """


# ---------------------------------------------------------------------------
# Test cases (spec §16)
# ---------------------------------------------------------------------------

def test_valid_minimal_config(tmp_path):
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert isinstance(cfg, Config)
    assert cfg.system.name == "test system"


def test_schema_version_missing(tmp_path):
    yaml = MINIMAL_VALID.replace("schema_version: 1\n", "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "schema_version" in str(exc.value)


def test_schema_version_unsupported(tmp_path):
    yaml = MINIMAL_VALID.replace("schema_version: 1", "schema_version: 2")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "schema_version" in str(exc.value)


def test_eval_tag_invalid(tmp_path):
    yaml = MINIMAL_VALID.replace("tag: right", "tag: wrong")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "tag" in str(exc.value)


def test_eval_type_invalid(tmp_path):
    yaml = MINIMAL_VALID.replace("type: regex", "type: custom").replace(
        'pattern: "foo"\n', ""
    ).replace("match: true\n", "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "type" in str(exc.value)


def test_regex_pattern_missing(tmp_path):
    yaml = MINIMAL_VALID.replace('pattern: "foo"\n', "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "pattern" in str(exc.value)


def test_regex_match_missing(tmp_path):
    yaml = MINIMAL_VALID.replace("match: true\n", "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "match" in str(exc.value)


def test_llm_binary_pass_criteria_missing(tmp_path):
    yaml = """\
        schema_version: 1
        system:
          name: s
          domain: d
        use_cases:
          - id: uc1
            description: d
            evals:
              - id: ev1
                tag: right
                type: llm
                description: desc
                fail_criteria: "bad"
            fixtures:
              directory: fixtures/
              sets:
                full: []
        """
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "pass_criteria" in str(exc.value)


def test_llm_binary_fail_criteria_missing(tmp_path):
    yaml = """\
        schema_version: 1
        system:
          name: s
          domain: d
        use_cases:
          - id: uc1
            description: d
            evals:
              - id: ev1
                tag: right
                type: llm
                description: desc
                pass_criteria: "good"
            fixtures:
              directory: fixtures/
              sets:
                full: []
        """
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "fail_criteria" in str(exc.value)


def test_llm_scored_scale_missing(tmp_path):
    yaml = """\
        schema_version: 1
        system:
          name: s
          domain: d
        use_cases:
          - id: uc1
            description: d
            evals:
              - id: ev1
                tag: good
                type: llm
                binary: false
                description: desc
                anchors:
                  1: bad
                  5: great
            fixtures:
              directory: fixtures/
              sets:
                full: []
        """
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "scale" in str(exc.value)


def test_llm_scored_anchors_missing(tmp_path):
    yaml = """\
        schema_version: 1
        system:
          name: s
          domain: d
        use_cases:
          - id: uc1
            description: d
            evals:
              - id: ev1
                tag: good
                type: llm
                binary: false
                description: desc
                scale: [1, 5]
            fixtures:
              directory: fixtures/
              sets:
                full: []
        """
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "anchors" in str(exc.value)


def test_duplicate_fixture_ids(tmp_path):
    yaml = """\
        schema_version: 1
        system:
          name: s
          domain: d
        use_cases:
          - id: uc1
            description: d
            evals: []
            fixtures:
              directory: fixtures/
              sets:
                full: [foo, bar]
          - id: uc2
            description: d
            evals: []
            fixtures:
              directory: fixtures/
              sets:
                full: [foo, baz]
        """
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "foo" in str(exc.value)


def test_defaults_applied_when_absent(tmp_path):
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.defaults.provider == "anthropic"
    assert cfg.defaults.model == "claude-haiku-3-5-20251001"
    assert cfg.defaults.runs == 5


def test_run_priority_use_case_wins(tmp_path):
    content = """\
        schema_version: 1
        system:
          name: test system
          domain: test domain
        defaults:
          runs: 10
        use_cases:
          - id: uc1
            description: test use case
            evals:
              - id: ev1
                tag: right
                type: regex
                description: checks something
                pattern: "foo"
                match: true
            fixtures:
              directory: fixtures/
              runs: 3
              sets:
                full: []
        """
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.use_cases[0].fixtures.runs == 3


def test_run_priority_defaults_wins(tmp_path):
    content = """\
        schema_version: 1
        system:
          name: test system
          domain: test domain
        defaults:
          runs: 7
        use_cases:
          - id: uc1
            description: test use case
            evals:
              - id: ev1
                tag: right
                type: regex
                description: checks something
                pattern: "foo"
                match: true
            fixtures:
              directory: fixtures/
              sets:
                full: []
        """
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.use_cases[0].fixtures.runs is None  # not set at use_case level
    assert cfg.defaults.runs == 7


def test_run_priority_hardcoded_fallback(tmp_path):
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.use_cases[0].fixtures.runs is None
    assert cfg.defaults.runs == 5  # hardcoded default in Defaults model


def test_raw_pydantic_error_not_propagated(tmp_path):
    yaml = MINIMAL_VALID.replace("schema_version: 1", "schema_version: 99")
    from pydantic import ValidationError as PydanticValidationError
    with pytest.raises(ConfigError):
        parse_and_validate(_write_config(tmp_path, yaml))
    # Ensure raw ValidationError is NOT raised
    try:
        parse_and_validate(_write_config(tmp_path, yaml))
    except ConfigError:
        pass
    except Exception as e:
        pytest.fail(f"Expected ConfigError, got {type(e).__name__}: {e}")
