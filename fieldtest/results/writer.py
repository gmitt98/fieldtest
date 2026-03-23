"""
fieldtest/results/writer.py

write_results() — writes {run_id}.json, {run_id}.md, {run_id}.csv to output_dir.
All three written atomically or none (failure raises and no partial files persist).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional

from fieldtest.config import Config, ResultRow
from fieldtest.results.report import format_report


def write_results(
    rows: list[ResultRow],
    summary: dict,
    delta: dict,
    config: Config,
    run_id: str,
    output_dir: Path,
    set_name: str = "full",
    partial: bool = False,
    partial_details: Optional[list[str]] = None,
) -> None:
    """
    Write {run_id}.json, {run_id}.md, {run_id}.csv to output_dir.
    Creates output_dir if it doesn't exist.
    All three written together — either all succeed or none written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{run_id}.json"
    md_path   = output_dir / f"{run_id}.md"
    csv_path  = output_dir / f"{run_id}.csv"

    # Build all content before writing — fail fast before any file is created
    json_content = _build_json(rows, summary, delta, config, run_id, set_name)
    md_content   = format_report(
        rows, summary, delta, config, run_id, set_name, partial, partial_details
    )
    csv_content  = _build_csv(rows)

    # Write all three
    json_path.write_text(json_content)
    md_path.write_text(md_content)
    csv_path.write_text(csv_content)


def _build_json(
    rows: list[ResultRow],
    summary: dict,
    delta: dict,
    config: Config,
    run_id: str,
    set_name: str,
) -> str:
    """Serialize result data to JSON string."""
    fixture_ids = {r.fixture_id for r in rows if not r.skipped}
    runs = config.defaults.runs
    if config.use_cases:
        from fieldtest.config import resolve_runs
        runs = resolve_runs(config, config.use_cases[0])

    data = {
        "run_id":        run_id,
        "set":           set_name,
        "fixture_count": len(fixture_ids),
        "runs":          runs,
        "rows":          [r.model_dump() for r in rows],
        "summary":       summary,
        "delta":         delta,
    }
    return json.dumps(data, indent=2, default=str)


def _build_csv(rows: list[ResultRow]) -> str:
    """Build CSV string — flat rows, one per fixture × eval × run."""
    output = io.StringIO()
    fieldnames = [
        "use_case", "eval_id", "tag", "type", "fixture_id", "run",
        "passed", "score", "floor_hit", "skipped", "detail", "error"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "use_case":   row.use_case,
            "eval_id":    row.eval_id,
            "tag":        row.tag,
            "type":       row.type,
            "fixture_id": row.fixture_id,
            "run":        row.run,
            "passed":     "" if row.passed is None else str(row.passed).lower(),
            "score":      "" if row.score is None else row.score,
            "floor_hit":  str(row.floor_hit).lower(),
            "skipped":    str(row.skipped).lower(),
            "detail":     row.detail or "",
            "error":      row.error or "",
        })

    return output.getvalue()
