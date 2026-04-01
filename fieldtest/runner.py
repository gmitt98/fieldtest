"""
fieldtest/runner.py

Core logic for `fieldtest score` — decoupled from CLI so it's testable.
This is NOT the user's runner. This is the eval tool's scoring engine.
"""
from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from fieldtest.config import (
    Config,
    ResultRow,
    load_fixture,
    resolve_runs,
    resolve_set,
)
from fieldtest.errors import OutputError
from fieldtest.judges.dispatch import dispatch_judge
from fieldtest.results.aggregator import build_delta, build_summary, find_baseline
from fieldtest.results.writer import write_results


def make_run_id() -> str:
    """Generate a run ID: {timestamp}-{4-char-hex}. e.g. 2026-03-22T14-30-00-a3f9"""
    ts     = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    suffix = secrets.token_hex(2)  # exactly 4 lowercase hex chars
    return f"{ts}-{suffix}"


def score(
    config: Config,
    config_path: Path,
    set_name: str = "full",
    baseline_path: Optional[Path] = None,
    allow_partial: bool = False,
    concurrency: int = 5,
    verbose: bool = False,
) -> tuple[str, list[ResultRow]]:
    """
    Core scoring logic. Returns (run_id, rows).
    Writes results to results/ directory.

    Raises:
      OutputError  — missing outputs (unless allow_partial)
      ConfigError  — rule registration issues, unknown types
    """
    base_dir     = config_path.resolve().parent
    outputs_dir  = base_dir / "outputs"
    results_dir  = base_dir / "results"

    # -------------------------------------------------------------------
    # VALIDATE OUTPUTS
    # -------------------------------------------------------------------
    partial_missing: list[str] = []

    all_fixture_ids: list[tuple[str, str]] = []  # (use_case_id, fixture_id)
    for uc in config.use_cases:
        fixture_ids = resolve_set(set_name, uc, base_dir)
        runs        = resolve_runs(config, uc)
        for fid in fixture_ids:
            all_fixture_ids.append((uc.id, fid))
            for n in range(1, runs + 1):
                p = outputs_dir / fid / f"run-{n}.txt"
                if not p.exists():
                    if allow_partial:
                        partial_missing.append(f"{fid} run {n}")
                    else:
                        found = len(list((outputs_dir / fid).glob("run-*.txt"))) if (outputs_dir / fid).exists() else 0
                        raise OutputError(
                            f"Missing output: evals/outputs/{fid}/run-{n}.txt\n"
                            f"  Expected {runs} runs for '{fid}', found {found}.\n"
                            f"  Re-run the runner, or use --allow-partial to skip missing outputs."
                        )

    # -------------------------------------------------------------------
    # EVALUATE — build flat list of judge tasks
    # -------------------------------------------------------------------
    judge_tasks = []
    for uc in config.use_cases:
        fixture_ids = resolve_set(set_name, uc, base_dir)
        runs        = resolve_runs(config, uc)
        for fid in fixture_ids:
            fixture_path = base_dir / uc.fixtures.directory / f"{fid}.yaml"
            fixture      = load_fixture(fixture_path)
            run_outputs  = []
            for n in range(1, runs + 1):
                p = outputs_dir / fid / f"run-{n}.txt"
                if p.exists():
                    run_outputs.append((n, p.read_text()))
                elif allow_partial:
                    pass  # skip missing — already warned
            for ev in uc.evals:
                for run_number, run_output in run_outputs:
                    judge_tasks.append((uc.id, ev, run_output, fixture, run_number))

    # -------------------------------------------------------------------
    # EXECUTE with ThreadPoolExecutor
    # -------------------------------------------------------------------
    all_results: list[ResultRow] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {
            pool.submit(dispatch_judge, uc_id, ev, output, fixture, run, config): None
            for (uc_id, ev, output, fixture, run) in judge_tasks
        }
        for future in as_completed(future_map):
            result = future.result()
            all_results.append(result)
            if verbose:
                if result.error:
                    status = "⚠ error"
                elif result.skipped:
                    status = "— skip"
                elif result.score is not None:
                    status = f"● score {result.score}"
                elif result.passed:
                    status = "✓ pass"
                else:
                    status = "✗ fail"
                print(
                    f"  {result.eval_id:<30} {result.fixture_id}  run {result.run}  {status}",
                    flush=True,
                )

    # -------------------------------------------------------------------
    # AGGREGATE
    # -------------------------------------------------------------------
    summary = build_summary(all_results, config)

    # Auto-detect baseline — same set only, to avoid misleading cross-set deltas
    run_id = make_run_id()
    if baseline_path is None:
        baseline_path = find_baseline(results_dir, run_id, set_name)

    delta = build_delta(summary, baseline_path)

    # -------------------------------------------------------------------
    # REPORT
    # -------------------------------------------------------------------
    write_results(
        rows=all_results,
        summary=summary,
        delta=delta,
        config=config,
        run_id=run_id,
        output_dir=results_dir,
        set_name=set_name,
        partial=allow_partial and bool(partial_missing),
        partial_details=partial_missing if allow_partial else None,
    )

    return run_id, all_results
