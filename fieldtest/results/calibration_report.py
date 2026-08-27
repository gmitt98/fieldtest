"""
fieldtest/results/calibration_report.py

Markdown rendering for a calibration run — the panel, the per-eval agreement
statistics, and the ranked disagreement list that is the actionable output.

Split from calibrate.py for the same reason report.py and html.py are split
from writer.py: computing the numbers and rendering them are separate jobs.
"""
from __future__ import annotations


def format_calibration(data: dict) -> str:
    """The calibration report: panel, per-eval statistics, ranked disagreement."""
    lines: list[str] = ["# Judge Calibration", ""]
    lines.append(f"run: {data['run_id']} | set: {data['set']}")
    lines.append("")
    lines.append(
        "This measures the instrument, not the system. It is not a measurement "
        "of your model and does not participate in `fieldtest diff`."
    )
    lines.append("")

    # --- Panel --------------------------------------------------------------
    lines.append("### Panel")
    lines.append("| judge | fingerprint | calls | errors |")
    lines.append("|-------|-------------|-------|--------|")
    for member in data["panel"]:
        lines.append(
            f"| {member['judge']} | {member['fingerprint']} | "
            f"{member['calls']} | {member['errors']} |"
        )
    lines.append("")

    # --- Ranked disagreement ------------------------------------------------
    ranked = data.get("ranked_by_disagreement", [])
    if ranked:
        lines.append("### Evals by Judge Disagreement")
        lines.append("Most contested first — these are the evals whose criteria need rewriting.")
        lines.append("")
        lines.append("| rank | eval | disagreement |")
        lines.append("|------|------|--------------|")
        for i, eval_id in enumerate(ranked, start=1):
            score = data["evals"][eval_id]["disagreement"]
            lines.append(f"| {i} | {eval_id} | {round(score * 100, 1)}% |")
        lines.append("")

    # --- Per eval -----------------------------------------------------------
    threshold = data.get("kappa_threshold", 0.6)
    # Ranked first, then anything the ranking could not score. An eval only one
    # judge could rule on has no disagreement figure, and dropping it would hide
    # exactly the eval the panel failed to evaluate.
    rendered = list(ranked) + [e for e in data["evals"] if e not in set(ranked)]
    for eval_id in rendered:
        stats = data["evals"][eval_id]
        lines.append(f"### {eval_id}")

        absent = stats.get("judges_absent") or []
        if absent:
            lines.append(
                f"⚠ {len(absent)} of {stats.get('judges_configured', '?')} judges "
                f"produced no verdict here ({', '.join(absent)}); "
                f"the figures below cover the rest."
            )
            lines.append("")

        if stats["type"] == "binary":
            fleiss = stats.get("fleiss_kappa")
            mean_agreement = stats.get("mean_agreement")

            if mean_agreement is None:
                lines.append("no two judges ruled on a shared output")
                lines.append("")
                continue

            lines.append(
                f"panel Fleiss' kappa: {fleiss if fleiss is not None else '—'} "
                f"| mean raw agreement: {round(mean_agreement * 100, 1)}%"
            )
            lines.append("")
            lines.append("| judge pair | raw agreement | Cohen's kappa |")
            lines.append("|------------|---------------|---------------|")
            for pair in stats["pairwise"]:
                a = pair["agreement"]
                k = pair["kappa"]
                flag = " ⚠" if pair["below_threshold"] else ""
                lines.append(
                    f"| {pair['judges'][0]} vs {pair['judges'][1]} | "
                    f"{round(a * 100, 1) if a is not None else '—'}% | "
                    f"{k if k is not None else '—'}{flag} |"
                )
            lines.append("")
            lines.append(
                f"  ⚠ marks a pair below the {threshold} kappa threshold. Two judges "
                f"that both always answer pass agree 95% of the time on an eval whose "
                f"true failure rate is 5%, and have demonstrated nothing."
            )
        else:
            lines.append(
                f"mean pairwise absolute deviation: "
                f"{stats['mean_mad'] if stats['mean_mad'] is not None else '—'}"
            )
            lines.append("")
            lines.append("| judge pair | mean abs deviation | Spearman |")
            lines.append("|------------|--------------------|----------|")
            for pair in stats["pairwise"]:
                lines.append(
                    f"| {pair['judges'][0]} vs {pair['judges'][1]} | "
                    f"{pair['mad'] if pair['mad'] is not None else '—'} | "
                    f"{pair['spearman'] if pair['spearman'] is not None else '—'} |"
                )
        lines.append("")

        human = stats.get("human")
        if human:
            lines.append("**Against human labels** — the number that actually matters.")
            lines.append("")
            if stats["type"] == "binary":
                lines.append("| judge | labeled runs | agreement | false pass | false fail |")
                lines.append("|-------|--------------|-----------|------------|------------|")
                for entry in human:
                    lines.append(
                        f"| {entry['judge']} | {entry['labeled_runs']} | "
                        f"{round(entry['agreement'] * 100, 1)}% | "
                        f"{entry['judge_false_pass']} | {entry['judge_false_fail']} |"
                    )
            else:
                lines.append("| judge | labeled runs | mean abs deviation | signed bias |")
                lines.append("|-------|--------------|--------------------|-------------|")
                for entry in human:
                    lines.append(
                        f"| {entry['judge']} | {entry['labeled_runs']} | "
                        f"{entry['mean_absolute_deviation']} | {entry['signed_bias']} |"
                    )
            lines.append("")

    if not data.get("has_labels"):
        lines.append("---")
        lines.append("")
        lines.append(
            "No fixture labels found. Judge-to-judge agreement without ground truth "
            "measures shared bias as readily as shared accuracy — two judges that agree "
            "and are both wrong look identical to two that agree and are both right. "
            "Add `labels:` to your fixtures to rank judges on accuracy."
        )
        lines.append("")

    return "\n".join(lines)
