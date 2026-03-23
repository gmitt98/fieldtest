"""
fieldtest/results/report.py

write_markdown() / format_report() — generates the human-readable eval report.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fieldtest.config import Config, ResultRow


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
    """
    fixture_ids = sorted({r.fixture_id for r in rows if not r.skipped})
    fixture_count = len(fixture_ids)

    # Determine runs from config (use first use_case as representative)
    from fieldtest.config import resolve_runs
    runs = config.defaults.runs
    if config.use_cases:
        runs = resolve_runs(config, config.use_cases[0])

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []

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

        # Delta index: eval_id → {previous, current, delta}
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

            lines.append("")
            lines.append(f"### {tag.upper()}")
            lines.append("| eval | failure rate | mean | floor hits | errors | vs prior |")
            lines.append("|------|-------------|------|-----------|--------|---------|")

            for eval_id, stats in tag_stats.items():
                fr      = stats.get("failure_rate")
                mean    = stats.get("mean")
                fh      = stats.get("floor_hits", 0)
                errs    = stats.get("error_count", 0)
                total   = stats.get("total_runs", 0)

                fr_str   = f"{round(fr * 100)}%" if fr is not None else "—"
                mean_str = f"{mean}/{config.use_cases[0].evals[0].scale[1] if False else '?'}" if mean is not None else "—"
                # Find the eval's scale for mean display
                for ev in uc.evals:
                    if ev.id == eval_id and ev.scale:
                        mean_str = f"{mean}/{ev.scale[1]}"
                        break

                # vs prior
                if eval_id in delta_idx:
                    d = delta_idx[eval_id]["delta"]
                    vs_str = f"+{round(d * 100 if fr is not None else d, 2)}%" if d > 0 else f"{round(d * 100 if fr is not None else d, 2)}%"
                    if mean is not None:
                        vs_str = f"+{round(d, 2)}" if d > 0 else f"{round(d, 2)}"
                elif eval_id in delta.get("unchanged", []):
                    vs_str = "↔"
                else:
                    vs_str = "—"

                lines.append(f"| {eval_id} | {fr_str} | {mean_str} | {fh} | {errs} | {vs_str} |")

                if errs > 0:
                    error_eval_ids.append((eval_id, errs))

                if fh > 0:
                    # Find the floor-hit rows
                    for row in rows:
                        if row.eval_id == eval_id and row.floor_hit:
                            floor_hit_rows.append(
                                f"outputs/{row.fixture_id}/run-{row.run}.txt"
                            )

        # Floor hits section
        if floor_hit_rows:
            lines.append("")
            lines.append("---")
            eval_id_fh = next(
                (r.eval_id for r in rows if r.floor_hit), "scored_eval"
            )
            lines.append(f"⚠ floor hits — {', '.join(floor_hit_rows)}")
            if floor_hit_rows:
                scale_str = ""
                for ev in uc.evals:
                    if ev.id == eval_id_fh and ev.scale:
                        scale_str = f"{ev.scale[0]}/{ev.scale[1]}"
                        break
                lines.append(f"  eval: {eval_id_fh} scored {scale_str} — review these outputs")

        # Judge errors section
        if error_eval_ids:
            lines.append("")
            lines.append("---")
            for eval_id, count in error_eval_ids:
                lines.append(
                    f"⚠ judge errors — {count} calls failed for {eval_id}; "
                    f"excluded from failure rate"
                )
            lines.append(
                "  re-run with --concurrency 1 to isolate; "
                "check ANTHROPIC_API_KEY if errors persist"
            )

    return "\n".join(lines)
