#!/usr/bin/env python3
"""
Spec 12's behavioural acceptance, as a script rather than a memory.

Re-introduces each defect the test tiers were built to catch, one at a time,
and checks the suite fails. A tier that cannot catch the bug it was built for
is decoration, and nothing else in the suite would notice if one rotted.

    python scripts/verify_tiers.py

Exits non-zero if any mutation goes undetected. Restores every file it touches,
including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass
class Mutation:
    """A defect that actually shipped, and the tier that should catch it."""
    name: str
    path: str
    old: str
    new: str
    shipped_in: str


MUTATIONS = [
    Mutation(
        name="judge_run not threaded on the llm path",
        path="fieldtest/judges/dispatch.py",
        old="""            return judge_llm_binary(
                use_case_id, eval, output, fixture, run, config, judge_run
            )""",
        new="""            return judge_llm_binary(
                use_case_id, eval, output, fixture, run, config
            )""",
        shipped_in="v2, found by the integration tier",
    ),
    Mutation(
        name="rule loading removed from score()",
        path="fieldtest/runner.py",
        old='    from fieldtest.judges.registry import load_rules\n    load_rules(base_dir / "rules.py")\n',
        new="",
        shipped_in="v2, crashed fieldtest calibrate on any rule eval",
    ),
    Mutation(
        name="parameter-rejection detector stops matching",
        path="fieldtest/providers/base.py",
        old='_REJECTION_MARKERS = (\n    "deprecated",',
        new='_REJECTION_MARKERS = (\n    "zzz-never-matches",',
        shipped_in="the drop path, written from docs and never provider-triggered",
    ),
    Mutation(
        name="fixture inputs withheld from the judge",
        path="fieldtest/judges/llm.py",
        old='build_binary_judge_prompt(eval, output, fixture.get("inputs"))',
        new="build_binary_judge_prompt(eval, output)",
        shipped_in="every release before 0.3.0; grounding evals judged blind",
    ),
]


def run_suite() -> tuple[bool, list[str]]:
    """(passed, names of failing tests). -x so a mutation costs one test, not a suite."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", "--tb=no"],
        cwd=REPO, capture_output=True, text=True,
    )
    failures = [
        line.split(" ")[1] if line.startswith("FAILED ") else line
        for line in r.stdout.splitlines()
        if line.startswith("FAILED ")
    ]
    return r.returncode == 0, failures


def main() -> int:
    ok, failures = run_suite()
    if not ok:
        print("suite is already failing — fix that first:")
        for f in failures:
            print(f"  {f}")
        return 2
    print("baseline: suite green\n")

    undetected = []
    for m in MUTATIONS:
        target = REPO / m.path
        original = target.read_text()
        if m.old not in original:
            print(f"  SKIP  {m.name}\n        anchor not found in {m.path} — mutation is stale")
            undetected.append(m.name + " (stale anchor)")
            continue
        try:
            target.write_text(original.replace(m.old, m.new, 1))
            passed, failures = run_suite()
            caught = not passed
        finally:
            target.write_text(original)

        print(f"  {'CAUGHT' if caught else 'MISSED'}  {m.name}")
        print(f"          shipped: {m.shipped_in}")
        if caught:
            print(f"          by: {failures[0] if failures else 'unknown'}")
        if not caught:
            undetected.append(m.name)

    print()
    if undetected:
        print("undetected defects:")
        for name in undetected:
            print(f"  - {name}")
        return 1
    print(f"all {len(MUTATIONS)} defects caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
