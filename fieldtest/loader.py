"""
fieldtest/loader.py

Import a Python file from the user's project directory.

Shared by the @rule and @provider registries. Both load user code from a
conventional path next to config.yaml, both memoize by resolved path because
score() is called from several threads during calibration, and both report an
import failure as a ConfigError with a file and line rather than a traceback.
"""
from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import Optional


def import_user_file(
    path: Path,
    module_name: str,
    loaded: set[str],
) -> Optional[object]:
    """
    Execute `path` as a module so its decorators register. No-op if the file
    does not exist or has already been loaded.

    Raises ConfigError on syntax or import error, located at the failing line.
    """
    from fieldtest.errors import ConfigError

    if not path.exists():
        return None

    # Loading the same file twice re-executes user code for no gain, and the
    # calibration panel calls score() from several threads at once.
    resolved = str(path.resolve())
    if resolved in loaded:
        return None
    loaded.add(resolved)

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Config error at {path}: could not load module spec")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SyntaxError as e:
        raise ConfigError(
            f"Failed to import {path}: SyntaxError: {e.msg}\n"
            f"  at {e.filename}:{e.lineno}"
        ) from e
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        last = tb[-1] if tb else None
        loc = f"{last.filename}:{last.lineno}" if last else "unknown"
        raise ConfigError(
            f"Failed to import {path}: {type(e).__name__}: {e}\n"
            f"  at {loc}"
        ) from e
    return module
