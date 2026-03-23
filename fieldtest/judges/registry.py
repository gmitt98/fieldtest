"""
fieldtest/judges/registry.py

@rule decorator + _rule_registry + load_rules().
Rules are registered by eval ID. Imported once at startup via importlib.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from fieldtest.errors import ConfigError

# Module-level registry: {eval_id: callable}
_rule_registry: dict[str, Callable] = {}


def rule(eval_id: str) -> Callable:
    """
    Decorator that registers a rule function by eval ID.

    Usage in user's evals/rules.py:

        from fieldtest import rule

        @rule("no_fabrication")
        def check(output: str, inputs: dict) -> dict:
            ...
            return {"passed": True, "detail": "ok"}
    """
    def decorator(fn: Callable) -> Callable:
        _rule_registry[eval_id] = fn
        return fn
    return decorator


def get_rule(eval_id: str) -> Optional[Callable]:
    """Return registered function or None."""
    return _rule_registry.get(eval_id)


def load_rules(rules_path: Path) -> None:
    """
    Import rules.py via importlib. Populates _rule_registry as a side effect.
    No-op if file doesn't exist.
    Raises ConfigError on syntax or import error.
    """
    if not rules_path.exists():
        return

    spec = importlib.util.spec_from_file_location("_fieldtest_rules", rules_path)
    if spec is None or spec.loader is None:
        raise ConfigError(
            f"Config error at {rules_path}: could not load module spec"
        )

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SyntaxError as e:
        raise ConfigError(
            f"Failed to import {rules_path}: SyntaxError: {e.msg}\n"
            f"  at {e.filename}:{e.lineno}"
        ) from e
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        last = tb[-1] if tb else None
        loc = f"{last.filename}:{last.lineno}" if last else "unknown"
        raise ConfigError(
            f"Failed to import {rules_path}: {type(e).__name__}: {e}\n"
            f"  at {loc}"
        ) from e
