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
    Binary evals: pass rate. Scored evals: avg score (separate line).
    Only counts non-skipped, non-error rows.
    """
    tag_totals: dict[str, dict] = defaultdict(
        lambda: {"passed": 0, "total": 0, "scores": [], "scale_max": None}
    )
    for r in rows:
        if r.use_case != use_case_id or r.skipped or r.error:
            continue
        tag = (r.tag or "untagged").upper()
        if r.score is not None:
            tag_totals[tag]["scores"].append(r.score)
        else:
            tag_totals[tag]["total"] += 1
            if r.passed:
                tag_totals[tag]["passed"] += 1

    if not any(v["total"] or v["scores"] for v in tag_totals.values()):
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
        if d["total"]:
            pct = f"{round(d['passed'] / d['total'] * 100)}%"
            lines.append(f"| {tag} | {pct} | {d['passed']} / {d['total']} |")
        if d["scores"]:
            avg = round(sum(d["scores"]) / len(d["scores"]), 1)
            lines.append(f"| {tag} (scored) | avg {avg} | {len(d['scores'])} scores |")
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
    cell: dict = defaultdict(
        lambda: {"passed": 0, "total": 0, "errors": 0, "scores": []}
    )
    for r in uc_rows:
        if r.skipped:
            continue
        key = (r.fixture_id, r.eval_id)
        if r.error:
            cell[key]["errors"] += 1
        elif r.score is not None:
            cell[key]["scores"].append(r.score)
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
            if d["scores"]:
                avg = round(sum(d["scores"]) / len(d["scores"]), 1)
                cells.append(f"avg {avg}")
            elif d["errors"] > 0 and d["total"] == 0:
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
    unsupported_params: Optional[list[str]] = None,
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
    from fieldtest.config import resolve_judge_runs, resolve_runs
    runs = config.defaults.runs
    header_judge_runs = 1
    if config.use_cases:
        runs = resolve_runs(config, config.use_cases[0])
        header_judge_runs = resolve_judge_runs(config, config.use_cases[0])

    # Two different numbers, and the header said only one of them. With
    # judge_runs: 3 a run makes three judge calls per output while the header
    # read "3 evaluations per eval" — a third of what the bill showed.
    # `runs` are generator outputs; `judge_runs` are repeat verdicts on each.
    # fixture_count is the total across use cases. Multiplying it by runs
    # claims a per-eval figure that is only true when there is one use case:
    # a project with 11 resume fixtures and 3 cover-letter ones reported
    # "42 scored output(s) per eval" when no eval had more than 33.
    if len(config.use_cases) > 1:
        scored = f"{fixture_count} fixture(s) across {len(config.use_cases)} use cases"
    else:
        scored = f"{fixture_count * runs} scored output(s) per eval"
    if header_judge_runs > 1:
        scored += f", judged {header_judge_runs}× each"

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
            f"{scored}"
        )

    # Only when an LLM judge is actually configured — a rules-only project has
    # no judge, and naming one would describe an instrument that never ran.
    if any(ev.type == "llm" for uc in config.use_cases for ev in uc.evals):
        lines.append(
            f"judge: {config.defaults.provider} {config.defaults.model} | "
            f"temperature: {config.defaults.judge_temperature}"
            + (f" | seed: {config.defaults.judge_seed}" if config.defaults.judge_seed is not None else "")
        )
    if unsupported_params:
        lines.append(
            "⚠ judge parameters ignored by provider: "
            + ", ".join(unsupported_params)
        )

    # Deltas against a baseline written before judge tracking are still shown —
    # blanking them out on upgrade is worse — but the caveat travels with them.
    changed = delta.get("sample_changed") or []
    if delta.get("baseline_run_id") and changed:
        shown = ", ".join(changed[:4]) + (" …" if len(changed) > 4 else "")
        lines.append(
            f"⚠ {len(changed)} eval(s) scored a different number of outputs than "
            f"the baseline ({shown}) — the deltas include a change of population, "
            f"not only a change in the system"
        )

    if not delta.get("baseline_run_id") and delta.get("no_baseline_reason"):
        lines.append(
            f"no baseline: {delta['no_baseline_reason']}. "
            f"Every 'vs prior' below reads '—' for that reason, not because "
            f"nothing moved."
        )

    share = delta.get("baseline_error_share") or 0.0
    if delta.get("baseline_run_id") and share >= 0.1:
        lines.append(
            f"⚠ baseline lost {share * 100:.0f}% of its judge calls to errors — "
            f"its rates are over whatever survived, so these deltas are not a "
            f"like-for-like comparison"
        )

    if delta.get("baseline_run_id") and delta.get("baseline_pre_judge"):
        lines.append(
            "⚠ baseline predates judge tracking — the judge that produced it is "
            "unknown, so deltas may reflect an instrument change."
        )

    current_judge_runs = 1
    if config.use_cases:
        from fieldtest.config import resolve_judge_runs
        current_judge_runs = resolve_judge_runs(config, config.use_cases[0])

    baseline_judge_runs = delta.get("baseline_judge_runs", current_judge_runs)
    if delta.get("baseline_run_id") and baseline_judge_runs != current_judge_runs:
        lines.append(
            f"⚠ baseline judged each output {baseline_judge_runs}× and this run "
            f"{current_judge_runs}× — collapsing resolves ties to fail, so the "
            f"failure rates move with the repetition count on their own, and "
            f"the judge spread figures are not comparable at all."
        )

    # Judge errors shrink the sample rather than failing the run, so say so where
    # the rates are read rather than leaving it to be inferred from two numbers.
    from fieldtest.results.aggregator import summarize_judge_errors
    judge_errors = summarize_judge_errors(summary)
    if judge_errors:
        lines.append(
            f"⚠ judge errors: {judge_errors['failed']} of {judge_errors['total']} "
            f"calls failed after retry."
        )
        lines.append(
            "  affected evals: "
            + ", ".join(
                f"{eval_id} ({scored} of {attempted} runs scored)"
                for eval_id, scored, attempted in judge_errors["affected"]
            )
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
        # Keyed by (use_case, eval_id). Eval ids are only unique within a use
        # case, so a bare-id index printed one use case's movement against
        # another's row — the same collision that nulled failure_rate in
        # build_summary. Falls back to the bare id for result files written
        # before delta entries carried a use case.
        delta_idx: dict[tuple, dict] = {}
        for bucket in ("increased", "decreased"):
            for item in delta.get(bucket, []):
                delta_idx[(item.get("use_case"), item["eval_id"])] = item
                delta_idx.setdefault((None, item["eval_id"]), item)

        unchanged_idx = {
            (u.get("use_case"), u["eval_id"]) for u in delta.get("unchanged_keys", [])
        } or {(None, e) for e in delta.get("unchanged", [])}

        # (eval_id, path): the list has no eval column, so the eval each
        # entry belongs to has to travel with it.
        floor_hit_rows: list[tuple[str, str]] = []
        error_eval_ids: list[tuple[str, int]] = []

        for tag in ["right", "good", "safe"]:
            tag_stats = uc_stats.get(tag, {})
            if not tag_stats:
                continue

            # Build labels lookup from evals list
            labels_map: dict[str, str] = {}
            for ev in uc.evals:
                labels_map[ev.id] = "|".join(ev.labels) if ev.labels else "—"

            lines.append(f"### {tag.upper()}")
            lines.append(
                "| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |"
            )
            lines.append(
                "|------|--------|----------|---|------|-----------|--------|---------|"
            )

            for eval_id, stats in tag_stats.items():
                fr    = stats.get("failure_rate")
                mean  = stats.get("mean")
                fh    = stats.get("floor_hits", 0)
                errs  = stats.get("error_count", 0)

                # Display pass rate (1 - failure_rate) so higher = better. The
                # interval is the failure-rate interval inverted, and it travels
                # with the rate: a point estimate at runs: 5 is gating on noise.
                ci = stats.get("failure_rate_ci")
                if fr is None:
                    pr_str = "—"
                elif ci:
                    low, high = round((1 - ci[1]) * 100), round((1 - ci[0]) * 100)
                    pr_str = f"{round((1 - fr) * 100)}% [{low}–{high}%]"
                else:
                    pr_str = f"{round((1 - fr) * 100)}%"
                mean_str = "—"
                if mean is not None:
                    for ev in uc.evals:
                        if ev.id == eval_id and ev.scale:
                            mean_str = f"{mean}/{ev.scale[1]}"
                            break

                # vs prior — delta is stored as (cur_failure_rate - prev_failure_rate),
                # so negate it to express as pass rate delta: positive = improvement.
                d_item = delta_idx.get((uc.id, eval_id)) or (
                    delta_idx.get((None, eval_id))
                    if not any(k[0] for k in delta_idx) else None
                )
                if d_item is not None:
                    d = d_item["delta"]
                    if mean is not None:
                        vs_str = f"+{round(d, 2)}" if d > 0 else f"{round(d, 2)}"
                    else:
                        pd = -d  # pass rate delta = negated failure rate delta
                        vs_str = f"+{round(pd * 100, 2)}%" if pd > 0 else f"{round(pd * 100, 2)}%"
                elif (uc.id, eval_id) in unchanged_idx or (
                    (None, eval_id) in unchanged_idx
                ):
                    vs_str = "↔"
                else:
                    vs_str = "—"

                lbl_str = labels_map.get(eval_id, "—")

                # An eval scored on fewer runs than it attempted is reporting a
                # rate from a shrunken sample. Mark it where the rate is read.
                scored    = stats.get("total_runs") or 0
                # Outputs, not judge calls: the rate's denominator is outputs,
                # so a shrunken-sample warning has to be counted in the same unit.
                attempted = stats.get("outputs_attempted") or (scored + errs)
                err_str   = (
                    f"{errs} ⚠ {scored}/{attempted} scored" if errs else f"{errs}"
                )

                lines.append(
                    f"| {eval_id} | {lbl_str} | {pr_str} | {scored} | {mean_str} | "
                    f"{fh} | {err_str} | {vs_str} |"
                )

                if errs > 0:
                    error_eval_ids.append((eval_id, errs))
                if fh > 0:
                    # The list has to name the outputs the count counted. The
                    # count is collapsed by majority over an output's
                    # repetitions, ties to floor (build_summary); de-duplicating
                    # the raw per-call rows instead listed every output where a
                    # single repetition hit the floor, so at judge_runs: 3 an
                    # output scored 1, 5, 5 was handed to the user to review
                    # beside a count that excluded it. Same rule, same unit.
                    scale_min_fh = next(
                        (
                            ev.scale[0]
                            for ev in uc.evals
                            if ev.id == eval_id and ev.scale
                        ),
                        None,
                    )
                    by_output_fh: dict[tuple, list] = {}
                    for row in rows:
                        # Eval ids are unique only within a use case, so the
                        # use case has to be matched too.
                        if (
                            row.use_case == uc.id
                            and row.eval_id == eval_id
                            and not row.skipped
                            and row.error is None
                            and row.score is not None
                        ):
                            by_output_fh.setdefault(
                                (row.fixture_id, row.run), []
                            ).append(row.score)
                    for (fixture_id, run), rep_scores in by_output_fh.items():
                        at_floor = sum(1 for s in rep_scores if s == scale_min_fh)
                        if at_floor and at_floor * 2 >= len(rep_scores):
                            floor_hit_rows.append(
                                (eval_id, f"outputs/{fixture_id}/run-{run}.txt")
                            )

            lines.append("")

        # --- Judge vs human ----------------------------------------------
        # Labels score the judge, not the system. Two judges that agree with each
        # other and are both wrong look identical until a human is in the picture.
        label_rows: list[str] = []
        for tag in ["right", "good", "safe"]:
            for eval_id, stats in uc_stats.get(tag, {}).items():
                if "labeled_runs" not in stats:
                    continue
                if "mean_absolute_deviation" in stats:
                    agreement = "—"
                    detail = f"mean abs deviation {stats['mean_absolute_deviation']}"
                else:
                    agreement = f"{round(stats['judge_agreement'] * 100, 1)}%"
                    detail = (
                        f"{stats['judge_false_pass']} false pass, "
                        f"{stats['judge_false_fail']} false fail"
                    )
                label_rows.append(
                    f"| {eval_id} | {stats['labeled_runs']} | {agreement} | {detail} |"
                )

        if label_rows:
            lines.append("### Judge vs Human Labels")
            lines.append("| eval | labeled runs | agreement | errors |")
            lines.append("|------|--------------|-----------|--------|")
            lines.extend(label_rows)
            lines.append("")
            lines.append(
                "  a false pass is an output a human failed and the judge passed — "
                "on a safe eval that is the error that matters."
            )
            lines.append("")

        # --- Judge repeatability -----------------------------------------
        # How much of the reported spread belongs to the instrument rather than
        # the system. Near zero is a well-specified eval; anything else is an
        # eval whose criteria are ambiguous. That diagnostic is the point.
        repeat_rows: list[str] = []
        for tag in ["right", "good", "safe"]:
            for eval_id, stats in uc_stats.get(tag, {}).items():
                if "judge_runs" not in stats:
                    continue
                disagreement = stats.get("judge_disagreement_rate")
                dis_str = f"{round(disagreement * 100, 1)}%" if disagreement is not None else "—"
                sys_str = (
                    f"{stats['system_stddev']}" if stats.get("system_stddev") is not None else "—"
                )
                jdg_str = (
                    f"{stats['judge_stddev']}" if stats.get("judge_stddev") is not None else "—"
                )
                repeat_rows.append(f"| {eval_id} | {dis_str} | {sys_str} | {jdg_str} |")

        if repeat_rows:
            reps = next(
                stats["judge_runs"]
                for tag in ["right", "good", "safe"]
                for stats in uc_stats.get(tag, {}).values()
                if "judge_runs" in stats
            )
            lines.append(f"### Judge Repeatability (judge_runs: {reps})")
            lines.append("| eval | judge disagreement | system spread | judge spread |")
            lines.append("|------|--------------------|---------------|--------------|")
            lines.extend(repeat_rows)
            lines.append("")
            lines.append(
                "  spread near zero means the judge is repeatable; a judge spread "
                "comparable to the system spread means the eval's criteria are ambiguous."
            )
            lines.append("")

        # Floor hits
        if floor_hit_rows:
            # One block per eval. A single flat list labelled with one eval id
            # printed the same output once per eval that scored it at the floor
            # and named the wrong criterion and the wrong scale for all but the
            # first — telling the user to review a 0 on a 0-10 scale as a 1/5.
            by_eval_fh: dict[str, list[str]] = {}
            for eval_id_fh, path in floor_hit_rows:
                by_eval_fh.setdefault(eval_id_fh, []).append(path)
            for eval_id_fh, paths in by_eval_fh.items():
                lines.append(f"⚠ floor hits — {', '.join(paths)}")
                scale_str = ""
                for ev in uc.evals:
                    if ev.id == eval_id_fh and ev.scale:
                        scale_str = f"{ev.scale[0]}/{ev.scale[1]}"
                        break
                lines.append(
                    f"  eval: {eval_id_fh} scored {scale_str} — review these outputs"
                )
            lines.append("")

        # Judge errors
        if error_eval_ids:
            for eval_id, count in error_eval_ids:
                lines.append(
                    f"⚠ judge errors — {count} calls failed for {eval_id}; "
                    f"excluded from pass rate"
                )
            # Providers say why. Repeating generic advice over a specific
            # message sends people to check a key that is working: a run that
            # died on an exhausted balance was told to check its credentials.
            causes = {
                "credit balance": "the account is out of credit",
                "quota": "the account is over quota",
                "rate limit": "rate limited beyond the retry policy",
                "authentication": "the API key was rejected",
                "not found": "the model id was not recognised",
                "permission": "the key lacks access to that model",
            }
            reason = next(
                (text for marker, text in causes.items()
                 if any(marker in (r.error or "").lower()
                        for r in rows if r.use_case == uc.id and r.error)),
                None,
            )
            # A `rule` eval runs the user's own Python. Its errors are never
            # about a credential, and sending someone to check their API key
            # over a ValueError in their own function sends them to the one
            # place the bug is not.
            errored = [r for r in rows
                       if r.use_case == uc.id and r.error and r.type == "rule"]
            if errored and all(
                r.type == "rule" for r in rows
                if r.use_case == uc.id and r.error
            ):
                first = errored[0]
                lines.append(
                    f"  these are `rule` evals — the error is in your own Python "
                    f"in evals/rules.py, not in a provider. First one: "
                    f"{first.eval_id} — {first.error}"
                )
            elif reason:
                lines.append(f"  every failure says the same thing: {reason}")
            else:
                lines.append(
                    "  re-run with --concurrency 1 to isolate; "
                    "check your API key if errors persist"
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
        w.writerow(["use_case", "tag", "pass_rate_pct", "passed", "total", "avg_score", "score_count"])
        tag_totals: dict[str, dict] = defaultdict(
            lambda: {"passed": 0, "total": 0, "scores": []}
        )
        for r in uc_rows:
            if r.skipped or r.error:
                continue
            tag = (r.tag or "untagged").upper()
            if r.score is not None:
                tag_totals[tag]["scores"].append(r.score)
            else:
                tag_totals[tag]["total"] += 1
                if r.passed:
                    tag_totals[tag]["passed"] += 1
        for tag in ["RIGHT", "GOOD", "SAFE"]:
            if tag in tag_totals:
                d = tag_totals[tag]
                pct = round(d["passed"] / d["total"] * 100) if d["total"] else ""
                avg = round(sum(d["scores"]) / len(d["scores"]), 1) if d["scores"] else ""
                w.writerow([uc.id, tag, pct, d["passed"], d["total"], avg, len(d["scores"]) or ""])
        w.writerow([])

        # ----------------------------------------------------------------
        # Section 2: Fixture × Eval Matrix
        # ----------------------------------------------------------------
        w.writerow(["## Fixture x Eval Matrix", uc.id])
        w.writerow(["use_case", "fixture_id", "eval_id", "tag", "passes", "total", "errors", "cell"])

        eval_ids = [ev.id for ev in uc.evals]
        fixture_ids = sorted({r.fixture_id for r in uc_rows if not r.skipped})
        tag_map: dict[str, str] = {ev.id: (ev.tag or "") for ev in uc.evals}

        cell: dict = defaultdict(
            lambda: {"passed": 0, "total": 0, "errors": 0, "scores": []}
        )
        for r in uc_rows:
            if r.skipped:
                continue
            key = (r.fixture_id, r.eval_id)
            if r.error:
                cell[key]["errors"] += 1
            elif r.score is not None:
                cell[key]["scores"].append(r.score)
            else:
                cell[key]["total"] += 1
                if r.passed:
                    cell[key]["passed"] += 1

        for fid in fixture_ids:
            for eid in eval_ids:
                d = cell[(fid, eid)]
                if d["scores"]:
                    avg = round(sum(d["scores"]) / len(d["scores"]), 1)
                    cell_str = f"avg {avg}"
                elif d["errors"] > 0 and d["total"] == 0:
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
