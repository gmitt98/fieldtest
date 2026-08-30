"""
fieldtest/cli_reports.py

Commands that read past runs: history and diff.

Split out of cli.py, which had grown past 900 lines. Commands are plain
click.Command objects registered by cli.py with add_command(), rather than
decorated with @main.command() here, so these modules do not import cli.py and
no cycle exists.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Optional

import click

from fieldtest.cli_common import _default_config_path


@click.command()
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def history(config_path: Optional[str]):
    """List past result files, newest first."""
    path        = Path(config_path) if config_path else _default_config_path()
    base_dir    = path.resolve().parent
    results_dir = base_dir / "results"

    if not results_dir.exists():
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results, or\n"
            f"  'fieldtest init' if you haven't set up a project yet."
        )
        return

    result_files = sorted(results_dir.glob("*-data.json"), reverse=True)

    # Runs written before the -data.json naming are invisible to that glob.
    # A long-lived project can have most of its history in the old layout —
    # one had 24 of 32 — and listing the rest without a word reads as
    # "that is all there is".
    legacy = [
        f for f in results_dir.glob("*.json")
        if not f.name.endswith("-data.json")
    ]
    if not result_files:
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results."
        )
        return

    # Header
    header = (
        f"{'RUN ID':<26}  {'TIMESTAMP':<18}  {'SET':<12}  "
        f"{'FIXTURES':<10}  {'JUDGE':<28}  {'RIGHT':<8}  {'GOOD':<8}  {'SAFE':<8}"
    )
    click.echo(header)

    for p in result_files:
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue

        run_id        = data.get("run_id", p.stem)
        set_name      = data.get("set", "—")
        fixture_count = data.get("fixture_count", 0)
        summary       = data.get("summary", {})

        # A rate series is unreadable if the instrument changed mid-series.
        judge_block = data.get("judge") or {}
        judge_model = judge_block.get("model", "—")
        judge_str   = judge_model if len(judge_model) <= 28 else judge_model[:27] + "…"

        # Parse timestamp from run_id: 2026-03-22T14-30-00-a3f9
        try:
            ts_part = run_id[:19].replace("T", " ").replace("-", ":")
            # format: 2026:03:22 14:30:00 → fix date separators
            date_part, time_part = ts_part.split(" ")
            date_str = date_part.replace(":", "-")
            ts_display = f"{date_str} {time_part[:5]}"
        except Exception:
            ts_display = "—"

        def _tag_rate(tag: str) -> str:
            rates = []
            for uc_stats in summary.values():
                for stats in uc_stats.get(tag, {}).values():
                    fr = stats.get("failure_rate")
                    if fr is not None:
                        rates.append(fr)
            if not rates:
                return "—"
            avg = sum(rates) / len(rates)
            return f"{math.ceil(avg * 100)}%"

        right = _tag_rate("right")
        good  = _tag_rate("good")
        safe  = _tag_rate("safe")

        click.echo(
            f"{run_id:<26}  {ts_display:<18}  {set_name:<12}  "
            f"{fixture_count:<10}  {judge_str:<28}  {right:<8}  {good:<8}  {safe:<8}"
        )

    if legacy:
        click.echo(
            f"\n  {len(legacy)} older result file(s) in this directory are not "
            f"listed — they predate the current naming and carry no summary "
            f"fieldtest can read."
        )


@click.command()
@click.argument("run_id", default=None, required=False)
@click.option("--baseline", "baseline_id", default=None,
              help="Baseline run ID to compare against")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def diff(run_id: Optional[str], baseline_id: Optional[str], config_path: Optional[str]):
    """Compare two runs — default: most recent vs prior."""
    path        = Path(config_path) if config_path else _default_config_path()
    base_dir    = path.resolve().parent
    results_dir = base_dir / "results"

    if not results_dir.exists():
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results."
        )
        return

    result_files = sorted(results_dir.glob("*-data.json"), reverse=True)
    if not result_files:
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results."
        )
        return

    # Resolve current and baseline
    if run_id:
        current_path = results_dir / f"{run_id}-data.json"
    else:
        current_path = result_files[0]

    if baseline_id:
        baseline_path = results_dir / f"{baseline_id}-data.json"
    else:
        # most recent that isn't current
        others = [f for f in result_files if f != current_path]
        baseline_path = others[0] if others else None

    if not current_path.exists():
        click.echo(f"Run not found: {current_path}", err=True)
        sys.exit(1)

    current_data = json.loads(current_path.read_text())

    # An explicit --baseline has to actually recompute. The stored delta was
    # frozen at score time against whatever find_baseline() auto-detected then,
    # so reusing it silently compares against the wrong run — and with judge
    # fingerprints now filtering baselines, the run the user names is often
    # precisely the one auto-detection skipped.
    baseline_data: dict = {}
    if baseline_id:
        if baseline_path == current_path:
            # find_baseline() skips the current run for this reason; the explicit
            # path needs the same guard, or a mistyped id reports a clean
            # all-unchanged diff that reads as a passing regression check.
            click.echo(
                f"Baseline is the same run as the one being compared "
                f"({baseline_id}). Pass a different run id.",
                err=True,
            )
            sys.exit(1)
        if baseline_path is None or not baseline_path.exists():
            click.echo(f"Baseline not found: {baseline_path}", err=True)
            sys.exit(1)
        from fieldtest.results.aggregator import build_delta

        baseline_data = json.loads(baseline_path.read_text())
        delta = build_delta(current_data.get("summary", {}), baseline_path)
    else:
        delta = current_data.get("delta", {})
        base_id = delta.get("baseline_run_id")
        if base_id:
            auto_path = results_dir / f"{base_id}-data.json"
            if auto_path.exists():
                try:
                    baseline_data = json.loads(auto_path.read_text())
                except Exception:
                    baseline_data = {}

    click.echo(f"Comparing: {current_path.stem}")
    click.echo(f"Baseline:  {delta.get('baseline_run_id', '—')}")
    click.echo("")

    # Warn if the user is comparing across dataset snapshots — only fires when
    # both runs declare a version and they differ. Either side unversioned is
    # treated as backwards-compat silence.
    cur_ver     = current_data.get("dataset_version")
    base_ver    = baseline_data.get("dataset_version")
    base_run_id = delta.get("baseline_run_id")
    if cur_ver is not None and base_ver is not None and cur_ver != base_ver:
        click.echo(
            f"⚠ Dataset version mismatch — current: {cur_ver}, baseline: {base_ver}. "
            f"Deltas may reflect fixture changes, not model behavior."
        )
        click.echo("")

    # Same shape, for the instrument rather than the fixtures. A changed judge
    # produces the identical artifact and is the more frequent event, because
    # judge model versions deprecate on the provider's schedule, not the team's.
    from fieldtest.results.provenance import describe_judge_change

    cur_judge  = current_data.get("judge")
    base_judge = baseline_data.get("judge")

    if cur_judge and base_judge:
        change = describe_judge_change(cur_judge, base_judge)
        if change:
            click.echo(
                f"⚠ Judge mismatch — {change}. "
                f"Deltas may reflect the instrument changing, not model behavior."
            )
            click.echo("")
    elif cur_judge and base_run_id and base_judge is None:
        click.echo(
            "⚠ Baseline predates judge tracking — the judge that produced it is unknown."
        )
        click.echo("")

    # A baseline that lost most of its judge calls is a rate over whatever
    # survived. Comparing against it is not like-for-like, and `diff` is where
    # someone reads these numbers most closely.
    share = delta.get("baseline_error_share") or 0.0
    if base_run_id and share >= 0.1:
        click.echo(
            f"⚠ Baseline lost {share * 100:.0f}% of its judge calls to errors — "
            f"its rates cover only what survived, so these deltas are not "
            f"like-for-like."
        )
        click.echo("")

    increased = delta.get("increased", [])
    decreased = delta.get("decreased", [])
    unchanged = delta.get("unchanged", [])

    if increased:
        click.echo("Increased:")
        for item in increased:
            click.echo(
                f"  {item['eval_id']}: {item['previous']:.3f} → {item['current']:.3f} "
                f"({item['delta']:+.3f})"
            )

    if decreased:
        click.echo("Decreased:")
        for item in decreased:
            click.echo(
                f"  {item['eval_id']}: {item['previous']:.3f} → {item['current']:.3f} "
                f"({item['delta']:+.3f})"
            )

    if unchanged:
        click.echo(f"Unchanged: {', '.join(unchanged)}")

    if not increased and not decreased and not unchanged:
        click.echo("No comparable evals found between runs.")
