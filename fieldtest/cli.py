"""
fieldtest/cli.py

Click entry point. All CLI commands: validate, score, history, diff, clean, init.
"""
from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path
from typing import Optional

import click

from fieldtest.errors import FieldtestError


def _handle_error(e: Exception) -> None:
    """Print error to stderr and exit 1. Unexpected errors show traceback + bug URL."""
    if isinstance(e, FieldtestError):
        click.echo(str(e), err=True)
        sys.exit(1)
    else:
        click.echo(traceback.format_exc(), err=True)
        click.echo(
            "Please file a bug at https://github.com/galenmittermann/fieldtest/issues",
            err=True,
        )
        sys.exit(1)


def _load_config(config_path: Path):
    """Load and validate config. Calls sys.exit(1) on error."""
    from fieldtest.config import parse_and_validate
    try:
        return parse_and_validate(config_path)
    except Exception as e:
        _handle_error(e)


def _default_config_path() -> Path:
    return Path("evals/config.yaml")


@click.group()
def main():
    """fieldtest — structured AI eval practice for any project."""
    pass


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@main.command()
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def validate(config_path: Optional[str]):
    """Check config.yaml is valid. Does not run anything."""
    path = Path(config_path) if config_path else _default_config_path()
    config = _load_config(path)

    base_dir    = path.resolve().parent
    fixture_dir = base_dir / "fixtures"

    # Coverage summary
    total_evals   = sum(len(uc.evals) for uc in config.use_cases)
    tag_counts    = {"right": 0, "good": 0, "safe": 0}
    fixture_count = 0
    warnings      = []

    for uc in config.use_cases:
        for ev in uc.evals:
            tag_counts[ev.tag] = tag_counts.get(ev.tag, 0) + 1

            # Warn: variation fixtures paired with reference evals
            if ev.type == "reference":
                for set_val in uc.fixtures.sets.values():
                    if isinstance(set_val, str) and "variations" in set_val:
                        warnings.append(
                            f"  ⚠ use_case '{uc.id}', eval '{ev.id}': "
                            f"reference eval paired with variations set — will always skip"
                        )

            # Warn: rule evals with no registered function
            if ev.type == "rule":
                from fieldtest.judges.registry import get_rule
                rules_path = base_dir / "rules.py"
                if rules_path.exists():
                    from fieldtest.judges.registry import load_rules
                    try:
                        load_rules(rules_path)
                    except Exception:
                        pass
                if get_rule(ev.id) is None:
                    warnings.append(
                        f"  ⚠ use_case '{uc.id}', eval '{ev.id}': "
                        f"type:rule but no @rule('{ev.id}') registered in evals/rules.py"
                    )

        # Count fixtures referenced in sets
        for set_val in uc.fixtures.sets.values():
            if isinstance(set_val, list):
                fixture_count += len(set_val)
                # Warn: fixtures referenced but not on disk
                for fid in set_val:
                    fixture_file = base_dir / uc.fixtures.directory / f"{fid}.yaml"
                    if not fixture_file.exists():
                        warnings.append(
                            f"  ⚠ fixture '{fid}' referenced in '{uc.id}' "
                            f"but not found at {fixture_file}"
                        )

    click.echo(f"✓ config valid: {path}")
    click.echo(f"  {len(config.use_cases)} use case(s), {total_evals} eval(s)")
    click.echo(
        f"  by tag — right: {tag_counts['right']}, "
        f"good: {tag_counts['good']}, safe: {tag_counts['safe']}"
    )
    click.echo(f"  {fixture_count} explicitly listed fixture(s)")

    if warnings:
        click.echo("")
        for w in warnings:
            click.echo(w)


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

@main.command()
@click.argument("set_name", default="full", metavar="[SET]")
@click.option("--set", "set_name_opt", default=None, help="Fixture set to score")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
@click.option("--baseline", "baseline_path", default=None, type=click.Path(),
              help="Path to baseline results JSON for delta")
@click.option("--allow-partial", is_flag=True, default=False,
              help="Warn and skip missing outputs instead of failing")
@click.option("--concurrency", default=5, type=int,
              help="Max parallel judge calls (default: 5; 1 = sequential)")
def score(
    set_name: str,
    set_name_opt: Optional[str],
    config_path: Optional[str],
    baseline_path: Optional[str],
    allow_partial: bool,
    concurrency: int,
):
    """Score outputs for a given fixture set."""
    # --set flag wins over positional if both provided
    effective_set = set_name_opt or set_name

    path    = Path(config_path) if config_path else _default_config_path()
    config  = _load_config(path)

    # Load rules
    from fieldtest.judges.registry import load_rules
    rules_path = path.resolve().parent / "rules.py"
    try:
        load_rules(rules_path)
    except Exception as e:
        _handle_error(e)

    baseline = Path(baseline_path) if baseline_path else None

    from fieldtest.runner import score as _score
    try:
        run_id, rows = _score(
            config=config,
            config_path=path,
            set_name=effective_set,
            baseline_path=baseline,
            allow_partial=allow_partial,
            concurrency=concurrency,
        )
    except Exception as e:
        _handle_error(e)

    # Print report to terminal
    results_dir = path.resolve().parent / "results"
    md_path     = results_dir / f"{run_id}.md"
    if md_path.exists():
        click.echo(md_path.read_text())
    click.echo(f"\nResults written to: {results_dir / run_id}")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

@main.command()
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def history(config_path: Optional[str]):
    """List past result files, newest first."""
    path        = Path(config_path) if config_path else _default_config_path()
    base_dir    = path.resolve().parent
    results_dir = base_dir / "results"

    if not results_dir.exists():
        click.echo("No results found in evals/results/")
        return

    result_files = sorted(results_dir.glob("*.json"), reverse=True)
    if not result_files:
        click.echo("No results found in evals/results/")
        return

    # Header
    header = (
        f"{'RUN ID':<26}  {'TIMESTAMP':<18}  {'SET':<12}  "
        f"{'FIXTURES':<10}  {'RIGHT':<8}  {'GOOD':<8}  {'SAFE':<8}"
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
                for ev_id, stats in uc_stats.get(tag, {}).items():
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
            f"{fixture_count:<10}  {right:<8}  {good:<8}  {safe:<8}"
        )


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

@main.command()
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
        click.echo("No results found.")
        return

    result_files = sorted(results_dir.glob("*.json"), reverse=True)
    if not result_files:
        click.echo("No results found.")
        return

    # Resolve current and baseline
    if run_id:
        current_path = results_dir / f"{run_id}.json"
    else:
        current_path = result_files[0]

    if baseline_id:
        baseline_path = results_dir / f"{baseline_id}.json"
    else:
        # most recent that isn't current
        others = [f for f in result_files if f != current_path]
        baseline_path = others[0] if others else None

    if not current_path.exists():
        click.echo(f"Run not found: {current_path}", err=True)
        sys.exit(1)

    current_data = json.loads(current_path.read_text())
    delta = current_data.get("delta", {})

    click.echo(f"Comparing: {current_path.stem}")
    click.echo(f"Baseline:  {delta.get('baseline_run_id', '—')}")
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


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

@main.command()
@click.option("--outputs", is_flag=True, default=False, help="Clear outputs/ directory")
@click.option("--results", is_flag=True, default=False,
              help="Remove old result files (keeps most recent N)")
@click.option("--keep", default=20, type=int, help="Number of results to keep (default: 20)")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def clean(outputs: bool, results: bool, keep: int, config_path: Optional[str]):
    """Clean up accumulated run artifacts."""
    path        = Path(config_path) if config_path else _default_config_path()
    base_dir    = path.resolve().parent
    outputs_dir = base_dir / "outputs"
    results_dir = base_dir / "results"

    if not outputs and not results:
        # Interactive mode
        to_remove = []
        if outputs_dir.exists():
            output_files = list(outputs_dir.rglob("*.txt"))
            if output_files:
                to_remove.append(f"  outputs/: {len(output_files)} run files")
        if results_dir.exists():
            result_files = sorted(results_dir.glob("*.json"), reverse=True)
            old_results  = result_files[keep:]
            if old_results:
                to_remove.append(f"  results/: {len(old_results)} old result sets (keeping {keep})")

        if not to_remove:
            click.echo("Nothing to clean.")
            return

        click.echo("Would remove:")
        for line in to_remove:
            click.echo(line)
        if click.confirm("Proceed?"):
            outputs = True
            results = True
        else:
            click.echo("Cancelled.")
            return

    if outputs and outputs_dir.exists():
        import shutil
        shutil.rmtree(outputs_dir)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        click.echo(f"✓ outputs/ cleared")

    if results and results_dir.exists():
        result_files = sorted(results_dir.glob("*.json"), reverse=True)
        removed = 0
        for p in result_files[keep:]:
            for ext in [".json", ".md", ".csv"]:
                fp = results_dir / (p.stem + ext)
                if fp.exists():
                    fp.unlink()
            removed += 1
        click.echo(f"✓ results/ pruned — kept {min(keep, len(result_files))}, removed {removed}")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@main.command("init")
@click.option("--dir", "target_dir", default="evals", show_default=True,
              help="Directory to scaffold (default: ./evals)")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite if directory already exists")
def init_cmd(target_dir: str, force: bool):
    """Scaffold evals/ directory structure in current project."""
    from fieldtest.init_template import GITIGNORE_CONTENT, STARTER_CONFIG

    evals_dir = Path(target_dir)

    if evals_dir.exists() and not force:
        click.echo(
            f"Error: '{evals_dir}' already exists. Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    # Create structure
    (evals_dir / "fixtures" / "golden").mkdir(parents=True, exist_ok=True)
    (evals_dir / "fixtures" / "variations").mkdir(parents=True, exist_ok=True)
    (evals_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (evals_dir / "results").mkdir(parents=True, exist_ok=True)

    config_path = evals_dir / "config.yaml"
    if not config_path.exists() or force:
        config_path.write_text(STARTER_CONFIG)

    gitignore_path = evals_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(GITIGNORE_CONTENT)

    click.echo(f"✓ Scaffolded eval structure at {evals_dir}/")
    click.echo(f"  {evals_dir}/config.yaml       — fill this out first")
    click.echo(f"  {evals_dir}/fixtures/golden/  — fixtures with expected outputs")
    click.echo(f"  {evals_dir}/fixtures/variations/ — fixtures without expected outputs")
    click.echo(f"  {evals_dir}/.gitignore        — outputs/ excluded from git")
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  1. Edit {evals_dir}/config.yaml")
    click.echo(f"  2. Add fixtures to {evals_dir}/fixtures/")
    click.echo(f"  3. Run your system → write outputs to {evals_dir}/outputs/")
    click.echo(f"  4. fieldtest score")
