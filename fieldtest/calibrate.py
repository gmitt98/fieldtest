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

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fieldtest.config import Config, PanelJudge, resolve_set
from fieldtest.errors import ConfigError
from fieldtest.results.calibration_analysis import analyze, collect_human_labels
from fieldtest.results.calibration_report import format_calibration
from fieldtest.results.provenance import build_judge_block

_MISSING_PANEL = """\
No calibration panel configured.

  Add a calibration block to config.yaml:

    calibration:
      panel:
        - { provider: anthropic, model: claude-haiku-4-5 }
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
        # Panel size only. per_judge already includes judge_runs, and the
        # user's own scoring runs do too, so folding it in again would quote a
        # multiple of a run they never make.
        "multiplier": len(panel),
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

    # Panel members are independent passes over the same files and often target
    # different providers, so running them one after another spends the sum of
    # their latencies for no reason. --concurrency stays the total in-flight
    # budget rather than becoming per-judge, so overlapping judges does not
    # quietly multiply the load a user configured.
    # Cap the pool at the budget too. Giving every judge a floor of one worker
    # while running all of them at once means a four-judge panel at
    # --concurrency 1 puts four calls in flight, which is the opposite of what
    # someone throttling a rate-limited account asked for.
    parallel_judges       = max(1, min(len(panel), concurrency))
    per_judge_concurrency = max(1, concurrency // parallel_judges)

    def _score_with(judge: PanelJudge):
        swapped = config_for_judge(config, judge)
        _, rows = score(
            config=swapped,
            config_path=config_path,
            set_name=set_name,
            allow_partial=False,
            concurrency=per_judge_concurrency,
            verbose=verbose,
            write_artifacts=False,
        )
        return swapped, rows

    if progress:
        for judge in panel:
            progress(judge_label(judge))

    with ThreadPoolExecutor(max_workers=parallel_judges) as pool:
        # map preserves panel order, so the report lists judges as configured.
        scored = list(pool.map(_score_with, panel))

    judge_rows  = []
    panel_meta  = []

    for judge, (swapped, rows) in zip(panel, scored):
        label = judge_label(judge)
        judge_rows.append((label, rows))
        panel_meta.append({
            "judge":       label,
            "provider":    judge.provider,
            "model":       judge.model,
            "fingerprint": build_judge_block(swapped)["fingerprint"],
            # Judge calls only. A regex or rule row never reached a provider,
            # and counting it here would contradict the projection printed
            # moments earlier by project_calls().
            "calls":       sum(1 for r in rows if r.type == "llm"),
            "errors":      sum(1 for r in rows if r.type == "llm" and r.error is not None),
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

    # Both built before either is written, matching write_results(): a panel
    # run has already been paid for by the time we get here, and a formatting
    # error must not leave data on disk with no report beside it.
    json_content = json.dumps(data, indent=2, default=str)
    md_content   = format_calibration(data)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{run_id}-calibration.json").write_text(json_content)
    (output_dir / f"{run_id}-calibration.md").write_text(md_content)


# The renderer lives in results/ with the other report writers; re-exported
# here so `from fieldtest.calibrate import format_calibration` keeps working.
from fieldtest.results.calibration_report import format_calibration  # noqa: E402
