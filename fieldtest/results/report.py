"""
fieldtest/results/report.py

write_markdown() / format_report() — generates the human-readable eval report.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fieldtest.config import Config, ResultRow


# ---------------------------------------------------------------------------
# Section helpers — each returns a list[str] of markdown lines
# ---------------------------------------------------------------------------


def _format_tag_summary(rows: list[ResultRow], use_case_id: str) -> list[str]:
    """
    Tag health summary — one pass rate per tag (RIGHT / GOOD / SAFE).
    Only counts non-skipped, non-error rows.
    """
    tag_totals: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
    for r in rows:
        if r.use_case != use_case_id or r.skipped or r.error:
            continue
        tag = (r.tag or "untagged").upper()
        tag_totals[tag]["total"] += 1
        if r.passed:
            tag_totals[tag]["passed"] += 1

    if not any(v["total"] for v in tag_totals.values()):
        return []

    lines = [
        "### Tag Health",
        "| tag | pass rate | passed / total |",
        "|-----|-----------|----------------|",
    ]
    for tag in ["RIGHT", "GOOD", "SAFE"]:
        if tag not in tag_totals:
            continue
        d = tag_totals[tag]
        pct = f"{round(d['passed'] / d['total'] * 100)}%" if d["total"] else "—"
        lines.append(f"| {tag} | {pct} | {d['passed']} / {d['total']} |")
    return lines


def _format_fixture_matrix(
    rows: list[ResultRow], use_case_id: str, eval_ids: list[str]
) -> list[str]:
    """
    Fixture × eval matrix.
    Rows = fixture IDs (sorted), columns = eval IDs (config order).
    Cell values:
      "X/N"       — X passes out of N judged runs
      "err"       — all runs returned judge errors
      "X/N+err"   — some passes, some errors
      "—"         — no data (all skipped or eval not run on fixture)
    """
    uc_rows = [r for r in rows if r.use_case == use_case_id]
    fixture_ids = sorted({r.fixture_id for r in uc_rows if not r.skipped})
    active_evals = [e for e in eval_ids if any(r.eval_id == e for r in uc_rows)]

    if not fixture_ids or not active_evals:
        return []

    # Accumulate per (fixture_id, eval_id)
    cell: dict = defaultdict(lambda: {"passed": 0, "total": 0, "errors": 0})
    for r in uc_rows:
        if r.skipped:
            continue
        key = (r.fixture_id, r.eval_id)
        if r.error:
            cell[key]["errors"] += 1
        else:
            cell[key]["total"] += 1
            if r.passed:
                cell[key]["passed"] += 1

    header = "| fixture | " + " | ".join(active_evals) + " |"
    sep = "| --- |" + " --- |" * len(active_evals)
    lines = ["### Fixture × Eval Matrix", header, sep]

    for fid in fixture_ids:
        cells = []
        for eid in active_evals:
            d = cell[(fid, eid)]
            if d["errors"] > 0 and d["total"] == 0:
                cells.append("err")
            elif d["errors"] > 0:
                cells.append(f"{d['passed']}/{d['total']}+err")
            elif d["total"] == 0:
                cells.append("—")
            else:
                cells.append(f"{d['passed']}/{d['total']}")
        lines.append("| " + fid + " | " + " | ".join(cells) + " |")

    return lines


def _format_failure_details(rows: list[ResultRow], use_case_id: str) -> list[str]:
    """
    Failure detail list — one entry per failing run, grouped by eval.
    Shows: fixture_id, run number, and judge reasoning (detail field).
    Errors and skipped rows are excluded — this is only judged failures.
    """
    failing = [
        r
        for r in rows
        if r.use_case == use_case_id
        and not r.skipped
        and not r.error
        and r.passed is False
    ]
    if not failing:
        return []

    by_eval: dict[str, list[ResultRow]] = defaultdict(list)
    for r in failing:
        by_eval[r.eval_id].append(r)

    lines = ["### Failure Details"]
    for eval_id in sorted(by_eval):
        lines.append(f"\n**{eval_id}**")
        for r in sorted(by_eval[eval_id], key=lambda x: (x.fixture_id, x.run)):
            detail = (r.detail or "").strip().replace("\n", " ") or "no detail"
            lines.append(f"- `{r.fixture_id}` run {r.run}: {detail}")

    return lines


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------


def format_report(
    rows: list[ResultRow],
    summary: dict,
    delta: dict,
    config: Config,
    run_id: str,
    set_name: str,
    partial: bool = False,
    partial_details: Optional[list[str]] = None,
) -> str:
    """
    Build the full markdown report as a string.

    Sections per use case:
      1. Tag Health summary (RIGHT / GOOD / SAFE pass rates at a glance)
      2. Per-eval RIGHT / GOOD / SAFE tables with failure rates and delta
      3. Floor hits and judge error notices
      4. Fixture × Eval matrix (rows=fixtures, cols=evals, cells=pass rate)
      5. Failure Details (per failing run with judge reasoning)
    """
    fixture_ids = sorted({r.fixture_id for r in rows if not r.skipped})
    fixture_count = len(fixture_ids)

    # Determine runs from config (use first use_case as representative)
    from fieldtest.config import resolve_runs
    runs = config.defaults.runs
    if config.use_cases:
        runs = resolve_runs(config, config.use_cases[0])

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []

    # Header
    lines.append("# Eval Report")
    if partial:
        missing = len(partial_details) if partial_details else 0
        lines.append(
            f"{ts} | set: {set_name} | {fixture_count} fixtures × {runs} runs "
            f"(PARTIAL — {missing} outputs missing, skipped)"
        )
        if partial_details:
            lines.append(
                "⚠ partial results: "
                + ", ".join(partial_details)
                + " not found — excluded from rates"
            )
    else:
        lines.append(
            f"{ts} | set: {set_name} | {fixture_count} fixtures × {runs} runs = "
            f"{fixture_count * runs} evaluations per eval"
        )

    # Per use_case sections
    for uc in config.use_cases:
        uc_stats = summary.get(uc.id, {})
        if not uc_stats:
            continue

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## {uc.id}")
        lines.append(uc.description)
        lines.append("")

        # --- Tag Health Summary -------------------------------------------
        tag_summary_lines = _format_tag_summary(rows, uc.id)
        if tag_summary_lines:
            lines.extend(tag_summary_lines)
            lines.append("")

        # --- Per-eval RIGHT / GOOD / SAFE tables --------------------------
        delta_idx: dict[str, dict] = {}
        for item in delta.get("increased", []):
            delta_idx[item["eval_id"]] = item
        for item in delta.get("decreased", []):
            delta_idx[item["eval_id"]] = item

        floor_hit_rows: list[str] = []
        error_eval_ids: list[tuple[str, int]] = []

        for tag in ["right", "good", "safe"]:
            tag_stats = uc_stats.get(tag, {})
            if not tag_stats:
                continue

            lines.append(f"### {tag.upper()}")
            lines.append("| eval | pass rate | mean | floor hits | errors | vs prior |")
            lines.append("|------|----------|------|-----------|--------|---------|")

            for eval_id, stats in tag_stats.items():
                fr    = stats.get("failure_rate")
                mean  = stats.get("mean")
                fh    = stats.get("floor_hits", 0)
                errs  = stats.get("error_count", 0)

                # Display pass rate (1 - failure_rate) so higher = better
                pr_str   = f"{round((1 - fr) * 100)}%" if fr is not None else "—"
                mean_str = "—"
                if mean is not None:
                    for ev in uc.evals:
                        if ev.id == eval_id and ev.scale:
                            mean_str = f"{mean}/{ev.scale[1]}"
                            break

                # vs prior — delta is stored as (cur_failure_rate - prev_failure_rate),
                # so negate it to express as pass rate delta: positive = improvement.
                if eval_id in delta_idx:
                    d = delta_idx[eval_id]["delta"]
                    if mean is not None:
                        vs_str = f"+{round(d, 2)}" if d > 0 else f"{round(d, 2)}"
                    else:
                        pd = -d  # pass rate delta = negated failure rate delta
                        vs_str = f"+{round(pd * 100, 2)}%" if pd > 0 else f"{round(pd * 100, 2)}%"
                elif eval_id in delta.get("unchanged", []):
                    vs_str = "↔"
                else:
                    vs_str = "—"

                lines.append(
                    f"| {eval_id} | {pr_str} | {mean_str} | {fh} | {errs} | {vs_str} |"
                )

                if errs > 0:
                    error_eval_ids.append((eval_id, errs))
                if fh > 0:
                    for row in rows:
                        if row.eval_id == eval_id and row.floor_hit:
                            floor_hit_rows.append(
                                f"outputs/{row.fixture_id}/run-{row.run}.txt"
                            )

            lines.append("")

        # Floor hits
        if floor_hit_rows:
            eval_id_fh = next((r.eval_id for r in rows if r.floor_hit), "scored_eval")
            lines.append(f"⚠ floor hits — {', '.join(floor_hit_rows)}")
            scale_str = ""
            for ev in uc.evals:
                if ev.id == eval_id_fh and ev.scale:
                    scale_str = f"{ev.scale[0]}/{ev.scale[1]}"
                    break
            lines.append(f"  eval: {eval_id_fh} scored {scale_str} — review these outputs")
            lines.append("")

        # Judge errors
        if error_eval_ids:
            for eval_id, count in error_eval_ids:
                lines.append(
                    f"⚠ judge errors — {count} calls failed for {eval_id}; "
                    f"excluded from pass rate"
                )
            lines.append(
                "  re-run with --concurrency 1 to isolate; "
                "check ANTHROPIC_API_KEY if errors persist"
            )
            lines.append("")

        # --- Fixture × Eval Matrix ----------------------------------------
        eval_ids = [ev.id for ev in uc.evals]
        matrix_lines = _format_fixture_matrix(rows, uc.id, eval_ids)
        if matrix_lines:
            lines.extend(matrix_lines)
            lines.append("")

        # --- Failure Details ----------------------------------------------
        detail_lines = _format_failure_details(rows, uc.id)
        if detail_lines:
            lines.extend(detail_lines)
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV report builder
# ---------------------------------------------------------------------------


def format_report_csv(rows: list[ResultRow], config: Config) -> str:
    """
    Build the report CSV — three labeled sections separated by blank rows,
    designed to be opened in a spreadsheet.

    Section 1 — Tag Health
      use_case, tag, pass_rate_pct, passed, total

    Section 2 — Fixture × Eval Matrix
      use_case, fixture_id, eval_id, tag, passes, total, errors, cell

    Section 3 — Failures
      use_case, eval_id, tag, fixture_id, run, detail

    Cells in the matrix:
      "X/N"       — X passes out of N judged runs
      "err"       — all judge calls errored
      "X/N+err"   — some passes, some errors
      ""          — no data (all skipped or eval not run on this fixture)
    """
    output = io.StringIO()
    w = csv.writer(output, lineterminator="\n")

    for uc in config.use_cases:
        uc_rows = [r for r in rows if r.use_case == uc.id]
        if not uc_rows:
            continue

        # ----------------------------------------------------------------
        # Section 1: Tag Health
        # ----------------------------------------------------------------
        w.writerow(["## Tag Health", uc.id])
        w.writerow(["use_case", "tag", "pass_rate_pct", "passed", "total"])
        tag_totals: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
        for r in uc_rows:
            if r.skipped or r.error:
                continue
            tag = (r.tag or "untagged").upper()
            tag_totals[tag]["total"] += 1
            if r.passed:
                tag_totals[tag]["passed"] += 1
        for tag in ["RIGHT", "GOOD", "SAFE"]:
            if tag in tag_totals:
                d = tag_totals[tag]
                pct = round(d["passed"] / d["total"] * 100) if d["total"] else ""
                w.writerow([uc.id, tag, pct, d["passed"], d["total"]])
        w.writerow([])

        # ----------------------------------------------------------------
        # Section 2: Fixture × Eval Matrix
        # ----------------------------------------------------------------
        w.writerow(["## Fixture x Eval Matrix", uc.id])
        w.writerow(["use_case", "fixture_id", "eval_id", "tag", "passes", "total", "errors", "cell"])

        eval_ids = [ev.id for ev in uc.evals]
        fixture_ids = sorted({r.fixture_id for r in uc_rows if not r.skipped})
        tag_map: dict[str, str] = {ev.id: (ev.tag or "") for ev in uc.evals}

        cell: dict = defaultdict(lambda: {"passed": 0, "total": 0, "errors": 0})
        for r in uc_rows:
            if r.skipped:
                continue
            key = (r.fixture_id, r.eval_id)
            if r.error:
                cell[key]["errors"] += 1
            else:
                cell[key]["total"] += 1
                if r.passed:
                    cell[key]["passed"] += 1

        for fid in fixture_ids:
            for eid in eval_ids:
                d = cell[(fid, eid)]
                if d["errors"] > 0 and d["total"] == 0:
                    cell_str = "err"
                elif d["errors"] > 0:
                    cell_str = f"{d['passed']}/{d['total']}+err"
                elif d["total"] == 0:
                    cell_str = ""
                else:
                    cell_str = f"{d['passed']}/{d['total']}"
                w.writerow([uc.id, fid, eid, tag_map.get(eid, ""),
                            d["passed"], d["total"], d["errors"], cell_str])
        w.writerow([])

        # ----------------------------------------------------------------
        # Section 3: Failures
        # ----------------------------------------------------------------
        w.writerow(["## Failures", uc.id])
        w.writerow(["use_case", "eval_id", "tag", "fixture_id", "run", "detail"])
        failing = [
            r for r in uc_rows
            if not r.skipped and not r.error and r.passed is False
        ]
        for r in sorted(failing, key=lambda x: (x.eval_id, x.fixture_id, x.run)):
            w.writerow([uc.id, r.eval_id, r.tag or "", r.fixture_id, r.run, r.detail or ""])
        w.writerow([])

    return output.getvalue()
