"""
fieldtest/results/aggregator.py

build_summary() and build_delta().
"""
from __future__ import annotations

import json
import math
from statistics import NormalDist
from pathlib import Path
from typing import Optional

from fieldtest.config import Config, ResultRow, resolve_judge_runs


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------

def wilson_interval(
    failures: int, total: int, confidence: float = 0.95
) -> Optional[tuple[float, float]]:
    """
    Two-sided Wilson score interval for a failure rate.

    Wilson rather than the normal approximation because small n is the common
    case here, not the edge case: at runs: 5 with zero failures the normal
    interval is degenerate and reports certainty the sample cannot support.

    Returns None when there is nothing to bound (total == 0).
    """
    if total <= 0:
        return None

    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = failures / total

    denominator = 1 + z**2 / total
    center      = (p + z**2 / (2 * total)) / denominator
    half_width  = (
        z / denominator * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    )

    low  = max(0.0, center - half_width)
    high = min(1.0, center + half_width)
    return (round(low, 4), round(high, 4))


def _group_by_output(rows: list[ResultRow]) -> dict:
    """Rows keyed by the output they judged: (fixture_id, run) → repetitions."""
    by_output: dict = {}
    for r in rows:
        by_output.setdefault((r.fixture_id, r.run), []).append(r)
    return by_output


def collapse_verdicts(reps: list[ResultRow]) -> bool:
    """
    One verdict per output from its repetitions: majority, ties resolved to fail.

    A tie means the judge could not decide. For a `safe` eval the conservative
    reading is the right default, and a rule that varies by tag would make the
    collapsed value depend on which column you read it from.
    """
    fails  = sum(1 for r in reps if r.passed is False)
    passes = sum(1 for r in reps if r.passed is True)
    return not (fails >= passes)


def _population_stddev(values: list[float]) -> float:
    """Population standard deviation; 0.0 for fewer than two values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def collapse_rows(rows: list[ResultRow], config: Config) -> list[ResultRow]:
    """
    One row per judged output, for the views that count rows rather than read the
    summary — the tag health table, the fixture x eval matrix, the report CSV.

    Without this, judge_runs: 3 makes those views report 5/6 while the headline
    rate, computed from collapsed verdicts, reports 1 of 2. Raw rows are what
    -data.csv and -data.json carry; this is only the reading view.

    Identity when every use case judges once, so existing output is unchanged.
    Scored rows pass through untouched: their cell shows an average, which the
    repetitions do not distort.
    """
    if all(resolve_judge_runs(config, uc) <= 1 for uc in config.use_cases):
        return rows

    binary_groups: dict = {}
    collapsed: list[ResultRow] = []

    for r in rows:
        is_binary_verdict = (
            r.passed is not None and r.score is None and not r.skipped and r.error is None
        )
        if not is_binary_verdict:
            collapsed.append(r)
            continue
        binary_groups.setdefault((r.use_case, r.eval_id, r.fixture_id, r.run), []).append(r)

    for reps in binary_groups.values():
        first = reps[0]
        if len(reps) == 1:
            collapsed.append(first)
            continue
        collapsed.append(first.model_copy(update={
            "passed":    collapse_verdicts(reps),
            "judge_run": 1,
            "detail":    first.detail,
        }))

    return collapsed


def _judge_agreement(
    eval_id: str,
    valid_rows: list[ResultRow],
    labels: dict,
    is_scored: bool,
) -> dict:
    """
    How well the judge agreed with the human, where a human said anything.

    Labels do not score the system — they score the judge. failure_rate is
    untouched by their presence. Partial coverage is the normal state: only
    labeled (fixture, run) pairs contribute.

    False passes are reported separately from false fails because on a `safe`
    eval they are the asymmetric error that matters, and a single agreement
    number hides them.
    """
    by_output = _group_by_output(valid_rows)

    compared    = 0
    agreed      = 0
    false_pass  = 0
    false_fail  = 0
    deviations: list[float] = []

    for (fixture_id, run), reps in by_output.items():
        label = labels.get((fixture_id, eval_id, run))
        if label is None:
            continue

        if is_scored:
            scores = [r.score for r in reps if r.score is not None]
            if not scores or not isinstance(label, int) or isinstance(label, bool):
                continue
            judged = sum(scores) / len(scores)
            compared += 1
            deviations.append(abs(judged - label))
            if judged == label:
                agreed += 1
        else:
            if label not in ("pass", "fail"):
                continue
            verdict = collapse_verdicts(reps)
            human_passed = label == "pass"
            compared += 1
            if verdict == human_passed:
                agreed += 1
            elif verdict and not human_passed:
                false_pass += 1
            else:
                false_fail += 1

    if not compared:
        return {}

    fields = {
        "labeled_runs":    compared,
        "judge_agreement": round(agreed / compared, 6),
    }
    if is_scored:
        fields["mean_absolute_deviation"] = round(sum(deviations) / len(deviations), 4)
    else:
        fields["judge_false_pass"] = false_pass
        fields["judge_false_fail"] = false_fail
    return fields


def build_summary(
    rows: list[ResultRow],
    config: Config,
    labels: Optional[dict] = None,
) -> dict:
    """
    Group rows by use_case → tag → eval_id and compute stats.

    Rules (spec §9):
    - error rows (error is not None): excluded from failure_rate; counted in error_count.
    - skipped rows (skipped=True): excluded from failure_rate and error_count.
    - total_runs = rows where not error and not skipped.
    - binary eval: failure_rate = failed_count / total_runs. mean/stddev/min/max = null.
    - scored eval: failure_rate = null. mean/stddev/min/max over score values.
    - floor_hits = count(score == scale.min) for scored evals; 0 for binary.
    - total_runs=0 (all errored or skipped): failure_rate=null, error_count=actual error count.
    """
    # Build eval type + scale_min lookup from config
    eval_meta: dict[str, dict] = {}  # eval_id → {is_scored, scale_min}
    for uc in config.use_cases:
        for ev in uc.evals:
            eval_meta[ev.id] = {
                "is_scored": ev.type == "llm" and not ev.binary,
                "scale_min": ev.scale[0] if ev.scale else None,
            }

    uc_by_id = {uc.id: uc for uc in config.use_cases}
    labels = labels or {}

    # Group: use_case → tag → eval_id → rows
    groups: dict[str, dict[str, dict[str, list[ResultRow]]]] = {}
    for row in rows:
        uc = groups.setdefault(row.use_case, {})
        tag = uc.setdefault(row.tag, {})
        tag.setdefault(row.eval_id, []).append(row)

    summary: dict = {}
    for uc_id, tags in groups.items():
        uc_model   = uc_by_id.get(uc_id)
        judge_runs = resolve_judge_runs(config, uc_model) if uc_model else 1
        summary[uc_id] = {}
        for tag, evals in tags.items():
            summary[uc_id][tag] = {}
            for eval_id, eval_rows in evals.items():
                meta      = eval_meta.get(eval_id, {"is_scored": False, "scale_min": None})
                is_scored = meta["is_scored"]
                scale_min = meta["scale_min"]

                error_rows   = [r for r in eval_rows if r.error is not None]
                skipped_rows = [r for r in eval_rows if r.skipped and r.error is None]
                valid_rows   = [r for r in eval_rows if not r.skipped and r.error is None]

                error_count = len(error_rows)
                total_runs  = len(valid_rows)

                if is_scored:
                    scores = [r.score for r in valid_rows if r.score is not None]
                    floor_hits = sum(1 for s in scores if scale_min is not None and s == scale_min)
                    mean   = sum(scores) / len(scores) if scores else None
                    stddev = (
                        math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
                        if scores and len(scores) > 1 else 0.0
                    ) if mean is not None else None
                    s_min  = min(scores) if scores else None
                    s_max  = max(scores) if scores else None
                    judge_fields = _judge_agreement(eval_id, valid_rows, labels, True)
                    if judge_runs > 1 and scores:
                        # Two sources of variance were summed and reported as one
                        # number attributed to the system. Separate them.
                        by_output    = _group_by_output(valid_rows)
                        output_means = []
                        within       = []
                        for reps in by_output.values():
                            rep_scores = [r.score for r in reps if r.score is not None]
                            if not rep_scores:
                                continue
                            output_means.append(sum(rep_scores) / len(rep_scores))
                            within.append(_population_stddev(rep_scores))
                        judge_fields = {
                            **judge_fields,
                            "system_stddev": round(_population_stddev(output_means), 4),
                            "judge_stddev":  round(sum(within) / len(within), 4) if within else 0.0,
                            "judge_runs":    judge_runs,
                        }

                    summary[uc_id][tag][eval_id] = {
                        "failure_rate": None,
                        "mean":         round(mean, 4) if mean is not None else None,
                        "min":          s_min,
                        "max":          s_max,
                        "stddev":       round(stddev, 4) if stddev is not None else None,
                        "floor_hits":   floor_hits,
                        "total_runs":   total_runs,
                        "error_count":  error_count,
                        **judge_fields,
                    }
                else:
                    judge_fields: dict = _judge_agreement(
                        eval_id, valid_rows, labels, False
                    )
                    if judge_runs > 1:
                        # Rates come from collapsed verdicts, not raw repetition
                        # rows: otherwise judge_runs: 3 triples the denominator
                        # and rates stop being comparable across configurations.
                        by_output = _group_by_output(valid_rows)
                        collapsed = {
                            key: collapse_verdicts(reps) for key, reps in by_output.items()
                        }
                        disagreeing = sum(
                            1 for reps in by_output.values()
                            if len({r.passed for r in reps}) > 1
                        )
                        total_runs   = len(collapsed)
                        failed_count = sum(1 for v in collapsed.values() if v is False)
                        judge_fields = {
                            **judge_fields,
                            "judge_disagreement_rate": (
                                round(disagreeing / len(by_output), 6) if by_output else None
                            ),
                            "judge_runs": judge_runs,
                        }
                    else:
                        failed_count = sum(1 for r in valid_rows if r.passed is False)

                    failure_rate  = (
                        round(failed_count / total_runs, 6) if total_runs > 0 else None
                    )
                    confidence = config.defaults.confidence
                    interval   = (
                        wilson_interval(failed_count, total_runs, confidence)
                        if failure_rate is not None else None
                    )
                    summary[uc_id][tag][eval_id] = {
                        "failure_rate":    failure_rate,
                        "failure_rate_ci": list(interval) if interval else None,
                        "confidence":      confidence,
                        "floor_hits":      0,
                        "total_runs":      total_runs,
                        "error_count":     error_count,
                        **judge_fields,
                    }

    return summary


# ---------------------------------------------------------------------------
# build_delta
# ---------------------------------------------------------------------------

def summarize_judge_errors(summary: dict) -> Optional[dict]:
    """
    Judge errors across the run, or None if there were none.

    Errored rows are excluded from failure_rate and counted in error_count,
    which is correct in isolation but means an overloaded provider silently
    shrinks the sample rather than failing loudly. This is what the report
    needs to say so out loud.

    Returns {"failed", "total", "affected": [(eval_id, scored, attempted)]}.
    """
    failed = 0
    total  = 0
    affected: list[tuple[str, int, int]] = []

    for uc_stats in summary.values():
        for tag_stats in uc_stats.values():
            for eval_id, stats in tag_stats.items():
                errors    = stats.get("error_count") or 0
                scored    = stats.get("total_runs") or 0
                attempted = scored + errors
                failed   += errors
                total    += attempted
                if errors:
                    affected.append((eval_id, scored, attempted))

    if not failed:
        return None

    return {"failed": failed, "total": total, "affected": affected}


def _intervals_overlap(current_ci, baseline_ci) -> bool:
    """
    Whether two confidence intervals overlap. Movement between overlapping
    intervals is movement the sample size cannot distinguish from noise.

    False when either side has no interval — a scored eval, an empty sample, or
    a baseline written before intervals existed.
    """
    if not current_ci or not baseline_ci:
        return False
    return current_ci[0] <= baseline_ci[1] and baseline_ci[0] <= current_ci[1]


def build_delta(current: dict, baseline_path: Optional[Path]) -> dict:
    """
    Compare current summary to baseline.

    Returns:
      {
        "baseline_run_id": str | null,
        "increased": [...],
        "decreased": [...],
        "unchanged": [...]
      }

    Rules (spec §9):
    - no baseline → {baseline_run_id: null, increased:[], decreased:[], unchanged:[]}.
    - binary evals: compare failure_rate. Scored: compare mean.
    - |current - previous| < 0.001 → "unchanged".
    - up → "increased", down → "decreased". No "better"/"worse".
    """
    empty = {"baseline_run_id": None, "increased": [], "decreased": [], "unchanged": []}

    if baseline_path is None or not baseline_path.exists():
        return empty

    try:
        baseline_data = json.loads(baseline_path.read_text())
    except Exception:
        return empty

    baseline_summary = baseline_data.get("summary", {})
    baseline_run_id  = baseline_data.get("run_id")
    # Accepted as a baseline, but the comparison carries a caveat: we cannot
    # tell whether the instrument was the same one.
    baseline_pre_judge = baseline_data.get("judge") is None
    # Collapsed failure_rate values stay comparable across repetition counts;
    # the judge spread fields do not. Keep the comparison, carry the caveat.
    baseline_judge_runs = baseline_data.get("judge_runs", 1)

    increased: list[dict] = []
    decreased: list[dict] = []
    unchanged: list[str]  = []

    for uc_id, tags in current.items():
        prev_tags = baseline_summary.get(uc_id, {})
        for tag, evals in tags.items():
            prev_evals = prev_tags.get(tag, {})
            for eval_id, stats in evals.items():
                prev_stats = prev_evals.get(eval_id)
                if prev_stats is None:
                    continue  # new eval — not in baseline

                # Determine which metric to compare
                is_scored = stats.get("mean") is not None
                if is_scored:
                    cur_val  = stats.get("mean")
                    prev_val = prev_stats.get("mean")
                else:
                    cur_val  = stats.get("failure_rate")
                    prev_val = prev_stats.get("failure_rate")

                if cur_val is None or prev_val is None:
                    continue

                delta = cur_val - prev_val

                # An additional flag, not a fourth bucket: existing CI jq
                # expressions read increased/decreased/unchanged and keep working.
                entry = {
                    "eval_id":  eval_id,
                    "previous": round(prev_val, 6),
                    "current":  round(cur_val, 6),
                    "delta":    round(delta, 6),
                    "overlapping": _intervals_overlap(
                        stats.get("failure_rate_ci"), prev_stats.get("failure_rate_ci")
                    ),
                }

                if abs(delta) < 0.001:
                    unchanged.append(eval_id)
                elif delta > 0:
                    increased.append(entry)
                else:
                    decreased.append(entry)

    return {
        "baseline_run_id":   baseline_run_id,
        "increased":         increased,
        "decreased":         decreased,
        "unchanged":         unchanged,
        "baseline_pre_judge": baseline_pre_judge,
        "baseline_judge_runs": baseline_judge_runs,
    }


def find_baseline(
    results_dir: Path,
    current_run_id: str,
    set_name: str,
    dataset_version: Optional[str] = None,
    judge_fingerprint: Optional[str] = None,
) -> Optional[Path]:
    """
    Find the most recent results JSON in results_dir that:
      - is not the current run
      - was scored on the same set (smoke/full/regression/etc.)
      - matches dataset_version if one is provided

    Filtering by set prevents misleading deltas when fixture populations differ
    between runs — e.g. comparing a full-set run against a smoke-set baseline
    would show movement that's purely an artifact of coverage, not model behavior.

    Filtering by dataset_version (when set) prevents the same artifact when
    fixtures themselves change between dataset snapshots. Unversioned current
    runs match any baseline (backwards compatible). Versioned current runs
    only match baselines tagged with the same version.

    Filtering by judge_fingerprint prevents the same artifact again when the
    instrument changes: rescoring an unchanged outputs/ directory with a
    different judge model otherwise reads as a system regression. Baselines
    written before judge tracking carry no fingerprint and are accepted as
    unknown rather than rejected — blanking out every historical delta on
    upgrade is a worse failure than a caveated comparison.

    Returns None if no matching baseline found.
    """
    if not results_dir.exists():
        return None
    candidates = sorted(results_dir.glob("*-data.json"), reverse=True)
    for p in candidates:
        if p.stem.removesuffix("-data") == current_run_id:
            continue
        try:
            data = json.loads(p.read_text())
            if data.get("set") != set_name:
                continue
            if dataset_version is not None and data.get("dataset_version") != dataset_version:
                continue
            if judge_fingerprint is not None:
                candidate_judge = data.get("judge")
                if candidate_judge and candidate_judge.get("fingerprint") != judge_fingerprint:
                    continue
            return p
        except Exception:
            continue
    return None
