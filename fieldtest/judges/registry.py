"""
fieldtest/judges/registry.py

@rule decorator + _rule_registry + load_rules().
Rules are registered by eval ID. Imported once at startup via importlib.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from fieldtest.loader import import_user_file

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


_loaded_rule_files: set[str] = set()


def load_rules(rules_path: Path) -> None:
    """
    Import rules.py so @rule decorators register. No-op if the file is absent.
    Raises ConfigError on syntax or import error.
    """
    import_user_file(rules_path, "_fieldtest_rules", _loaded_rule_files)
