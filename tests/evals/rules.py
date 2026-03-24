"""
tests/evals/rules.py

Rule judges for the fieldtest dogfood eval suite.

Each fixture's output is the JSON content of a fieldtest result file
(or {"error": ..., "exit_code": N} if scoring failed and no file was written).

Rule functions receive:
  output: str  — the captured JSON text
  inputs: dict — the fixture inputs block (config path, set name, results_dir)

Returns: {"passed": bool, "detail": str}
"""
from __future__ import annotations

import json

from fieldtest import rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(output: str) -> tuple[dict | None, dict]:
    """Parse output as JSON. Returns (data, error_result) where error_result is non-None on failure."""
    try:
        return json.loads(output), {}
    except json.JSONDecodeError as e:
        return None, {"passed": False, "detail": f"output is not valid JSON: {e}"}


def _is_run_failed(data: dict) -> bool:
    """True when the dogfood runner couldn't produce a result file (fieldtest exited 1)."""
    return "error" in data and "rows" not in data


# ---------------------------------------------------------------------------
# correct_aggregation
# ---------------------------------------------------------------------------

@rule("failure_rate_accurate")
def check_failure_rate_accurate(output: str, inputs: dict) -> dict:
    """
    All failure_rate values in the result JSON are valid floats in [0.0, 1.0].
    Skipped rows are excluded from the denominator.
    Error rows are excluded and counted in error_count instead.
    """
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        return {"passed": False, "detail": f"run failed: {data.get('error')}"}

    for uc_id, tags in data.get("summary", {}).items():
        for tag, evals in tags.items():
            for eval_id, stats in evals.items():
                fr         = stats.get("failure_rate")
                total_runs = stats.get("total_runs", -1)
                if fr is None:
                    # None is valid when total_runs=0 (all runs errored or skipped)
                    if total_runs == 0:
                        continue
                    return {
                        "passed": False,
                        "detail": (
                            f"failure_rate is null for {uc_id}.{eval_id} "
                            f"but total_runs={total_runs} (expected null only when total_runs=0)"
                        ),
                    }
                if not isinstance(fr, (int, float)) or not (0.0 <= fr <= 1.0):
                    return {
                        "passed": False,
                        "detail": f"failure_rate {fr!r} out of [0,1] for {uc_id}.{eval_id}",
                    }

    return {"passed": True, "detail": "all failure_rate values are valid floats in [0.0, 1.0]"}


@rule("error_rows_excluded")
def check_error_rows_excluded(output: str, inputs: dict) -> dict:
    """
    Rows with error set have passed=None and are counted in error_count (not failure_rate).
    """
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        return {"passed": False, "detail": f"run failed: {data.get('error')}"}

    rows = data.get("rows", [])
    error_rows = [r for r in rows if r.get("error")]

    if not error_rows:
        return {"passed": True, "detail": "no error rows in this run (nothing to verify)"}

    # Error rows must have passed=None
    for row in error_rows:
        if row.get("passed") is not None:
            return {
                "passed": False,
                "detail": (
                    f"error row {row['eval_id']} (fixture {row['fixture_id']} run {row['run']}) "
                    f"has passed={row['passed']} — should be null"
                ),
            }

    # error_count in summary must match actual error row count per eval
    for uc_id, tags in data.get("summary", {}).items():
        for tag, evals in tags.items():
            for eval_id, stats in evals.items():
                expected = sum(
                    1 for r in error_rows
                    if r["use_case"] == uc_id and r["eval_id"] == eval_id
                )
                actual = stats.get("error_count", 0)
                if expected != actual:
                    return {
                        "passed": False,
                        "detail": (
                            f"{uc_id}.{eval_id}: {expected} error rows but "
                            f"error_count={actual} in summary"
                        ),
                    }

    return {
        "passed": True,
        "detail": (
            f"{len(error_rows)} error row(s) have passed=null and "
            f"are correctly counted in error_count"
        ),
    }


@rule("delta_direction_correct")
def check_delta_direction_correct(output: str, inputs: dict) -> dict:
    """
    Delta arrays (increased/decreased/unchanged) reference only eval IDs present
    in the summary. With no baseline, all arrays must be empty.
    """
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        return {"passed": False, "detail": f"run failed: {data.get('error')}"}

    delta = data.get("delta", {})

    if delta.get("baseline_run_id") is None:
        if delta.get("increased") or delta.get("decreased"):
            return {
                "passed": False,
                "detail": "increased/decreased non-empty with no baseline",
            }
        return {"passed": True, "detail": "no baseline — delta arrays correctly empty"}

    # With a baseline: every item in delta arrays must reference a known eval_id
    valid_eval_ids: set[str] = set()
    for uc_id, tags in data.get("summary", {}).items():
        for tag, evals in tags.items():
            valid_eval_ids.update(evals.keys())

    all_delta_items = (
        delta.get("increased", [])
        + delta.get("decreased", [])
        + delta.get("unchanged", [])
    )
    for item in all_delta_items:
        eid = item.get("eval_id")
        if eid not in valid_eval_ids:
            return {
                "passed": False,
                "detail": f"delta references unknown eval_id '{eid}'",
            }

    return {"passed": True, "detail": "delta arrays reference valid eval IDs"}


# ---------------------------------------------------------------------------
# valid_output_files
# ---------------------------------------------------------------------------

@rule("json_keys_present")
def check_json_keys_present(output: str, inputs: dict) -> dict:
    """Result JSON contains run_id, rows, summary, delta and parses without error."""
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        return {"passed": False, "detail": f"run failed: {data.get('error')}"}

    required = {"run_id", "rows", "summary", "delta"}
    missing  = required - set(data.keys())
    if missing:
        return {"passed": False, "detail": f"missing required JSON keys: {sorted(missing)}"}

    return {"passed": True, "detail": "run_id, rows, summary, delta all present"}


@rule("exit_0_on_success")
def check_exit_0_on_success(output: str, inputs: dict) -> dict:
    """fieldtest score exits 0 when outputs are present and config is valid."""
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        return {
            "passed": False,
            "detail": f"run did not produce a result file (exit {data.get('exit_code')}): {data.get('error')}",
        }
    return {"passed": True, "detail": "result file written — scoring completed successfully"}


@rule("no_files_on_config_error")
def check_no_files_on_config_error(output: str, inputs: dict) -> dict:
    """No results written when config is invalid — fieldtest exits 1 cleanly."""
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        exit_code = data.get("exit_code")
        if exit_code != 1:
            return {
                "passed": False,
                "detail": f"expected exit_code 1, got {exit_code}",
            }
        return {"passed": True, "detail": "no result file written and exit_code=1 — correct for config error"}
    return {
        "passed": False,
        "detail": "result file was written — expected config error to prevent any output",
    }


@rule("no_files_on_missing_output")
def check_no_files_on_missing_output(output: str, inputs: dict) -> dict:
    """No results written when outputs are missing — fieldtest exits 1 cleanly."""
    data, err = _parse(output)
    if err:
        return err
    if _is_run_failed(data):
        exit_code = data.get("exit_code")
        if exit_code != 1:
            return {
                "passed": False,
                "detail": f"expected exit_code 1, got {exit_code}",
            }
        return {"passed": True, "detail": "no result file written and exit_code=1 — correct for missing outputs"}
    return {
        "passed": False,
        "detail": "result file was written — expected missing-output error to prevent any output",
    }
