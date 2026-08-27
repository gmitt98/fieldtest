"""
fieldtest/calibrate.py

Core logic for `fieldtest calibrate` — measuring the instrument rather than
the system.

Every eval of type llm runs one judge, and there has been no way to ask whether
that judge deserves the authority the report gives it. A calibration run is N
scoring runs over one unchanged output set, differing only in judge config,
which is cheap here precisely because the generator already wrote its outputs
to disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fieldtest.config import Config, PanelJudge, extract_labels, load_fixture, resolve_set
from fieldtest.errors import ConfigError
from fieldtest.results.calibration import (
    cohens_kappa,
    fleiss_kappa,
    mean_absolute_deviation,
    raw_agreement,
    signed_bias,
    spearman,
)
from fieldtest.results.provenance import build_judge_block

_MISSING_PANEL = """\
No calibration panel configured.

  Add a calibration block to config.yaml:

    calibration:
      panel:
        - { provider: anthropic, model: claude-haiku-3-5-20251001 }
        - { provider: openai,    model: gpt-5 }

  A panel needs at least two judges — agreement is a property of a pair."""


def require_panel(config: Config) -> list[PanelJudge]:
    """The configured panel, or a config error naming what is missing."""
    if config.calibration is None or not config.calibration.panel:
        raise ConfigError(_MISSING_PANEL)
    return config.calibration.panel


def judge_label(judge: PanelJudge) -> str:
    return f"{judge.provider}/{judge.model}"


def config_for_judge(config: Config, judge: PanelJudge) -> Config:
    """A copy of the config with the judge swapped, leaving everything else alone."""
    swapped = config.model_copy(deep=True)
    swapped.defaults.provider = judge.provider
    swapped.defaults.model    = judge.model
    # Per-eval overrides would pin an eval to one model and defeat the panel.
    for uc in swapped.use_cases:
        for ev in uc.evals:
            ev.provider = None
            ev.model    = None
    return swapped


def project_calls(config: Config, base_dir: Path, set_name: str) -> dict:
    """
    Projected judge calls for a panel run: judges × fixtures × runs × judge_runs
    × llm evals. A four-judge panel with judge_runs: 3 is twelve times a normal
    run, and that belongs in front of the user before the run, not after.
    """
    from fieldtest.config import resolve_judge_runs, resolve_runs

    panel = require_panel(config)
    per_judge = 0
    for uc in config.use_cases:
        llm_evals = sum(1 for ev in uc.evals if ev.type == "llm")
        if not llm_evals:
            continue
        try:
            fixtures = len(resolve_set(set_name, uc, base_dir))
        except Exception:
            fixtures = 0
        per_judge += fixtures * resolve_runs(config, uc) * resolve_judge_runs(config, uc) * llm_evals

    return {
        "judges":     len(panel),
        "per_judge":  per_judge,
        "total":      per_judge * len(panel),
        "multiplier": len(panel) * max(
            (resolve_judge_runs(config, uc) for uc in config.use_cases), default=1
        ),
    }


# ---------------------------------------------------------------------------
# Per-judge views
# ---------------------------------------------------------------------------

def _verdicts_by_eval(rows: list) -> dict:
    """eval_id → {(fixture_id, run): collapsed verdict} for binary rows."""
    from fieldtest.results.aggregator import collapse_verdicts

    grouped: dict = {}
    for r in rows:
        if r.skipped or r.error is not None or r.passed is None or r.score is not None:
            continue
        grouped.setdefault(r.eval_id, {}).setdefault((r.fixture_id, r.run), []).append(r)

    return {
        eval_id: {key: collapse_verdicts(reps) for key, reps in by_output.items()}
        for eval_id, by_output in grouped.items()
    }


def _scores_by_eval(rows: list) -> dict:
    """eval_id → {(fixture_id, run): mean score} for scored rows."""
    grouped: dict = {}
    for r in rows:
        if r.skipped or r.error is not None or r.score is None:
            continue
        grouped.setdefault(r.eval_id, {}).setdefault((r.fixture_id, r.run), []).append(r.score)

    return {
        eval_id: {key: sum(v) / len(v) for key, v in by_output.items()}
        for eval_id, by_output in grouped.items()
    }


def collect_human_labels(config: Config, base_dir: Path, set_name: str) -> dict:
    """eval_id → {(fixture_id, run): label} from every fixture in the set."""
    labels: dict = {}
    for uc in config.use_cases:
        try:
            fixture_ids = resolve_set(set_name, uc, base_dir)
        except Exception:
            continue
        for fid in fixture_ids:
            fixture_path = base_dir / uc.fixtures.directory / f"{fid}.yaml"
            if not fixture_path.exists():
                continue
            for (eval_id, run), value in extract_labels(load_fixture(fixture_path)).items():
                labels.setdefault(eval_id, {})[(fid, run)] = value
    return labels


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _eval_types(config: Config) -> dict:
    """eval_id → ("binary" | "scored", scale)."""
    types: dict = {}
    for uc in config.use_cases:
        for ev in uc.evals:
            if ev.type != "llm":
                continue
            types[ev.id] = ("scored" if not ev.binary else "binary", ev.scale)
    return types


def _binary_eval_stats(eval_id, per_judge, labels, threshold) -> dict:
    """Judge-to-judge and judge-to-human agreement for one binary eval."""
    names   = [name for name, verdicts in per_judge if verdicts]
    views   = [verdicts for _, verdicts in per_judge if verdicts]

    pairwise = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            agreement = raw_agreement(views[i], views[j])
            kappa     = cohens_kappa(views[i], views[j])
            pairwise.append({
                "judges":    [names[i], names[j]],
                "agreement": agreement,
                "kappa":     kappa,
                "below_threshold": kappa is not None and kappa < threshold,
            })

    agreements = [p["agreement"] for p in pairwise if p["agreement"] is not None]
    mean_agreement = round(sum(agreements) / len(agreements), 6) if agreements else None

    stats = {
        "type":            "binary",
        "pairwise":        pairwise,
        "mean_agreement":  mean_agreement,
        "fleiss_kappa":    fleiss_kappa(views) if len(views) >= 2 else None,
        # Most contested first is the actionable ordering: these are the evals
        # whose pass_criteria need rewriting.
        "disagreement":    round(1 - mean_agreement, 6) if mean_agreement is not None else None,
    }

    human = labels.get(eval_id)
    if human:
        per_judge_human = []
        for name, verdicts in zip(names, views):
            compared = agreed = false_pass = false_fail = 0
            for key, verdict in verdicts.items():
                label = human.get(key)
                if label not in ("pass", "fail"):
                    continue
                compared += 1
                human_passed = label == "pass"
                if verdict == human_passed:
                    agreed += 1
                elif verdict:
                    false_pass += 1
                else:
                    false_fail += 1
            if compared:
                per_judge_human.append({
                    "judge":            name,
                    "labeled_runs":     compared,
                    "agreement":        round(agreed / compared, 6),
                    "judge_false_pass": false_pass,
                    "judge_false_fail": false_fail,
                })
        if per_judge_human:
            stats["human"] = sorted(
                per_judge_human, key=lambda d: d["agreement"], reverse=True
            )

    return stats


def _scored_eval_stats(eval_id, per_judge, labels, scale) -> dict:
    """Judge-to-judge and judge-to-human comparison for one scored eval."""
    names = [name for name, scores in per_judge if scores]
    views = [scores for _, scores in per_judge if scores]

    pairwise = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairwise.append({
                "judges":   [names[i], names[j]],
                "mad":      mean_absolute_deviation(views[i], views[j]),
                "spearman": spearman(views[i], views[j]),
            })

    mads = [p["mad"] for p in pairwise if p["mad"] is not None]
    mean_mad = round(sum(mads) / len(mads), 4) if mads else None

    # Normalized against the scale so scored and binary evals can be ranked in
    # one list; without it a 1-5 scale and a pass/fail eval are incomparable.
    span = (scale[1] - scale[0]) if scale and scale[1] > scale[0] else 1
    stats = {
        "type":           "scored",
        "pairwise":       pairwise,
        "mean_mad":       mean_mad,
        "disagreement":   round(min(mean_mad / span, 1.0), 6) if mean_mad is not None else None,
    }

    human = labels.get(eval_id)
    if human:
        human_scores = {
            k: v for k, v in human.items() if isinstance(v, int) and not isinstance(v, bool)
        }
        per_judge_human = []
        for name, scores in zip(names, views):
            mad = mean_absolute_deviation(scores, human_scores)
            if mad is None:
                continue
            per_judge_human.append({
                "judge":                    name,
                "labeled_runs":             len(set(scores) & set(human_scores)),
                "mean_absolute_deviation":  mad,
                "signed_bias":              signed_bias(scores, human_scores),
            })
        if per_judge_human:
            stats["human"] = sorted(
                per_judge_human, key=lambda d: d["mean_absolute_deviation"]
            )

    return stats


def analyze(config: Config, judge_rows: list, labels: dict) -> dict:
    """
    Per-eval agreement statistics across the panel.

    judge_rows is [(judge_label, rows)] — one entry per panel member, all over
    the same outputs/ directory.
    """
    threshold = config.calibration.kappa_threshold if config.calibration else 0.6
    types     = _eval_types(config)

    binary_views = [(name, _verdicts_by_eval(rows)) for name, rows in judge_rows]
    scored_views = [(name, _scores_by_eval(rows))   for name, rows in judge_rows]

    evals: dict = {}
    for eval_id, (eval_type, scale) in types.items():
        if eval_type == "binary":
            per_judge = [(name, view.get(eval_id, {})) for name, view in binary_views]
            if not any(v for _, v in per_judge):
                continue
            evals[eval_id] = _binary_eval_stats(eval_id, per_judge, labels, threshold)
        else:
            per_judge = [(name, view.get(eval_id, {})) for name, view in scored_views]
            if not any(v for _, v in per_judge):
                continue
            evals[eval_id] = _scored_eval_stats(eval_id, per_judge, labels, scale)

    ranked = sorted(
        (eid for eid in evals if evals[eid]["disagreement"] is not None),
        key=lambda eid: evals[eid]["disagreement"],
        reverse=True,
    )

    return {
        "evals":            evals,
        "ranked_by_disagreement": ranked,
        "kappa_threshold":  threshold,
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_calibration(
    config: Config,
    config_path: Path,
    set_name: str = "full",
    concurrency: int = 5,
    verbose: bool = False,
    progress=None,
) -> tuple[str, dict]:
    """
    Score the same outputs/ once per panel judge and report how much they agree.

    Reuses runner.score() rather than forking the scoring path — a calibration
    run is N scoring runs differing only in judge config. Artifact writing is
    suppressed for the panel members: a panel member's pass is not a
    measurement of the system and must not reach find_baseline().
    """
    from fieldtest.runner import make_run_id, score

    panel     = require_panel(config)
    base_dir  = config_path.resolve().parent
    run_id    = make_run_id()

    judge_rows  = []
    panel_meta  = []

    for judge in panel:
        label = judge_label(judge)
        if progress:
            progress(label)

        swapped = config_for_judge(config, judge)
        _, rows = score(
            config=swapped,
            config_path=config_path,
            set_name=set_name,
            allow_partial=False,
            concurrency=concurrency,
            verbose=verbose,
            write_artifacts=False,
        )
        judge_rows.append((label, rows))
        panel_meta.append({
            "judge":       label,
            "provider":    judge.provider,
            "model":       judge.model,
            "fingerprint": build_judge_block(swapped)["fingerprint"],
            "calls":       len(rows),
            "errors":      sum(1 for r in rows if r.error is not None),
        })

    labels   = collect_human_labels(config, base_dir, set_name)
    analysis = analyze(config, judge_rows, labels)

    return run_id, {
        "run_id":       run_id,
        "set":          set_name,
        "kind":         "calibration",
        "panel":        panel_meta,
        "has_labels":   bool(labels),
        **analysis,
    }


def write_calibration(data: dict, output_dir: Path, run_id: str) -> None:
    """Write {run_id}-calibration.json and {run_id}-calibration.md."""
    import json

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{run_id}-calibration.json").write_text(json.dumps(data, indent=2))
    (output_dir / f"{run_id}-calibration.md").write_text(format_calibration(data))


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
    for eval_id in ranked or data["evals"]:
        stats = data["evals"][eval_id]
        lines.append(f"### {eval_id}")

        if stats["type"] == "binary":
            fleiss = stats.get("fleiss_kappa")
            lines.append(
                f"panel Fleiss' kappa: {fleiss if fleiss is not None else '—'} "
                f"| mean raw agreement: "
                f"{round(stats['mean_agreement'] * 100, 1)}%"
                if stats.get("mean_agreement") is not None else "no shared outputs"
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
