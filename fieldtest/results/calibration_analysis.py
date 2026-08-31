"""
fieldtest/results/calibration_analysis.py

Turning a panel's raw rows into per-eval agreement statistics.

results/calibration.py holds the statistics themselves; this module decides
what to feed them — one verdict per judge per output, keyed by use case as
well as eval id, and human labels where a fixture carries them.
"""
from __future__ import annotations

from pathlib import Path

from fieldtest.config import (
    CalibrationConfig,
    Config,
    extract_labels,
    load_fixture,
    resolve_set,
)
from fieldtest.errors import ConfigError
from fieldtest.fixtures import find_fixture_path
from fieldtest.results.calibration import (
    cohens_kappa,
    fleiss_kappa,
    mean_absolute_deviation,
    raw_agreement,
    signed_bias,
    spearman,
)


# ---------------------------------------------------------------------------
# Per-judge views
# ---------------------------------------------------------------------------

def _verdicts_by_eval(rows: list) -> dict:
    """(use_case, eval_id) → {(fixture_id, run): collapsed verdict} for binary rows.

    Keyed by use case as well as eval id because config enforces globally unique
    fixture ids but not eval ids: two use cases may both declare tone_professional,
    and merging them would report one agreement figure for two different evals.
    """
    from fieldtest.results.aggregator import collapse_verdicts

    grouped: dict = {}
    for r in rows:
        if r.skipped or r.error is not None or r.passed is None or r.score is not None:
            continue
        grouped.setdefault((r.use_case, r.eval_id), {}).setdefault(
            (r.fixture_id, r.run), []
        ).append(r)

    return {
        eval_key: {key: collapse_verdicts(reps) for key, reps in by_output.items()}
        for eval_key, by_output in grouped.items()
    }


def _scores_by_eval(rows: list) -> dict:
    """(use_case, eval_id) → {(fixture_id, run): mean score} for scored rows."""
    grouped: dict = {}
    for r in rows:
        if r.skipped or r.error is not None or r.score is None:
            continue
        grouped.setdefault((r.use_case, r.eval_id), {}).setdefault(
            (r.fixture_id, r.run), []
        ).append(r.score)

    return {
        eval_key: {key: sum(v) / len(v) for key, v in by_output.items()}
        for eval_key, by_output in grouped.items()
    }


def collect_human_labels(config: Config, base_dir: Path, set_name: str) -> dict:
    """(use_case, eval_id) → {(fixture_id, run): label} from every fixture in the set."""
    labels: dict = {}
    for uc in config.use_cases:
        try:
            fixture_ids = resolve_set(set_name, uc, base_dir)
        except Exception:
            continue
        for fid in fixture_ids:
            # The third place this lookup lived. runner and validate were moved
            # to find_fixture_path; this one was missed, so on the
            # fixtures/golden/ layout `fieldtest init` scaffolds, calibrate
            # found no human labels and silently dropped the judge-vs-human
            # section — no error, no warning, just a missing table.
            try:
                fixture_path = find_fixture_path(base_dir / uc.fixtures.directory, fid)
            except ConfigError:
                continue
            if not fixture_path.exists():
                continue
            for (eval_id, run), value in extract_labels(load_fixture(fixture_path, base_dir)).items():
                labels.setdefault((uc.id, eval_id), {})[(fid, run)] = value
    return labels


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _eval_types(config: Config) -> dict:
    """(use_case, eval_id) → ("binary" | "scored", scale)."""
    types: dict = {}
    for uc in config.use_cases:
        for ev in uc.evals:
            if ev.type != "llm":
                continue
            types[(uc.id, ev.id)] = ("scored" if not ev.binary else "binary", ev.scale)
    return types


def _display_names(types: dict) -> dict:
    """
    (use_case, eval_id) → label. Bare eval id where it is unambiguous, qualified
    only when the same id appears in more than one use case.
    """
    counts: dict = {}
    for _, eval_id in types:
        counts[eval_id] = counts.get(eval_id, 0) + 1
    return {
        (uc_id, eval_id): (eval_id if counts[eval_id] == 1 else f"{uc_id}/{eval_id}")
        for (uc_id, eval_id) in types
    }


def _binary_eval_stats(eval_key, per_judge, labels, threshold) -> dict:
    """Judge-to-judge and judge-to-human agreement for one binary eval."""
    names   = [name for name, verdicts in per_judge if verdicts]
    views   = [verdicts for _, verdicts in per_judge if verdicts]
    # A judge that errored on every call contributes no verdicts and drops out
    # of the pairwise matrix. Say so rather than quietly reporting a smaller
    # panel's kappa as if it were the configured panel's.
    absent  = [name for name, verdicts in per_judge if not verdicts]

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
        "judges_participating": len(names),
        "judges_configured":    len(per_judge),
        "judges_absent":        absent,
        "pairwise":        pairwise,
        "mean_agreement":  mean_agreement,
        "fleiss_kappa":    fleiss_kappa(views) if len(views) >= 2 else None,
        # Most contested first is the actionable ordering: these are the evals
        # whose pass_criteria need rewriting.
        "disagreement":    round(1 - mean_agreement, 6) if mean_agreement is not None else None,
    }

    human = labels.get(eval_key)
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


def _scored_eval_stats(eval_key, per_judge, labels, scale) -> dict:
    """Judge-to-judge and judge-to-human comparison for one scored eval."""
    names  = [name for name, scores in per_judge if scores]
    views  = [scores for _, scores in per_judge if scores]
    absent = [name for name, scores in per_judge if not scores]

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
        "judges_participating": len(names),
        "judges_configured":    len(per_judge),
        "judges_absent":        absent,
        "pairwise":       pairwise,
        "mean_mad":       mean_mad,
        "disagreement":   round(min(mean_mad / span, 1.0), 6) if mean_mad is not None else None,
    }

    human = labels.get(eval_key)
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
    threshold = (
        config.calibration.kappa_threshold
        if config.calibration else CalibrationConfig.model_fields["kappa_threshold"].default
    )
    types   = _eval_types(config)
    display = _display_names(types)

    binary_views = [(name, _verdicts_by_eval(rows)) for name, rows in judge_rows]
    scored_views = [(name, _scores_by_eval(rows))   for name, rows in judge_rows]

    evals: dict = {}
    for eval_key, (eval_type, scale) in types.items():
        label = display[eval_key]
        if eval_type == "binary":
            per_judge = [(name, view.get(eval_key, {})) for name, view in binary_views]
            if not any(v for _, v in per_judge):
                continue
            evals[label] = _binary_eval_stats(eval_key, per_judge, labels, threshold)
        else:
            per_judge = [(name, view.get(eval_key, {})) for name, view in scored_views]
            if not any(v for _, v in per_judge):
                continue
            evals[label] = _scored_eval_stats(eval_key, per_judge, labels, scale)

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
