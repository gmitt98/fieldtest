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


# The five artifacts a run writes. `clean` enumerates the same five to delete a
# run, and enumerated them independently — two lists that happened to agree,
# with nothing to keep them agreeing. A sixth artifact added here would have
# left orphans behind every `fieldtest clean`.
DATA_JSON   = "-data.json"
DATA_CSV    = "-data.csv"
REPORT_MD   = "-report.md"
REPORT_CSV  = "-report.csv"
REPORT_HTML = "-report.html"

RESULT_SUFFIXES = (DATA_JSON, DATA_CSV, REPORT_MD, REPORT_CSV, REPORT_HTML)

# A calibration run measures the judge, not the system, so it writes neither a
# -data.json nor anything in RESULT_SUFFIXES — which is why `clean` never saw
# these and left them behind at every --keep value. They live here because this
# module is the one home for artifact suffixes, and calibrate.py imports them
# for the paths it writes.
CALIBRATION_JSON = "-calibration.json"
CALIBRATION_MD   = "-calibration.md"


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
    unsupported_params: Optional[list[str]] = None,
) -> None:
    """
    Write {run_id}-data.json, {run_id}-data.csv, {run_id}-report.md,
    {run_id}-report.csv, {run_id}-report.html to output_dir.
    Creates output_dir if it doesn't exist.
    All five built before any file is written — fail fast on build errors.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path        = output_dir / f"{run_id}{DATA_JSON}"
    data_csv_path    = output_dir / f"{run_id}{DATA_CSV}"
    md_path          = output_dir / f"{run_id}{REPORT_MD}"
    report_csv_path  = output_dir / f"{run_id}{REPORT_CSV}"
    html_path        = output_dir / f"{run_id}{REPORT_HTML}"

    # Build all content before writing — fail fast before any file is created
    json_content        = _build_json(
        rows, summary, delta, config, run_id, set_name, partial, partial_details,
    )

    # Raw rows are what -data.json and -data.csv carry; the human-facing views
    # read one row per judged output so their counts match the headline rates.
    from fieldtest.results.aggregator import collapse_rows
    view_rows = collapse_rows(rows, config)
    data_csv_content    = _build_data_csv(rows)
    md_content          = format_report(
        view_rows, summary, delta, config, run_id, set_name, partial, partial_details,
        unsupported_params,
    )
    report_csv_content  = format_report_csv(view_rows, config)

    # Parse json back to dict for HTML generator (avoids re-building)
    import json as _json
    run_data = _json.loads(json_content)
    run_data["rows"] = [r.model_dump() for r in view_rows]

    # Write all five
    json_path.write_text(json_content, encoding="utf-8")
    data_csv_path.write_text(data_csv_content, encoding="utf-8")
    md_path.write_text(md_content, encoding="utf-8")
    report_csv_path.write_text(report_csv_content, encoding="utf-8")
    write_html(run_data, config, html_path)


def _build_json(
    rows: list[ResultRow],
    summary: dict,
    delta: dict,
    config: Config,
    run_id: str,
    set_name: str,
    partial: bool = False,
    partial_details: Optional[list[str]] = None,
) -> str:
    """Serialize result data to JSON string."""
    fixture_ids = {r.fixture_id for r in rows if not r.skipped}
    runs = config.defaults.runs
    if config.use_cases:
        from fieldtest.config import resolve_runs
        runs = resolve_runs(config, config.use_cases[0])

    from fieldtest.config import config_identity, resolve_dataset_version, resolve_judge_runs
    dataset_version = resolve_dataset_version(config)

    judge_runs = 1
    if config.use_cases:
        judge_runs = resolve_judge_runs(config, config.use_cases[0])

    from fieldtest.results.provenance import build_judge_block

    data = {
        "schema_version":  2,
        "run_id":          run_id,
        "set":             set_name,
        "dataset_version": dataset_version,
        "config":          config_identity(config),
        "judge":           build_judge_block(config),
        "fixture_count":   len(fixture_ids),
        "runs":            runs,
        "judge_runs":      judge_runs,
        # A run with outputs missing reported runs and fixture_count as though
        # it were complete, so nothing reading this file — the README's own jq
        # gates included — could tell the rates were over a smaller population.
        "partial":         partial,
        "partial_details": partial_details or [],
        "rows":            [r.model_dump() for r in rows],
        "summary":         summary,
        "delta":           delta,
    }
    return json.dumps(data, indent=2, default=str)


def _build_data_csv(rows: list[ResultRow]) -> str:
    """Build data CSV string — flat rows, one per fixture × eval × run."""
    output = io.StringIO()
    fieldnames = [
        "use_case", "eval_id", "tag", "labels", "type", "fixture_id", "run",
        "judge_run", "passed", "score", "floor_hit", "skipped", "detail", "error"
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
            "judge_run":  row.judge_run,
            "passed":     "" if row.passed is None else str(row.passed).lower(),
            "score":      "" if row.score is None else row.score,
            "floor_hit":  str(row.floor_hit).lower(),
            "skipped":    str(row.skipped).lower(),
            "detail":     row.detail or "",
            "error":      row.error or "",
        })

    return output.getvalue()
