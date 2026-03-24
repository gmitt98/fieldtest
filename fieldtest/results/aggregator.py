"""
fieldtest/results/aggregator.py

build_summary() and build_delta().
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from fieldtest.config import Config, ResultRow


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------

def build_summary(rows: list[ResultRow], config: Config) -> dict:
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

    # Group: use_case → tag → eval_id → rows
    groups: dict[str, dict[str, dict[str, list[ResultRow]]]] = {}
    for row in rows:
        uc = groups.setdefault(row.use_case, {})
        tag = uc.setdefault(row.tag, {})
        tag.setdefault(row.eval_id, []).append(row)

    summary: dict = {}
    for uc_id, tags in groups.items():
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
                    summary[uc_id][tag][eval_id] = {
                        "failure_rate": None,
                        "mean":         round(mean, 4) if mean is not None else None,
                        "min":          s_min,
                        "max":          s_max,
                        "stddev":       round(stddev, 4) if stddev is not None else None,
                        "floor_hits":   floor_hits,
                        "total_runs":   total_runs,
                        "error_count":  error_count,
                    }
                else:
                    failed_count  = sum(1 for r in valid_rows if r.passed is False)
                    failure_rate  = (
                        round(failed_count / total_runs, 6) if total_runs > 0 else None
                    )
                    summary[uc_id][tag][eval_id] = {
                        "failure_rate": failure_rate,
                        "floor_hits":   0,
                        "total_runs":   total_runs,
                        "error_count":  error_count,
                    }

    return summary


# ---------------------------------------------------------------------------
# build_delta
# ---------------------------------------------------------------------------

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
                if abs(delta) < 0.001:
                    unchanged.append(eval_id)
                elif delta > 0:
                    increased.append({
                        "eval_id":  eval_id,
                        "previous": round(prev_val, 6),
                        "current":  round(cur_val, 6),
                        "delta":    round(delta, 6),
                    })
                else:
                    decreased.append({
                        "eval_id":  eval_id,
                        "previous": round(prev_val, 6),
                        "current":  round(cur_val, 6),
                        "delta":    round(delta, 6),
                    })

    return {
        "baseline_run_id": baseline_run_id,
        "increased":       increased,
        "decreased":       decreased,
        "unchanged":       unchanged,
    }


def find_baseline(results_dir: Path, current_run_id: str, set_name: str) -> Optional[Path]:
    """
    Find the most recent results JSON in results_dir that:
      - is not the current run
      - was scored on the same set (smoke/full/regression/etc.)

    Filtering by set prevents misleading deltas when fixture populations differ
    between runs — e.g. comparing a full-set run against a smoke-set baseline
    would show movement that's purely an artifact of coverage, not model behavior.

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
            if data.get("set") == set_name:
                return p
        except Exception:
            continue
    return None
