"""
fieldtest/judges/regex_.py

Regex judge. Trailing _ avoids stdlib name clash.
"""
from __future__ import annotations

import re

from fieldtest.config import Eval, ResultRow


def judge_regex(use_case_id: str, eval: Eval, output: str, fixture: dict, run: int) -> ResultRow:
    """
    match=true:  output MUST match pattern to pass.
    match=false: output MUST NOT match pattern to pass.
    """
    matched = bool(re.search(eval.pattern, output))
    passed = matched if eval.match else not matched
    found_str = "found" if matched else "not found"
    detail = f"pattern '{eval.pattern}' {found_str}"

    return ResultRow(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
        passed=passed,
        detail=detail,
    )
