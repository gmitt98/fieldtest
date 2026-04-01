"""
fieldtest/config.py

Pydantic models for config.yaml + fixture yaml + result rows.
parse_and_validate() is the single entry point for loading config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from fieldtest.errors import ConfigError


# ---------------------------------------------------------------------------
# Enums (as str subclasses so they serialise cleanly)
# ---------------------------------------------------------------------------

class EvalTag(str):
    pass


class EvalType(str):
    pass


VALID_TAGS  = {"right", "good", "safe"}
VALID_TYPES = {"rule", "regex", "llm", "reference"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LLMExample(BaseModel):
    output:    str
    label:     Literal["pass", "fail"]
    reasoning: str


class Eval(BaseModel):
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
    # per-eval overrides
    model:    Optional[str] = None
    provider: Optional[str] = None

    @field_validator("pattern")
    @classmethod
    def pattern_required_for_regex(cls, v, info):
        if info.data.get("type") == "regex" and v is None:
            raise ValueError("pattern is required for type: regex")
        return v

    @field_validator("match")
    @classmethod
    def match_required_for_regex(cls, v, info):
        if info.data.get("type") == "regex" and v is None:
            raise ValueError(
                "match is required for type: regex (true = must match, false = must not match)"
            )
        return v

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
    directory: str = "fixtures/"
    sets:      dict[str, Union[list[str], str]]  # set_name → [ids] | "dir/*" | "all"
    runs:      Optional[int] = None


class UseCase(BaseModel):
    id:          str
    description: str
    evals:       list[Eval]
    fixtures:    FixturesConfig


class SystemConfig(BaseModel):
    name:   str
    domain: str


VALID_PROVIDERS = {"anthropic", "openai"}


class Defaults(BaseModel):
    provider: str = "anthropic"
    model:    str = "claude-sonnet-4-20250514"
    runs:     int = 5

    @field_validator("provider")
    @classmethod
    def provider_must_be_supported(cls, v: str) -> str:
        if v not in VALID_PROVIDERS:
            supported = ", ".join(sorted(VALID_PROVIDERS))
            raise ValueError(
                f"Unknown provider '{v}'. v1 supports: {supported}. "
                f"Check defaults.provider in config.yaml."
            )
        return v


class Config(BaseModel):
    schema_version: Literal[1]
    system:         SystemConfig
    use_cases:      list[UseCase]
    defaults:       Defaults = Field(default_factory=Defaults)

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

def parse_and_validate(config_path: Path) -> Config:
    """Load and validate config.yaml. Raises ConfigError (never raw ValidationError)."""
    try:
        raw = yaml.safe_load(config_path.read_text())
    except FileNotFoundError:
        raise ConfigError(
            f"Config not found: {config_path}\n"
            f"  Run 'fieldtest init' to scaffold a new project, or\n"
            f"  use --config to specify a different path."
        )
    except Exception as e:
        raise ConfigError(f"Config error at {config_path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config error at {config_path}: expected a YAML mapping, got {type(raw).__name__}")

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        # Extract the first error location + message and wrap in ConfigError.
        # Raw Pydantic errors must never propagate to callers.
        errors = exc.errors()
        if errors:
            loc   = " -> ".join(str(p) for p in errors[0]["loc"])
            msg   = errors[0]["msg"]
            raise ConfigError(f"Config error at {loc}: {msg}") from exc
        raise ConfigError(f"Config error at {config_path}: {exc}") from exc


def load_fixture(fixture_path: Path) -> dict:
    """Load a YAML fixture file. Raises ConfigError if id field missing."""
    try:
        data = yaml.safe_load(fixture_path.read_text())
    except Exception as e:
        raise ConfigError(f"Config error at {fixture_path}: {e}") from e
    if not isinstance(data, dict) or "id" not in data:
        raise ConfigError(f"Config error at {fixture_path}: fixture missing required 'id' field")
    return data


def resolve_runs(config: Config, use_case: UseCase) -> int:
    """Return effective run count. use_case wins, then defaults, then hardcoded 5."""
    if use_case.fixtures.runs is not None:
        return use_case.fixtures.runs
    return config.defaults.runs  # Defaults model defaults to 5


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
