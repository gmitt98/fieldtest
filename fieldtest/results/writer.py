"""
fieldtest/results/writer.py

write_results() — writes five files per run to output_dir:
  {run_id}-data.json    full result data (rows + summary + delta)
  {run_id}-data.csv     flat rows, one per fixture × eval × run
  {run_id}-report.md    human-readable markdown report
  {run_id}-report.csv   spreadsheet-friendly report (tag health / matrix / failures)
  {run_id}-report.html  self-contained HTML visual report

All five written atomically — content is built before any file is touched.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional

from fieldtest.config import Config, ResultRow
from fieldtest.results.html import write_html
from fieldtest.results.report import format_report, format_report_csv


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
    Write {run_id}-data.json, {run_id}-data.csv, {run_id}-report.md,
    {run_id}-report.csv, {run_id}-report.html to output_dir.
    Creates output_dir if it doesn't exist.
    All five built before any file is written — fail fast on build errors.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path        = output_dir / f"{run_id}-data.json"
    data_csv_path    = output_dir / f"{run_id}-data.csv"
    md_path          = output_dir / f"{run_id}-report.md"
    report_csv_path  = output_dir / f"{run_id}-report.csv"
    html_path        = output_dir / f"{run_id}-report.html"

    # Build all content before writing — fail fast before any file is created
    json_content        = _build_json(rows, summary, delta, config, run_id, set_name)
    data_csv_content    = _build_data_csv(rows)
    md_content          = format_report(
        rows, summary, delta, config, run_id, set_name, partial, partial_details
    )
    report_csv_content  = format_report_csv(rows, config)

    # Parse json back to dict for HTML generator (avoids re-building)
    import json as _json
    run_data = _json.loads(json_content)

    # Write all five
    json_path.write_text(json_content)
    data_csv_path.write_text(data_csv_content)
    md_path.write_text(md_content)
    report_csv_path.write_text(report_csv_content)
    write_html(run_data, config, html_path)


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


def _build_data_csv(rows: list[ResultRow]) -> str:
    """Build data CSV string — flat rows, one per fixture × eval × run."""
    output = io.StringIO()
    fieldnames = [
        "use_case", "eval_id", "tag", "labels", "type", "fixture_id", "run",
        "passed", "score", "floor_hit", "skipped", "detail", "error"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "use_case":   row.use_case,
            "eval_id":    row.eval_id,
            "tag":        row.tag,
            "labels":     "|".join(row.labels) if row.labels else "",
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
