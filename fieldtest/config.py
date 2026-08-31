"""
fieldtest/config.py

Pydantic models for config.yaml + fixture yaml + result rows.
parse_and_validate() is the single entry point for loading config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import ConfigDict, BaseModel, Field, ValidationError, field_validator, model_validator

from fieldtest.errors import ConfigError
from fieldtest.providers.base import RetryPolicy
from fieldtest.providers.settings import (
    BUILTIN_PROVIDERS,
    VALID_PROVIDERS,
    ProviderSettings,
    validate_provider_name,
)


# ---------------------------------------------------------------------------
# Enums (as str subclasses so they serialise cleanly)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LLMExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output:    str
    label:     Literal["pass", "fail"]
    reasoning: str


class Eval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id:          str
    tag:         Literal["right", "good", "safe"]
    labels:      list[str] = []   # optional free-form analytics labels; multiple allowed
    type:        Literal["rule", "regex", "llm", "reference"]
    description: str
    # type: regex
    pattern: Optional[str]  = None
    match:   Optional[bool] = None   # true = must match; false = must not match
    # type: llm binary
    binary:        bool              = True
    pass_criteria: Optional[str]    = None
    fail_criteria: Optional[str]    = None
    examples:      list[LLMExample] = []
    # type: llm scored
    scale:   Optional[list[int]]      = None   # [min, max]
    anchors: Optional[dict[int, str]] = None
    # Whether the judge is shown the fixture's inputs alongside the output.
    # Default true: an eval whose criteria reference "the context" or "the
    # question" cannot be answered without them, and a judge guessing returns a
    # verdict that looks exactly like a judged one. Set false for evals about the
    # output alone, or to keep a large retrieved context out of every call.
    judge_sees_inputs: bool = True
    # per-eval overrides
    model:    Optional[str] = None
    provider: Optional[str] = None

    @field_validator("provider")
    @classmethod
    def provider_must_be_supported(cls, v: Optional[str]) -> Optional[str]:
        # Same check Defaults.provider and PanelJudge.provider run. Without it,
        # a typo here passed `fieldtest validate` and only surfaced as errored
        # rows twenty judge calls into a paid run.
        if v is None:
            return v
        return validate_provider_name(v, "use_cases[].evals[].provider in config.yaml")

    @model_validator(mode="after")
    def regex_type_required_fields(self) -> "Eval":
        # A model validator, not a field validator on `pattern`/`match`: pydantic
        # skips a field validator when the field is absent, so the omitted case —
        # the one a user actually writes — sailed through, and `score` then died
        # with a TypeError from re.search(None, ...) and a "please file a bug".
        if self.type == "regex":
            if self.pattern is None:
                raise ValueError("pattern is required for type: regex")
            if self.match is None:
                raise ValueError(
                    "match is required for type: regex (true = must match, false = must not match)"
                )
        return self

    @model_validator(mode="after")
    def llm_type_required_fields(self) -> "Eval":
        if self.type == "llm":
            if self.binary:
                if self.pass_criteria is None:
                    raise ValueError("pass_criteria required for type: llm binary")
                if self.fail_criteria is None:
                    raise ValueError("fail_criteria required for type: llm binary")
            else:
                if self.scale is None:
                    raise ValueError("scale required for type: llm scored (binary: false)")
                if self.anchors is None:
                    raise ValueError("anchors required for type: llm scored (binary: false)")
        return self


class FixturesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str           = "fixtures/"
    sets:      dict[str, Union[list[str], str]]  # set_name → [ids] | "dir/*" | "all"
    runs:      Optional[int] = None
    # Judge repetitions per output — how many times the same output is judged,
    # independently of how many outputs the generator produced. Default 1, so an
    # existing config produces identical rows, summaries and deltas, and nobody
    # pays the multiplied bill unless they ask for it.
    judge_runs: int = 1
    # Optional dataset snapshot tag — surfaces in result metadata so that
    # find_baseline() skips runs from a different snapshot, and `fieldtest diff`
    # warns when an explicit baseline crosses versions. Existing configs that
    # omit this field are treated as unversioned (no filter, no warning).
    version:   Optional[str] = None


class UseCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id:          str
    description: str
    evals:       list[Eval]
    fixtures:    FixturesConfig


class SystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name:   str
    domain: str


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "anthropic"
    # Haiku 4.5 rather than a 5-series model: sampling parameters were removed
    # on Sonnet 5 / Opus 5, so defaults.judge_temperature cannot be honoured
    # there. A judge that can be pinned is worth more than a larger one that
    # cannot — and it is cheaper per call, which a judge should be.
    model:    str = "claude-haiku-4-5"
    runs:     int = 5

    # Judge generation settings. Temperature defaults to 0.0 rather than the
    # provider default (typically 1.0) so the instrument is held still: run-to-run
    # movement in a rate should come from the system under test, not the judge.
    judge_temperature: float = 0.0
    judge_seed:        Optional[int] = None

    # Transient-failure policy for judge calls. A fast local demo and a nightly
    # CI run want different patience, so this is configurable rather than fixed.
    judge_retry: RetryPolicy = RetryPolicy()

    # Confidence level for the Wilson score interval on a binary eval's
    # failure_rate — a statistic computed from pass and fail counts.
    #
    # Named confidence_level rather than confidence because in a tool full of
    # LLM judges, "confidence" reads as a model reporting how sure it is of its
    # own answer. It is not that, and it must never become that: self-reported
    # confidence is poorly calibrated, and treating it as a measurement is the
    # error this whole project exists to argue against. No judge is asked
    # anything here; the interval comes from arithmetic on the verdicts.
    confidence_level: float = 0.95

    @field_validator("judge_retry", mode="before")
    @classmethod
    def judge_retry_keys_must_be_real(cls, v):
        # RetryPolicy itself does not forbid extra keys, so a misspelled
        # `max_attemtps:` was silently dropped and the run retried on the
        # defaults the user thought they had overridden.
        if isinstance(v, dict):
            unknown = set(v) - set(RetryPolicy.model_fields)
            if unknown:
                valid = ", ".join(RetryPolicy.model_fields)
                raise ValueError(
                    f"unrecognised key(s) under defaults.judge_retry: "
                    f"{', '.join(sorted(unknown))}. Valid keys: {valid}."
                )
        return v

    @field_validator("judge_retry")
    @classmethod
    def judge_retry_values_must_be_non_negative(cls, v: RetryPolicy) -> RetryPolicy:
        # Negative values load cleanly but break at runtime: max_attempts of -1
        # makes with_retry() skip the judge call entirely (every row errors),
        # and a negative delay crashes the first retry inside time.sleep().
        if v.max_attempts < 0:
            raise ValueError(
                f"defaults.judge_retry.max_attempts must be 0 or more, got "
                f"{v.max_attempts}. 0 means no retries; a negative count would "
                f"skip the judge call itself."
            )
        for name in ("initial_delay", "max_delay", "multiplier"):
            value = getattr(v, name)
            if value < 0:
                raise ValueError(
                    f"defaults.judge_retry.{name} must be 0 or more, got {value}. "
                    f"A negative value produces a negative retry delay, which "
                    f"crashes the run at the first retry."
                )
        return v

    @field_validator("confidence_level")
    @classmethod
    def confidence_level_in_open_unit_interval(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError(
                f"defaults.confidence_level must be between 0 and 1 (exclusive), got {v}."
            )
        return v

    @field_validator("provider")
    @classmethod
    def provider_must_be_supported(cls, v: str) -> str:
        return validate_provider_name(v, "defaults.provider in config.yaml")


class PanelJudge(BaseModel):
    """One judge in a calibration panel."""
    model_config = ConfigDict(extra="forbid")

    provider: str
    model:    str

    @field_validator("provider")
    @classmethod
    def provider_must_be_supported(cls, v: str) -> str:
        return validate_provider_name(v, "calibration.panel in config.yaml")


class CalibrationConfig(BaseModel):
    """
    The judge panel, declared in config and versioned with everything else
    rather than passed as ad hoc CLI flags.
    """
    model_config = ConfigDict(extra="forbid")

    panel: list[PanelJudge]
    # Below this, a judge pair is flagged as agreeing no better than chance.
    # 0.6 is the conventional "substantial agreement" floor.
    kappa_threshold: float = 0.6

    @field_validator("panel")
    @classmethod
    def panel_needs_two_distinct(cls, v: list) -> list:
        if len(v) < 2:
            raise ValueError(
                "calibration.panel needs at least two judges — agreement is a "
                "property of a pair."
            )
        # The same model twice is not two raters. It would agree with itself,
        # pull every eval's disagreement down, and make Fleiss' kappa an invalid
        # computation over a duplicated rater.
        seen: set = set()
        for judge in v:
            key = (judge.provider, judge.model)
            if key in seen:
                raise ValueError(
                    f"calibration.panel lists {judge.provider}/{judge.model} twice. "
                    f"A duplicate judge agrees with itself and inflates every "
                    f"agreement figure."
                )
            seen.add(key)
        return v

    @field_validator("kappa_threshold")
    @classmethod
    def kappa_threshold_in_range(cls, v: float) -> float:
        # Kappa is bounded to [-1, 1]. A threshold of 60 (meaning percent) would
        # otherwise load cleanly and flag a perfect panel as failing.
        if not (-1.0 <= v <= 1.0):
            raise ValueError(
                f"calibration.kappa_threshold must be between -1 and 1, got {v}. "
                f"Kappa is a chance-corrected coefficient, not a percentage."
            )
        return v


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2]
    system:         SystemConfig
    use_cases:      list[UseCase]
    defaults:       Defaults = Field(default_factory=Defaults)
    calibration:    Optional[CalibrationConfig] = None
    # Connection settings per provider name. Absent in every v1 config and in
    # any config using only the three key-from-env providers.
    providers:      dict[str, ProviderSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def configured_providers_have_settings(self) -> "Config":
        """
        openai_compatible names an endpoint, so it cannot be used without one.
        Caught here rather than at the first judge call, which would be twenty
        errored rows into a run.
        """
        used = {self.defaults.provider}
        used.update(
            ev.provider
            for uc in self.use_cases
            for ev in uc.evals
            if ev.provider
        )
        if self.calibration:
            used.update(j.provider for j in self.calibration.panel)

        if "openai_compatible" in used and "openai_compatible" not in self.providers:
            raise ValueError(
                "provider 'openai_compatible' is used but has no settings. Add:\n"
                "  providers:\n"
                "    openai_compatible:\n"
                "      base_url: https://openrouter.ai/api/v1\n"
                "      api_key_env: OPENROUTER_API_KEY"
            )
        return self

    @model_validator(mode="after")
    def fixture_ids_globally_unique(self) -> "Config":
        # A fixture ID can appear in multiple sets of the same use_case (different views).
        # It must NOT appear in two DIFFERENT use_cases — outputs/ uses ID as directory key.
        uc_ids: dict[str, set] = {}
        for uc in self.use_cases:
            ids: set = set()
            for set_val in uc.fixtures.sets.values():
                if isinstance(set_val, list):
                    for fid in set_val:
                        ids.add(fid)
            uc_ids[uc.id] = ids

        seen: dict[str, str] = {}  # fid → first uc_id
        for uc_id, ids in uc_ids.items():
            for fid in ids:
                if fid in seen and seen[fid] != uc_id:
                    raise ValueError(
                        f"Fixture ID '{fid}' appears in both "
                        f"'{seen[fid]}' and '{uc_id}'. "
                        f"Fixture IDs must be globally unique."
                    )
                seen[fid] = uc_id
        return self
    # note: glob/all sets are resolved at runtime, not validated here
    # duplicate detection for glob sets happens in resolve_set() before scoring


# ---------------------------------------------------------------------------
# Result row — one per fixture × eval × run
# ---------------------------------------------------------------------------

class ResultRow(BaseModel):
    use_case:    str
    eval_id:     str
    tag:         str
    labels:      list[str] = []
    type:        str
    fixture_id:  str
    run:         int
    judge_run:   int = 1              # which judge repetition produced this row
    passed:      Optional[bool] = None
    score:       Optional[int]  = None
    floor_hit:   bool           = False
    skipped:     bool           = False   # True when reference eval has no expected block
    skip_reason: Optional[str]  = None    # why skipped; shown in report
    detail:      Optional[str]  = None
    error:       Optional[str]  = None    # populated if judge call failed; row excluded from rates


# ---------------------------------------------------------------------------
# Judge output contracts
# ---------------------------------------------------------------------------

class BinaryJudgeOutput(BaseModel):
    answer:    Literal["Pass", "Fail"]
    reasoning: str


class ScoredJudgeOutput(BaseModel):
    score:     int
    reasoning: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Where a key belongs, for the keys most often written one level off. Listed by
# hand because the message names a YAML path, not a model; a test checks every
# entry is still a real field on the model it points at.
_WHERE_KEYS_BELONG = {
    "runs":             "use_cases[].fixtures, or defaults",
    "judge_runs":       "use_cases[].fixtures",
    "version":          "use_cases[].fixtures",
    "directory":        "use_cases[].fixtures",
    "sets":             "use_cases[].fixtures",
    "provider":         "defaults",
    "model":            "defaults",
    "judge_temperature": "defaults",
    "judge_seed":       "defaults",
    "judge_retry":      "defaults",
    "confidence_level": "defaults",
    "evals":            "use_cases[]",
    "fixtures":         "use_cases[]",
    "panel":            "calibration",
    "kappa_threshold":  "calibration",
}

# Keys an older config may still carry.
_RENAMED_KEYS = {
    "confidence": "confidence_level",
}


def parse_and_validate(config_path: Path) -> Config:
    """Load and validate config.yaml. Raises ConfigError (never raw ValidationError)."""
    try:
        raw = yaml.safe_load(config_path.read_text())
    except FileNotFoundError:
        raise ConfigError(
            f"Config not found: {config_path}\n"
            + (
                f"  There is a config.yaml in {Path.cwd().name}/ — run from the\n"
                f"  parent directory, or use --config config.yaml\n"
                if Path("config.yaml").is_file() else ""
            )
            + "  Run 'fieldtest init' to scaffold a new project, or\n"
            "  use --config to specify a different path."
        )
    except Exception as e:
        raise ConfigError(f"Config error at {config_path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config error at {config_path}: expected a YAML mapping, got {type(raw).__name__}")

    # Before validation, not after: a name registered by @provider has to be
    # accepted by the provider field validator, and providers.py sits next to
    # the config by the same convention rules.py does.
    from fieldtest.providers.registry import load_providers
    load_providers(config_path.parent / "providers.py")

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        # Extract the first error location + message and wrap in ConfigError.
        # Raw Pydantic errors must never propagate to callers.
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc   = " -> ".join(str(p) for p in first["loc"])
            msg   = first["msg"]

            # A blank tag is what the templates ship, deliberately — deciding
            # right/good/safe is the point of the scaffold. The generic Literal
            # error does not say so, so someone who has just run
            # `fieldtest init --template` reads a validation failure rather than
            # an instruction.
            # loc is empty for model-level validators, so index only when
            # there is a field to index.
            field = str(first["loc"][-1]) if first["loc"] else ""
            if field == "tag" and first.get("input") in ("", None):
                msg = (
                    "tag is blank. Templates ship it blank on purpose: choose "
                    "right (is it correct?), good (is it well-formed?) or safe "
                    "(what must never happen?) for each eval."
                )
            # An unrecognised key used to be dropped in silence, so a `runs:`
            # one level too high, or the `confidence:` that 0.3.0 renamed, ran
            # with the default and reported a number the user never asked for.
            # Pydantic's own wording does not say which key or where it belongs.
            if first["type"] == "extra_forbidden":
                msg = f"unrecognised key '{field}'."
                belongs = _WHERE_KEYS_BELONG.get(field)
                if belongs:
                    msg += f" It belongs under {belongs}."
                renamed = _RENAMED_KEYS.get(field)
                if renamed:
                    msg += f" It was renamed to '{renamed}'."

            raise ConfigError(f"Config error at {loc}: {msg}") from exc
        raise ConfigError(f"Config error at {config_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Re-exported from the modules these were split into. Imported at the bottom so
# the models above are defined first, and kept here so that every existing
# `from fieldtest.config import ...` keeps working.
# ---------------------------------------------------------------------------

from fieldtest.fixtures import (  # noqa: E402
    extract_labels,
    load_fixture,
    summarize_file_inputs,
    validate_fixture_labels,
)
from fieldtest.resolve import (  # noqa: E402
    resolve_dataset_version,
    resolve_judge_runs,
    resolve_runs,
    resolve_set,
    use_cases_with_fixtures,
)

__all__ = [
    "BinaryJudgeOutput", "CalibrationConfig", "Config", "Defaults", "Eval",
    "FixturesConfig", "LLMExample", "PanelJudge",
    "ProviderSettings", "ResultRow", "ScoredJudgeOutput", "SystemConfig",
    "UseCase", "BUILTIN_PROVIDERS", "VALID_PROVIDERS",
    "extract_labels", "load_fixture", "parse_and_validate", "summarize_file_inputs",
    "validate_fixture_labels",
    "resolve_dataset_version", "resolve_judge_runs", "resolve_runs", "resolve_set",
    "use_cases_with_fixtures",
]
