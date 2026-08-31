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
    failures: int, total: int, confidence_level: float = 0.95
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

    z = NormalDist().inv_cdf(1 - (1 - confidence_level) / 2)
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


def _sample_variance(values: list[float]) -> float:
    """Unbiased (n-1) sample variance; 0.0 for fewer than two values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def decompose_variance(groups: list[list[float]]) -> tuple[float, float]:
    """
    Split repeated scores into (system spread, judge spread).

    `groups` is one list of repeated judge scores per output.

    The obvious pair of formulas is biased in both directions at once, and the
    bias is large enough to invert the verdict this decomposition exists to
    give. Writing s for true system SD and j for true judge SD over R
    repetitions:

      - The SD of the per-output means does not estimate s. Each mean carries
        its own judge noise, so it estimates sqrt(s^2 + j^2/R) — biased up.
      - The mean of the per-output SDs does not estimate j. Averaging SDs
        rather than variances understates it (Jensen), and a population (n)
        denominator on a sample of R understates it again by sqrt((R-1)/R).

    Both errors move spread off the judge and onto the system, so an ambiguous
    criterion reads as a noisy system and the user leaves the criterion alone.
    Monte Carlo over 2000 outputs at R=3: true s=5, j=5 was reported as
    system 5.72 / judge 3.59, and true s=5, j=8 as system 6.61 / judge 5.82 —
    both saying "system dominates" when the judge did.

    So estimate the variance components directly, as a one-way random-effects
    model does:

      judge_var  = pooled within-output sample variance  (unbiased for j^2)
      system_var = max(0, sample variance of the means - judge_var * mean(1/R_i))

    The clamp matters: when the judge explains everything, the unclamped
    estimate goes slightly negative, and 0.0 is the honest reading. The same
    Monte Carlo returns system 4.96 / judge 4.95 and system 4.70 / judge 8.07.
    """
    usable = [g for g in groups if g]
    if not usable:
        return 0.0, 0.0

    # Pooled within-output variance: sum of squared deviations over the summed
    # degrees of freedom. Outputs judged once contribute no information about
    # judge spread and are correctly weightless here.
    sq_dev = 0.0
    dof    = 0
    for g in usable:
        if len(g) < 2:
            continue
        mean_g = sum(g) / len(g)
        sq_dev += sum((v - mean_g) ** 2 for v in g)
        dof    += len(g) - 1
    judge_var = sq_dev / dof if dof > 0 else 0.0

    means = [sum(g) / len(g) for g in usable]
    # mean(1/R_i) rather than 1/R, so an output whose judge call errored out and
    # left it with fewer repetitions still subtracts the right amount.
    mean_inv_reps = sum(1.0 / len(g) for g in usable) / len(usable)
    system_var = max(0.0, _sample_variance(means) - judge_var * mean_inv_reps)

    return math.sqrt(system_var), math.sqrt(judge_var)


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

        verdict = collapse_verdicts(reps)
        # Reasoning has to come from a repetition that reached the collapsed
        # verdict. Taking the first rep's detail unconditionally put text
        # arguing for a pass under a row labelled fail, on exactly the ambiguous
        # evals repetitions exist to surface.
        speaker  = next((r for r in reps if r.passed is verdict), first)
        agreeing = sum(1 for r in reps if r.passed is verdict)
        detail   = speaker.detail
        if agreeing != len(reps):
            detail = f"[{agreeing}/{len(reps)} judges] {detail or ''}".rstrip()

        collapsed.append(first.model_copy(update={
            "passed":    verdict,
            "judge_run": 1,
            "detail":    detail,
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

    if is_scored:
        # No agreement figure here. It would be exact equality between an integer
        # label and a mean across repetitions, which is almost never equal once
        # the judge varies at all — a judge returning 3, 4, 4 against a human's 4
        # would score zero agreement while matching perfectly on central
        # tendency. Deviation reports the same comparison honestly.
        return {
            "labeled_runs":            compared,
            "mean_absolute_deviation": round(sum(deviations) / len(deviations), 4),
        }

    return {
        "labeled_runs":     compared,
        "judge_agreement":  round(agreed / compared, 6),
        "judge_false_pass": false_pass,
        "judge_false_fail": false_fail,
    }


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
    # Build eval type + scale_min lookup from config.
    #
    # Keyed by (use_case, eval_id), not eval_id. Eval ids only have to be unique
    # within a use case — the same `no-hallucination` in two use cases is
    # ordinary — and a flat key let the later definition overwrite the earlier
    # one. Where the two differed in type, a binary eval inherited is_scored
    # from a scored namesake and reported failure_rate: null, so an eval failing
    # every run showed nothing at all.
    eval_meta: dict[tuple, dict] = {}
    for uc in config.use_cases:
        for ev in uc.evals:
            eval_meta[(uc.id, ev.id)] = {
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

    # Reinsert each tag's evals in config declaration order. Rows arrive from
    # as_completed(), so insertion order is judge-completion order and two
    # identical runs produced tables in different orders — in the markdown, the
    # HTML and the JSON alike. Config order rather than alphabetical, to match
    # the fixture matrix and the order the user reads in config.yaml.
    declared = {
        uc.id: {ev.id: i for i, ev in enumerate(uc.evals)} for uc in config.use_cases
    }
    for uc_id, tags in groups.items():
        order = declared.get(uc_id, {})
        for tag, evals in tags.items():
            tags[tag] = {
                k: evals[k]
                for k in sorted(evals, key=lambda e: (order.get(e, len(order)), e))
            }

    summary: dict = {}
    for uc_id, tags in groups.items():
        uc_model      = uc_by_id.get(uc_id)
        configured_jr = resolve_judge_runs(config, uc_model) if uc_model else 1
        summary[uc_id] = {}
        for tag, evals in tags.items():
            summary[uc_id][tag] = {}
            for eval_id, eval_rows in evals.items():
                meta      = eval_meta.get((uc_id, eval_id),
                                         {"is_scored": False, "scale_min": None})
                is_scored = meta["is_scored"]

                # What this eval was actually judged, not what the config asked
                # for. judge_runs applies to llm evals; a rule or regex eval is
                # evaluated once however high the setting goes. Reporting the
                # configured value put every rule eval in the repeatability
                # table at "0.0% disagreement", implying a judge had been
                # consulted twice and agreed, when none was consulted at all.
                judge_runs = len({r.judge_run for r in eval_rows}) or 1
                if judge_runs > configured_jr:
                    judge_runs = configured_jr
                scale_min = meta["scale_min"]

                error_rows   = [r for r in eval_rows if r.error is not None]
                valid_rows   = [r for r in eval_rows if not r.skipped and r.error is None]

                error_count = len(error_rows)
                total_runs  = len(valid_rows)

                # Two different units, and conflating them misreports both. A
                # judge call is one repetition; an output is one generator run
                # that may have been judged several times. At judge_runs: 1 they
                # coincide, which is why the distinction went unnoticed.
                judge_calls       = len(valid_rows) + len(error_rows)
                outputs_attempted = len(
                    {(r.fixture_id, r.run) for r in valid_rows + error_rows}
                )

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
                        groups = []
                        for reps in by_output.values():
                            rep_scores = [r.score for r in reps if r.score is not None]
                            if not rep_scores:
                                continue
                            groups.append(rep_scores)
                        sys_sd, judge_sd = decompose_variance(groups)
                        judge_fields = {
                            **judge_fields,
                            "system_stddev": round(sys_sd, 4),
                            "judge_stddev":  round(judge_sd, 4),
                            "judge_runs":    judge_runs,
                        }
                        # Outputs, matching the binary branch. mean, stddev, min
                        # and max keep their definitions over every raw score;
                        # only the reported n moves, so the report's n column
                        # means one thing across both eval types.
                        total_runs = len(by_output)

                        # floor_hits has to move with it. Left counting raw
                        # scores it sat beside an n of outputs, so two tainted
                        # outputs judged three times each read as 6 floor hits
                        # out of 8 — a floor rate of 75% where the true one is
                        # 25%. Collapsed by majority, ties to floor, matching
                        # how a binary verdict collapses.
                        floor_hits = 0
                        for reps in by_output.values():
                            rep_scores = [r.score for r in reps if r.score is not None]
                            if not rep_scores:
                                continue
                            at_floor = sum(1 for x in rep_scores if x == scale_min)
                            if at_floor * 2 >= len(rep_scores):
                                floor_hits += 1
                        # The raw count is kept only where it differs from the
                        # collapsed one, so a single-judge-run summary keeps the
                        # shape it has always had.
                        judge_fields["floor_hit_calls"] = sum(
                            1 for x in scores if scale_min is not None and x == scale_min
                        )

                    summary[uc_id][tag][eval_id] = {
                        "failure_rate": None,
                        "mean":         round(mean, 4) if mean is not None else None,
                        "min":          s_min,
                        "max":          s_max,
                        "stddev":       round(stddev, 4) if stddev is not None else None,
                        "floor_hits":   floor_hits,
                        "total_runs":   total_runs,
                        "error_count":  error_count,
                        "judge_calls":  judge_calls,
                        "outputs_attempted": outputs_attempted,
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
                    confidence_level = config.defaults.confidence_level
                    interval   = (
                        wilson_interval(failed_count, total_runs, confidence_level)
                        if failure_rate is not None else None
                    )
                    summary[uc_id][tag][eval_id] = {
                        "failure_rate":    failure_rate,
                        "failure_rate_ci": list(interval) if interval else None,
                        "confidence_level": confidence_level,
                        "floor_hits":      0,
                        "total_runs":      total_runs,
                        "error_count":     error_count,
                        "judge_calls":     judge_calls,
                        "outputs_attempted": outputs_attempted,
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
                errors = stats.get("error_count") or 0
                scored = stats.get("total_runs") or 0
                # Summaries written before judge repetitions existed carry
                # neither field, and there scored + errors is the call count.
                calls     = stats.get("judge_calls") or (scored + errors)
                attempted = stats.get("outputs_attempted") or (scored + errors)
                failed   += errors
                total    += calls
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
    # Every key present on every path. A consumer that reads .delta.baseline_pre_judge
    # must not get one shape on a run with a baseline and another on a run without,
    # for the same reason judge.overrides serializes as {} rather than being omitted.
    empty = {
        "baseline_run_id":     None,
        "increased":           [],
        "decreased":           [],
        "unchanged":           [],
        "baseline_pre_judge":  False,
        "baseline_judge_runs": None,
        "baseline_error_share": 0.0,
        "baseline_fixture_count": None,
        "sample_changed": [],
    }

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
    # Collapsed failure_rate values are roughly comparable across repetition
    # counts — but not exactly, and not monotonically. collapse_verdicts
    # resolves ties to fail (spec 06 §2.7), so the collapsed rate depends on the
    # parity of judge_runs, not just its size. Independent judge, P(pass)=0.9,
    # identical outputs: judge_runs 1 → 0.100, 2 → 0.190, 3 → 0.028, 4 → 0.052,
    # 5 → 0.009. An even count biases toward fail. Judge spread fields are not
    # comparable at all. Keep the comparison, carry the caveat — but a delta
    # across a judge_runs change of different parity is not a system change.
    baseline_judge_runs = baseline_data.get("judge_runs", 1)

    # A baseline whose judge calls largely failed is a rate over whatever
    # survived. One real run lost 140 of 237 calls to an exhausted balance and
    # silently became the baseline for the next, which then reported a 26-point
    # "drop" against a third of the evidence. Keep the comparison and say so.
    baseline_errors = sum(
        st.get("error_count", 0)
        for tags in baseline_summary.values()
        for evals in tags.values()
        for st in evals.values()
    )
    baseline_scored = sum(
        st.get("total_runs", 0)
        for tags in baseline_summary.values()
        for evals in tags.values()
        for st in evals.values()
    )
    # A set can be redefined between runs. Comparing a rate over 14 fixtures
    # against one over 11 is not like-for-like even though both runs are
    # nominally the same set, and the deltas read as a change in the system.
    baseline_fixture_count = baseline_data.get("fixture_count")

    # fixture_count counts what is on disk and does not move when a set is
    # redefined. Per-eval n does: one project's `full` went from 14 fixtures to
    # 11 while fixture_count stayed 14, and every rate moved for that reason
    # alone. Collected per eval and reported once.
    sample_changed: list[str] = []

    baseline_error_share = (
        baseline_errors / (baseline_errors + baseline_scored)
        if (baseline_errors + baseline_scored) else 0.0
    )

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

                cur_n, prev_n = stats.get("total_runs"), prev_stats.get("total_runs")
                if cur_n and prev_n and cur_n != prev_n:
                    sample_changed.append(f"{eval_id} {prev_n}→{cur_n}")

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
        "baseline_error_share": round(baseline_error_share, 4),
        "baseline_fixture_count": baseline_fixture_count,
        "sample_changed": sorted(set(sample_changed)),
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
    path, _ = find_baseline_with_reason(
        results_dir, current_run_id, set_name, dataset_version, judge_fingerprint
    )
    return path


def find_baseline_with_reason(
    results_dir: Path,
    current_run_id: str,
    set_name: str,
    dataset_version: Optional[str] = None,
    judge_fingerprint: Optional[str] = None,
) -> tuple[Optional[Path], Optional[str]]:
    """
    find_baseline(), plus why the newest rejected candidate was rejected.

    Every `vs prior` going to `—` looks identical whether this is a first run or
    the judge model changed, and only one of those is the user's doing. The
    reason describes the most recent run that was otherwise usable, so it names
    the thing that actually differs.
    """
    if not results_dir.exists():
        return None, None

    reason: Optional[str] = None

    def note(text: str) -> None:
        nonlocal reason
        if reason is None:      # newest candidate wins; candidates are newest-first
            reason = text

    for p in sorted(results_dir.glob("*-data.json"), reverse=True):
        if p.stem.removesuffix("-data") == current_run_id:
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue

        if data.get("set") != set_name:
            note(f"the last run scored the '{data.get('set')}' set, not '{set_name}'")
            continue
        if dataset_version is not None and data.get("dataset_version") != dataset_version:
            note(
                f"the last run used dataset version "
                f"{data.get('dataset_version') or 'none'}, this one uses {dataset_version}"
            )
            continue
        if judge_fingerprint is not None:
            candidate_judge = data.get("judge") or {}
            if candidate_judge and candidate_judge.get("fingerprint") != judge_fingerprint:
                was = " ".join(
                    str(candidate_judge.get(k)) for k in ("provider", "model")
                    if candidate_judge.get(k)
                )
                note(
                    "the judge changed since the last run"
                    + (f" (was {was})" if was else "")
                    + " — rescoring the same outputs with a different judge is "
                    "not a measurement of the system"
                )
                continue
        return p, None

    return None, reason
