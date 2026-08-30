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
    Mutation(
        name="user-file load memo records the path before executing it",
        path="fieldtest/loader.py",
        old="""    resolved = str(path.resolve())
    with _load_lock:
        if resolved in loaded:
            return None
""",
        new="""    resolved = str(path.resolve())
    if True:
        if resolved in loaded:
            return None
        loaded.add(resolved)
""",
        shipped_in="0.3.0 until found by running calibrate on the bundled dataset",
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


def find_vacuous_tests() -> list[str]:
    """
    Tests whose every assertion sits inside an `if`, so they pass when the
    condition is False and assert nothing at all.

    test_provider_error_message_format was one: it checked an errored row
    inside `if results:`, and a config error writes no -data.json, so none of
    its assertions had ever run.
    """
    import ast

    vacuous = []
    for f in sorted((REPO / "tests").glob("test_*.py")):
        tree = ast.parse(f.read_text())
        for fn in ast.walk(tree):
            if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_")):
                continue
            unguarded = [n for n in fn.body if isinstance(n, ast.Assert)]
            guarded = [
                n
                for stmt in fn.body
                if isinstance(stmt, ast.If)
                for n in ast.walk(stmt)
                if isinstance(n, ast.Assert)
            ]
            if guarded and not unguarded:
                vacuous.append(f"{f.name}:{fn.lineno} {fn.name} (all assertions conditional)")

            # An assertion that cannot fail. `assert x or True` slipped past the
            # check above, because it is unguarded — it is just always true.
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assert):
                    continue
                t = node.test
                if isinstance(t, ast.Constant) and t.value:
                    vacuous.append(f"{f.name}:{node.lineno} {fn.name} (assert of a constant)")
                elif (
                    isinstance(t, ast.BoolOp)
                    and isinstance(t.op, ast.Or)
                    and any(isinstance(v, ast.Constant) and v.value for v in t.values)
                ):
                    vacuous.append(f"{f.name}:{node.lineno} {fn.name} (assert ... or True)")
    return vacuous


def find_real_lint_errors() -> list[str]:
    """
    Pyflakes-class findings only — undefined names, unused imports and
    variables. Not style: the repo has never had a linter and imposing one
    would churn every file. An unused local is worth catching because it is
    often the visible half of a test that stopped testing anything.

    Skipped silently when ruff is unavailable, so this script never fails for
    reasons unrelated to the suite.
    """
    import shutil

    runner = shutil.which("uvx") or shutil.which("ruff")
    if runner is None:
        return []
    cmd = [runner, "ruff"] if runner.endswith("uvx") else [runner]
    r = subprocess.run(
        cmd + ["check", "fieldtest/", "tests/", "scripts/",
               "--select", "F,E9", "--no-cache", "--output-format=concise"],
        cwd=REPO, capture_output=True, text=True,
    )
    return [ln for ln in r.stdout.splitlines() if ": F" in ln or ": E9" in ln]


def check_documented_test_count() -> list[str]:
    """
    The CHANGELOG states a test count. It went stale within a day of being
    written, because nothing recomputed it.

    Checked here rather than in a test: a test asserting the size of its own
    suite fails every time a test is added, which trains people to edit the
    number rather than read it.
    """
    import re

    changelog = (REPO / "CHANGELOG.md").read_text()
    m = re.search(r"Test suite: \d+ → (\d+)", changelog)
    if not m:
        return ["CHANGELOG no longer states a test count"]
    claimed = int(m.group(1))

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", ""],
        cwd=REPO, capture_output=True, text=True,
    )
    found = re.search(r"(\d+) tests collected", r.stdout)
    if not found:
        return []
    actual = int(found.group(1))
    if claimed != actual:
        return [f"CHANGELOG claims {claimed} tests; the suite has {actual}"]
    return []


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

    vacuous = find_vacuous_tests()
    if vacuous:
        print("tests whose assertions are all conditional (they pass on the else path):")
        for v in vacuous:
            print(f"  - {v}")
        undetected.append(f"{len(vacuous)} vacuous test(s)")
    else:
        print("no vacuous tests")

    stale = check_documented_test_count()
    if stale:
        for msg in stale:
            print(f"  - {msg}")
        undetected.extend(stale)
    else:
        print("documented test count is current")

    lint = find_real_lint_errors()
    if lint:
        print("\nunused or undefined names:")
        for ln in lint:
            print(f"  - {ln}")
        undetected.append(f"{len(lint)} lint error(s)")
    else:
        print("no unused or undefined names")

    print()
    if undetected:
        print("undetected defects:")
        for name in undetected:
            print(f"  - {name}")
        return 1
    print(f"all {len(MUTATIONS)} defects caught, suite is clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
