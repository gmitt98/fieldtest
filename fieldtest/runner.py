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
    extract_labels,
    load_fixture,
    config_identity,
    resolve_dataset_version,
    resolve_judge_runs,
    resolve_runs,
    resolve_set,
    validate_run_counts,
)
from fieldtest.errors import ConfigError, OutputError
from fieldtest.fixtures import find_fixture_path
from fieldtest.judges.dispatch import dispatch_judge
from fieldtest.judges.llm import get_unsupported_params, reset_unsupported_params
from fieldtest.results.aggregator import (
    build_delta,
    build_summary,
    find_baseline_with_reason,
)
from fieldtest.results.provenance import build_judge_block
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
    write_artifacts: bool = True,
) -> tuple[str, list[ResultRow]]:
    """
    Core scoring logic. Returns (run_id, rows).
    Writes results to results/ directory.

    Raises:
      OutputError  — missing outputs (unless allow_partial)
      ConfigError  — rule registration issues, unknown types
    """
    if concurrency < 1:
        raise ConfigError(f"--concurrency must be at least 1, got {concurrency}.")

    # runs: 0 would write a green result set that measured nothing, and
    # judge_runs: 0 would silently drop every LLM eval from the run — both
    # passed validation and exited 0 before this check existed. The check
    # itself lives in resolve.validate_run_counts so `fieldtest validate`
    # applies the identical rule.
    validate_run_counts(config)

    base_dir     = config_path.resolve().parent
    outputs_dir  = base_dir / "outputs"
    results_dir  = base_dir / "results"

    # Rule evals need their registry populated. Doing this here rather than in
    # each CLI command means every caller of score() is correct by construction
    # — `fieldtest calibrate` reused this path and crashed on any project with a
    # rule eval, because rule loading lived only in the `score` command.
    from fieldtest.judges.registry import load_rules
    load_rules(base_dir / "rules.py")

    # Same reasoning for registered providers: a caller that constructs a Config
    # without going through parse_and_validate still needs the registry.
    from fieldtest.providers.registry import load_providers
    load_providers(base_dir / "providers.py")

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
    # Human verdicts, keyed (fixture_id, eval_id, run). Used to score the judge,
    # never to score the system.
    human_labels: dict = {}
    for uc in config.use_cases:
        fixture_ids = resolve_set(set_name, uc, base_dir)
        runs        = resolve_runs(config, uc)
        for fid in fixture_ids:
            fixture_file = find_fixture_path(base_dir / uc.fixtures.directory, fid)
            fixture      = load_fixture(fixture_file, base_dir)
            for (eval_id, run_number), value in extract_labels(fixture).items():
                # Keyed by the fixture's internal id, because every judge
                # stamps rows with fixture["id"] — keying by filename stem
                # silently discarded every label when the two differed.
                human_labels[(fixture["id"], eval_id, run_number)] = value
            run_outputs  = []
            for n in range(1, runs + 1):
                p = outputs_dir / fid / f"run-{n}.txt"
                if p.exists():
                    try:
                        run_outputs.append((n, p.read_text(encoding="utf-8")))
                    except UnicodeDecodeError as e:
                        raise OutputError(
                            f"Output file is not valid UTF-8 text: {p}\n"
                            f"  ({e})\n"
                            f"  fieldtest scores text outputs; re-run the runner "
                            f"writing UTF-8, or remove the file."
                        ) from e
                    except OSError as e:
                        raise OutputError(f"Cannot read output file {p}: {e}") from e
                elif allow_partial:
                    pass  # skip missing — already warned
            judge_runs = resolve_judge_runs(config, uc)
            for ev in uc.evals:
                for run_number, run_output in run_outputs:
                    # Repetitions only mean something for a sampling judge; a
                    # regex or rule returns the same answer every time, so
                    # repeating it would inflate the bill and the row count for
                    # no information.
                    reps = judge_runs if ev.is_judged else 1
                    for judge_run in range(1, reps + 1):
                        judge_tasks.append(
                            (uc.id, ev, run_output, fixture, run_number, judge_run)
                        )

    # -------------------------------------------------------------------
    # EXECUTE with ThreadPoolExecutor
    # -------------------------------------------------------------------
    if not all_fixture_ids:
        raise OutputError(
            f"No fixtures resolved for set '{set_name}'.\n"
            f"  Add fixture ids under fixtures.sets.{set_name} in config.yaml, or\n"
            f"  point --set at a set that has some.\n"
            f"  Scoring nothing would write a result set measuring nothing, which\n"
            f"  find_baseline() would then offer as a baseline for a real run."
        )

    run_id = make_run_id()
    all_results: list[ResultRow] = []
    reset_unsupported_params()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {
            pool.submit(
                dispatch_judge, uc_id, ev, output, fixture, run, config, judge_run
            ): None
            for (uc_id, ev, output, fixture, run, judge_run) in judge_tasks
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

    # Threads complete in whatever order the scheduler picks, so two identical
    # runs wrote their rows in different orders — spurious diffs in committed
    # -data.json / -data.csv artifacts. Aggregates never cared; bytes did.
    all_results.sort(
        key=lambda r: (r.use_case, r.eval_id, r.fixture_id, r.run, r.judge_run)
    )

    # -------------------------------------------------------------------
    # AGGREGATE
    # -------------------------------------------------------------------
    if not write_artifacts:
        # A calibration panel member is one judge's pass over the same outputs,
        # not a measurement of the system. It writes no result set — so it must
        # not reach find_baseline() — and nothing downstream reads the summary
        # or delta, so resolving a baseline here would be pure waste, repeated
        # once per panel judge.
        return run_id, all_results

    summary = build_summary(all_results, config, labels=human_labels)

    # Auto-detect baseline — same set + dataset_version only, to avoid misleading
    # cross-set or cross-snapshot deltas.
    no_baseline_reason = None
    if baseline_path is None:
        baseline_path, no_baseline_reason = find_baseline_with_reason(
            results_dir, run_id, set_name,
            dataset_version=resolve_dataset_version(config),
            judge_fingerprint=build_judge_block(config)["fingerprint"],
            config_id=config_identity(config),
        )

    delta = build_delta(summary, baseline_path)
    if baseline_path is None and no_baseline_reason:
        # Every `vs prior` reads `—` whether this is a first run or the judge
        # changed. Only one of those is something the user did.
        delta["no_baseline_reason"] = no_baseline_reason

    # -------------------------------------------------------------------
    # REPORT
    # -------------------------------------------------------------------
    try:
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
            unsupported_params=get_unsupported_params(),
        )
    except OSError as e:
        # The judging is done and possibly paid for; a permissions problem on
        # results/ is a user-fixable condition, not an internal bug.
        raise OutputError(
            f"Run {run_id} completed but its results could not be written to "
            f"{results_dir}: {e}\n"
            f"  Check the directory exists and is writable, then re-run."
        ) from e

    return run_id, all_results
