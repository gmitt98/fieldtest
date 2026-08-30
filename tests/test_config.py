"""
tests/test_config.py

Tests for config.py — parse_and_validate(), Pydantic models, and validators.
Test names match spec §16 exactly.
"""
import textwrap
from pathlib import Path

import pytest

from fieldtest.config import Config, Defaults, parse_and_validate, resolve_dataset_version
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
    yaml = MINIMAL_VALID.replace("schema_version: 1", "schema_version: 3")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "schema_version" in str(exc.value)


def test_schema_version_2_accepted(tmp_path):
    """v2 adds judge provenance and failure rate intervals."""
    yaml = MINIMAL_VALID.replace("schema_version: 1", "schema_version: 2")
    cfg = parse_and_validate(_write_config(tmp_path, yaml))
    assert cfg.schema_version == 2


def test_v1_config_still_accepted(tmp_path):
    """v1 configs load unchanged for one minor release, with v2 defaults filled in."""
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.schema_version == 1
    assert cfg.defaults.confidence_level == 0.95


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
    assert cfg.defaults.model == "claude-haiku-4-5"
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


def test_dataset_version_optional_defaults_to_none(tmp_path):
    """Existing configs that omit `version` continue to load — backwards compatible."""
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.use_cases[0].fixtures.version is None
    assert resolve_dataset_version(cfg) is None


def test_dataset_version_parses_when_set(tmp_path):
    content = """\
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
              version: v2
              sets:
                full: []
        """
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.use_cases[0].fixtures.version == "v2"
    assert resolve_dataset_version(cfg) == "v2"


def test_raw_pydantic_error_not_propagated(tmp_path):
    yaml = MINIMAL_VALID.replace("schema_version: 1", "schema_version: 99")
    with pytest.raises(ConfigError):
        parse_and_validate(_write_config(tmp_path, yaml))
    # Ensure raw ValidationError is NOT raised
    try:
        parse_and_validate(_write_config(tmp_path, yaml))
    except ConfigError:
        pass
    except Exception as e:
        pytest.fail(f"Expected ConfigError, got {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Judge generation config (spec 02)
# ---------------------------------------------------------------------------

def test_judge_temperature_defaults_to_zero():
    """
    The instrument ships locked. A judge left at the provider default samples at
    roughly 1.0, which puts noise into every rate the tool reports.
    """
    d = Defaults()
    assert d.judge_temperature == 0.0
    assert d.judge_seed is None


def test_v1_config_gets_zero_temperature_default(tmp_path):
    """A config written before this spec loads unchanged and gets a pinned judge."""
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.defaults.judge_temperature == 0.0
    assert cfg.defaults.judge_seed is None


def test_judge_temperature_configurable(tmp_path):
    """Anyone who wants the old sampling behavior asks for it explicitly."""
    content = MINIMAL_VALID.replace(
        "    schema_version: 1\n",
        "    schema_version: 1\n"
        "    defaults:\n"
        "      judge_temperature: 1.0\n"
        "      judge_seed: 7\n",
    )
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.defaults.judge_temperature == 1.0
    assert cfg.defaults.judge_seed == 7


def test_judge_retry_defaults_to_shared_policy(tmp_path):
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.defaults.judge_retry.max_attempts == 6
    assert cfg.defaults.judge_retry.initial_delay == 5.0
    assert cfg.defaults.judge_retry.max_delay == 60.0
    assert cfg.defaults.judge_retry.multiplier == 2.0


def test_judge_retry_configurable(tmp_path):
    """A fast local demo and a nightly CI run want different patience."""
    content = MINIMAL_VALID.replace(
        "    schema_version: 1\n",
        "    schema_version: 1\n"
        "    defaults:\n"
        "      judge_retry:\n"
        "        max_attempts: 2\n"
        "        initial_delay: 0.5\n",
    )
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.defaults.judge_retry.max_attempts == 2
    assert cfg.defaults.judge_retry.initial_delay == 0.5
    # Unset fields keep their defaults.
    assert cfg.defaults.judge_retry.max_delay == 60.0


def test_judge_runs_defaults_to_one(tmp_path):
    """Nobody pays the multiplied bill unless they ask for it."""
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.use_cases[0].fixtures.judge_runs == 1


def test_judge_runs_parses_when_set(tmp_path):
    content = MINIMAL_VALID.replace(
        "          directory: fixtures/\n",
        "          directory: fixtures/\n          judge_runs: 3\n",
    )
    cfg = parse_and_validate(_write_config(tmp_path, content))
    assert cfg.use_cases[0].fixtures.judge_runs == 3


# ---------------------------------------------------------------------------
# Human labels (spec 07)
# ---------------------------------------------------------------------------

def _label_project(tmp_path, labels_yaml: str, eval_yaml: str | None = None):
    """Build a project with one fixture carrying a labels block."""
    from fieldtest.config import validate_fixture_labels

    evals = eval_yaml or """\
          - id: ev1
            tag: right
            type: llm
            description: checks something
            pass_criteria: it is fine
            fail_criteria: it is not
"""
    config = f"""\
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
{evals}
    fixtures:
      directory: fixtures/
      sets:
        full: [fix1]
"""
    (tmp_path / "config.yaml").write_text(config)
    (tmp_path / "fixtures").mkdir(exist_ok=True)
    (tmp_path / "fixtures" / "fix1.yaml").write_text(
        "id: fix1\ninputs:\n  q: x\n" + labels_yaml
    )
    cfg = parse_and_validate(tmp_path / "config.yaml")
    return validate_fixture_labels(cfg, tmp_path)


def test_labels_parsed_per_eval_per_run():
    from fieldtest.config import extract_labels

    fixture = {"id": "f1", "labels": {"ev1": {1: "pass", 3: "fail"}}}
    assert extract_labels(fixture) == {("ev1", 1): "pass", ("ev1", 3): "fail"}


def test_fixture_without_labels_extracts_nothing():
    from fieldtest.config import extract_labels

    assert extract_labels({"id": "f1", "inputs": {}}) == {}


def test_valid_labels_report_coverage(tmp_path):
    errors, coverage = _label_project(
        tmp_path, "labels:\n  ev1:\n    1: pass\n    2: fail\n"
    )
    assert errors == []
    assert coverage == {"ev1": 2}


def test_label_type_mismatch_is_config_error(tmp_path):
    errors, _ = _label_project(tmp_path, "labels:\n  ev1:\n    1: 4\n")
    assert any("must be 'pass' or 'fail'" in e for e in errors)


def test_label_references_unknown_eval_is_config_error(tmp_path):
    errors, _ = _label_project(tmp_path, "labels:\n  nope:\n    1: pass\n")
    assert any("unknown eval 'nope'" in e for e in errors)


def test_label_run_number_exceeding_runs_is_config_error(tmp_path):
    errors, _ = _label_project(tmp_path, "labels:\n  ev1:\n    9: pass\n")
    assert any("exceeds runs: 3" in e for e in errors)


SCORED_EVAL = """\
          - id: ev1
            tag: good
            type: llm
            binary: false
            description: rate it
            scale: [1, 5]
            anchors:
              1: bad
              5: great
"""


def test_label_score_outside_scale_is_config_error(tmp_path):
    errors, _ = _label_project(
        tmp_path, "labels:\n  ev1:\n    1: 9\n", eval_yaml=SCORED_EVAL
    )
    assert any("outside scale 1–5" in e for e in errors)


def test_scored_label_must_be_integer(tmp_path):
    errors, _ = _label_project(
        tmp_path, "labels:\n  ev1:\n    1: pass\n", eval_yaml=SCORED_EVAL
    )
    assert any("must be an integer score" in e for e in errors)


def test_valid_scored_label_accepted(tmp_path):
    errors, coverage = _label_project(
        tmp_path, "labels:\n  ev1:\n    1: 4\n", eval_yaml=SCORED_EVAL
    )
    assert errors == []
    assert coverage == {"ev1": 1}


def _plus(block: str) -> str:
    """
    MINIMAL_VALID ends mid-indent, so a raw concatenation shifts the appended
    block. Join on a clean line boundary and re-indent to match.
    """
    body = textwrap.indent(textwrap.dedent(block).strip("\n"), "    ")
    return MINIMAL_VALID.rstrip() + "\n" + body + "\n"

# ---------------------------------------------------------------------------
# Provider surface (spec 11)
# ---------------------------------------------------------------------------

PROVIDERS_BLOCK = """
defaults:
  provider: openai_compatible
  model: llama-3.3-70b-instruct
providers:
  openai_compatible:
    base_url: http://localhost:8000/v1
    api_key_env: VLLM_API_KEY
"""


def test_openai_compatible_config_parses(tmp_path):
    cfg = parse_and_validate(_write_config(tmp_path, _plus(PROVIDERS_BLOCK)))
    assert cfg.defaults.provider == "openai_compatible"
    assert cfg.providers["openai_compatible"].base_url == "http://localhost:8000/v1"
    assert cfg.providers["openai_compatible"].api_key_env == "VLLM_API_KEY"


def test_openai_compatible_without_settings_is_a_config_error(tmp_path):
    yaml = _plus("""
        defaults:
          provider: openai_compatible
          model: llama-3.3-70b-instruct
    """)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "base_url" in str(exc.value)


def test_provider_settings_requires_base_url(tmp_path):
    yaml = _plus("""
        defaults:
          provider: openai_compatible
        providers:
          openai_compatible:
            api_key_env: VLLM_API_KEY
    """)
    with pytest.raises(ConfigError):
        parse_and_validate(_write_config(tmp_path, yaml))


def test_api_key_literal_in_config_is_rejected(tmp_path):
    """A committed secret that is also silently ignored is the worst outcome."""
    yaml = _plus("""
        defaults:
          provider: openai_compatible
        providers:
          openai_compatible:
            base_url: http://localhost:8000/v1
            api_key: sk-literal-secret
    """)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "api_key" in str(exc.value)


def test_config_without_providers_block_is_unaffected(tmp_path):
    """Every existing config predates this feature."""
    cfg = parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert cfg.providers == {}
    assert cfg.defaults.provider == "anthropic"


def _write_providers_py(tmp_path: Path, body: str) -> None:
    (tmp_path / "providers.py").write_text(textwrap.dedent(body))


def test_user_registered_provider_is_valid_in_config(tmp_path):
    _write_providers_py(tmp_path, '''
        from fieldtest import provider

        @provider("my-inference-service")
        def call(model, prompt, gen, retry):
            return {"answer": "pass", "reasoning": "registered"}
    ''')
    yaml = _plus("""
        defaults:
          provider: my-inference-service
          model: whatever
    """)
    cfg = parse_and_validate(_write_config(tmp_path, yaml))
    assert cfg.defaults.provider == "my-inference-service"


def test_user_registered_provider_may_appear_in_a_calibration_panel(tmp_path):
    _write_providers_py(tmp_path, '''
        from fieldtest import provider

        @provider("panel-service")
        def call(model, prompt, gen, retry):
            return {"answer": "pass", "reasoning": "ok"}
    ''')
    yaml = _plus("""
        calibration:
          panel:
            - { provider: anthropic, model: claude-haiku-4-5 }
            - { provider: panel-service, model: local-7b }
    """)
    cfg = parse_and_validate(_write_config(tmp_path, yaml))
    assert [j.provider for j in cfg.calibration.panel] == ["anthropic", "panel-service"]


def test_provider_registration_failure_is_a_config_error(tmp_path):
    _write_providers_py(tmp_path, '''
        from fieldtest import provider
        raise RuntimeError("boom in user code")
    ''')
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, MINIMAL_VALID))
    assert "boom in user code" in str(exc.value)


def test_unregistered_provider_name_still_rejected(tmp_path):
    yaml = _plus("""
        defaults:
          provider: nope-not-registered
    """)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "Unknown provider" in str(exc.value)


def test_registered_provider_does_not_leak_into_another_project(tmp_path):
    """
    A name registered by one project must not resolve for the next, including
    when the next has no providers.py — otherwise defaults.provider silently
    accepts a name that project never defined.
    """
    proj_a = tmp_path / "a"
    proj_a.mkdir()
    _write_providers_py(proj_a, '''
        from fieldtest import provider

        @provider("leaky-service")
        def call(model, prompt, gen, retry):
            return {"answer": "pass", "reasoning": "a"}
    ''')
    cfg_a = _write_config(proj_a, _plus("""
        defaults:
          provider: leaky-service
    """))
    assert parse_and_validate(cfg_a).defaults.provider == "leaky-service"

    proj_b = tmp_path / "b"
    proj_b.mkdir()
    cfg_b = _write_config(proj_b, _plus("""
        defaults:
          provider: leaky-service
    """))
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(cfg_b)
    assert "Unknown provider 'leaky-service'" in str(exc.value)


def test_reloading_the_same_project_keeps_its_providers(tmp_path):
    """Scoping must not break the common case: the same project, twice."""
    _write_providers_py(tmp_path, '''
        from fieldtest import provider

        @provider("stable-service")
        def call(model, prompt, gen, retry):
            return {"answer": "pass", "reasoning": "ok"}
    ''')
    cfg = _write_config(tmp_path, _plus("""
        defaults:
          provider: stable-service
    """))
    assert parse_and_validate(cfg).defaults.provider == "stable-service"
    assert parse_and_validate(cfg).defaults.provider == "stable-service"


def test_no_dead_module_level_constants():
    """
    Every UPPER_CASE module constant must be referenced somewhere other than
    the line defining it.

    VALID_TAGS and VALID_TYPES sat in config.py duplicating the Literal[...]
    on the Eval model, and AVAILABLE_TEMPLATES duplicated a click.Choice list —
    all three dead, all three a second copy of a fact that could drift.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pkg = root / "fieldtest"
    sources = {
        p: p.read_text()
        for p in pkg.rglob("*.py")
        if "datasets" not in p.parts and "demo" not in p.parts
    }
    corpus = "\n".join(sources.values()) + (root / "tests").joinpath("..").as_posix()
    tests = "\n".join(p.read_text() for p in (root / "tests").glob("test_*.py"))

    dead = []
    for path, text in sources.items():
        for m in re.finditer(r"^([A-Z][A-Z0-9_]{2,})\s*[:=]", text, re.M):
            name = m.group(1)
            uses = len(re.findall(rf"\b{name}\b", corpus)) + len(re.findall(rf"\b{name}\b", tests))
            if uses <= 1:
                dead.append(f"{path.relative_to(root)}: {name}")
    assert not dead, f"module constants defined but never used: {dead}"


# ---------------------------------------------------------------------------
# Validators that no test reached (Track D)
#
# Each of these fires on a config a user can plausibly write, and each was
# unreached by the suite — so the message a user meets had never been read
# back by anything.
# ---------------------------------------------------------------------------

def test_regex_eval_without_a_pattern_is_rejected(tmp_path):
    yaml = MINIMAL_VALID.replace('            pattern: "foo"\n', "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "pattern is required for type: regex" in str(exc.value)


def test_regex_eval_without_match_says_what_match_means(tmp_path):
    yaml = MINIMAL_VALID.replace("            match: true\n", "")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    msg = str(exc.value)
    assert "match is required for type: regex" in msg
    # The distinction is the whole point of the field; a user who omitted it
    # does not know which way round it goes.
    assert "must match" in msg and "must not match" in msg


@pytest.mark.parametrize("value", ["0", "1", "95", "-0.5"])
def test_confidence_level_outside_the_unit_interval_is_rejected(tmp_path, value):
    """95 is the plausible mistake: the field reads as a percentage."""
    yaml = MINIMAL_VALID.replace(
        "    use_cases:", f"    defaults:\n      confidence_level: {value}\n    use_cases:"
    )
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "confidence_level must be between 0 and 1" in str(exc.value)


def test_kappa_threshold_outside_minus_one_to_one_is_rejected(tmp_path):
    yaml = MINIMAL_VALID.replace(
        "    use_cases:",
        "    calibration:\n"
        "      kappa_threshold: 60\n"
        "      panel:\n"
        "        - provider: anthropic\n"
        "          model: a\n"
        "        - provider: anthropic\n"
        "          model: b\n"
        "    use_cases:",
    )
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    msg = str(exc.value)
    assert "kappa_threshold must be between -1 and 1" in msg
    assert "not a percentage" in msg


def test_a_panel_listing_the_same_judge_twice_is_rejected(tmp_path):
    yaml = MINIMAL_VALID.replace(
        "    use_cases:",
        "    calibration:\n"
        "      panel:\n"
        "        - provider: anthropic\n"
        "          model: claude-haiku-4-5-20251001\n"
        "        - provider: anthropic\n"
        "          model: claude-haiku-4-5-20251001\n"
        "    use_cases:",
    )
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    msg = str(exc.value)
    assert "twice" in msg
    assert "agrees with itself" in msg


def test_a_config_that_is_not_a_mapping_says_what_it_got(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- one\n- two\n")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(p)
    assert "expected a YAML mapping, got list" in str(exc.value)


def test_unparseable_yaml_is_a_config_error_not_a_traceback(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("schema_version: 1\nsystem: [unclosed\n")
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(p)
    assert str(p) in str(exc.value)


# ---------------------------------------------------------------------------
# Unknown keys (Track C)
# ---------------------------------------------------------------------------

def test_a_key_at_the_wrong_level_says_where_it_belongs(tmp_path):
    """`runs` under the use case ran 5 runs instead of 3, and said nothing."""
    yaml = MINIMAL_VALID.replace(
        "        evals:", "        runs: 3\n        evals:", 1)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    msg = str(exc.value)
    assert "unrecognised key 'runs'" in msg
    assert "use_cases[].fixtures" in msg


def test_the_key_renamed_in_0_3_0_says_what_it_became(tmp_path):
    """An upgrader's `confidence:` was dropped and the default used instead."""
    yaml = MINIMAL_VALID.replace(
        "    use_cases:", "    defaults:\n      confidence: 0.95\n    use_cases:", 1)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    msg = str(exc.value)
    assert "unrecognised key 'confidence'" in msg
    assert "renamed to 'confidence_level'" in msg


def test_a_misspelled_key_is_rejected_rather_than_ignored(tmp_path):
    yaml = MINIMAL_VALID.replace(
        "    use_cases:", "    defaults:\n      judge_temprature: 0.0\n    use_cases:", 1)
    with pytest.raises(ConfigError) as exc:
        parse_and_validate(_write_config(tmp_path, yaml))
    assert "unrecognised key 'judge_temprature'" in str(exc.value)


def test_every_key_in_the_where_it_belongs_table_is_a_real_field():
    """The table names YAML paths, so nothing else stops it going stale."""
    from fieldtest.config import (
        _WHERE_KEYS_BELONG, _RENAMED_KEYS,
        CalibrationConfig, Defaults, FixturesConfig, UseCase,
    )

    models = {
        "use_cases[].fixtures": FixturesConfig,
        "defaults": Defaults,
        "use_cases[]": UseCase,
        "calibration": CalibrationConfig,
    }
    for key, where in _WHERE_KEYS_BELONG.items():
        targets = [w.strip() for w in where.rstrip(".").split(", or ")]
        for target in targets:
            model = models.get(target)
            assert model is not None, f"'{where}' names an unknown location {target!r}"
            assert key in model.model_fields, \
                f"'{key}' is not a field on {model.__name__} ({target})"

    for old, new in _RENAMED_KEYS.items():
        assert any(new in m.model_fields for m in models.values()), \
            f"'{old}' is said to have been renamed to '{new}', which exists nowhere"


def test_shipped_configs_still_load_with_unknown_keys_forbidden():
    """extra=forbid is only safe if nothing fieldtest ships carries a stray key."""
    import fieldtest

    pkg = Path(fieldtest.__file__).parent
    checked = 0
    for sub in ("demo", "datasets"):
        for cfg in sorted((pkg / sub).rglob("*.yaml")):
            if "schema_version" not in cfg.read_text():
                continue
            parse_and_validate(cfg)   # raises if a shipped config has a stray key
            checked += 1
    assert checked >= 5, f"only {checked} shipped configs found — did they move?"
