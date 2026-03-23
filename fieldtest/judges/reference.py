"""
fieldtest/judges/reference.py

Reference judge — checks output against expected.contains / expected.not_contains.
If fixture has no expected block, returns skipped=True (not an error).
"""
from __future__ import annotations

from fieldtest.config import Eval, ResultRow


def judge_reference(use_case_id: str, eval: Eval, output: str, fixture: dict, run: int) -> ResultRow:
    """
    Check output against fixture.expected.contains / not_contains.
    Skips if no expected block in fixture.
    """
    base = dict(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
    )

    expected = fixture.get("expected")
    if not expected:
        return ResultRow(**base, skipped=True, skip_reason="no expected block in fixture")

    failures = []
    for s in expected.get("contains", []):
        if s not in output:
            failures.append(f"missing: {s}")
    for s in expected.get("not_contains", []):
        if s in output:
            failures.append(f"found forbidden: {s}")

    passed = len(failures) == 0
    detail = "all checks passed" if passed else "; ".join(failures)

    return ResultRow(**base, passed=passed, detail=detail)
