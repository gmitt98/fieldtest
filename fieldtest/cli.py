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
              help="Max parallel judge calls (default: 5; 1 = sequential with per-judge output)")
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

    # verbose = per-judge output; only useful when sequential (concurrency 1)
    verbose = concurrency == 1

    from fieldtest.runner import score as _score
    try:
        run_id, rows = _score(
            config=config,
            config_path=path,
            set_name=effective_set,
            baseline_path=baseline,
            allow_partial=allow_partial,
            concurrency=concurrency,
            verbose=verbose,
        )
    except Exception as e:
        _handle_error(e)

    # Print report to terminal
    results_dir = path.resolve().parent / "results"
    md_path     = results_dir / f"{run_id}-report.md"
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
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results, or\n"
            f"  'fieldtest init' if you haven't set up a project yet."
        )
        return

    result_files = sorted(results_dir.glob("*-data.json"), reverse=True)
    if not result_files:
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results."
        )
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
        # Interactive mode — show only what actually needs cleaning,
        # then set flags based on what was shown (not unconditionally).
        to_remove = []
        output_files: list = []
        old_results: list  = []

        if outputs_dir.exists():
            output_files = list(outputs_dir.rglob("*.txt"))
            if output_files:
                to_remove.append(f"  outputs/: {len(output_files)} run files")

        if results_dir.exists():
            result_files = sorted(results_dir.glob("*-data.json"), reverse=True)
            old_results  = result_files[keep:]
            if old_results:
                to_remove.append(
                    f"  results/: {len(old_results)} old result sets (keeping {keep})"
                )

        if not to_remove:
            click.echo("Nothing to clean.")
            return

        click.echo("Would remove:")
        for line in to_remove:
            click.echo(line)
        if click.confirm("Proceed?"):
            # Only act on what was described in the prompt above
            outputs = bool(output_files)
            results = bool(old_results)
        else:
            click.echo("Cancelled.")
            return

    if outputs and outputs_dir.exists():
        import shutil
        shutil.rmtree(outputs_dir)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        click.echo("✓ outputs/ cleared")

    if results and results_dir.exists():
        result_files = sorted(results_dir.glob("*-data.json"), reverse=True)
        removed = 0
        for p in result_files[keep:]:
            run_id = p.stem.removesuffix("-data")
            for fp in results_dir.glob(f"{run_id}-*"):
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
@click.option("--template", type=click.Choice(["chatbot", "rag", "email"]), default=None,
              help="Start from a curated template: chatbot, rag, or email")
def init_cmd(target_dir: str, force: bool, template: Optional[str]):
    """Scaffold evals/ directory structure in current project.

    Templates provide curated judge prompts for common AI product types.
    Tags are left blank — you decide what's right, good, or safe.

    \b
    Examples:
      fieldtest init                      # blank starter config
      fieldtest init --template chatbot   # conversational AI
      fieldtest init --template rag       # document Q&A / RAG
      fieldtest init --template email     # email responder
    """
    import shutil
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

    gitignore_path = evals_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(GITIGNORE_CONTENT)

    if template:
        # Load curated template config from templates/ directory
        template_path = Path(__file__).parent / "templates" / f"{template}.yaml"
        if not template_path.exists():
            click.echo(f"Error: template '{template}' not found", err=True)
            sys.exit(1)

        shutil.copy2(template_path, evals_dir / "config.yaml")

        click.echo(f"✓ Scaffolded from {template} template at {evals_dir}/")
        click.echo(f"  {evals_dir}/config.yaml       — fill in system, domain, tags")
        click.echo(f"  {evals_dir}/fixtures/golden/  — fixtures with expected outputs")
        click.echo(f"  {evals_dir}/fixtures/variations/ — fixtures without expected outputs")
        click.echo(f"  {evals_dir}/.gitignore        — outputs/ excluded from git")
        click.echo("")
        click.echo("Next steps:")
        click.echo(f"  1. Fill in system name and domain in {evals_dir}/config.yaml")
        click.echo(f"  2. Tag each eval: right, good, or safe")
        click.echo(f"  3. Add fixtures to {evals_dir}/fixtures/")
        click.echo(f"  4. Run your system → write outputs to {evals_dir}/outputs/")
        click.echo(f"  5. fieldtest score")
    else:
        config_path = evals_dir / "config.yaml"
        if not config_path.exists() or force:
            config_path.write_text(STARTER_CONFIG)

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
        click.echo("")
        click.echo("Or start from a template: fieldtest init --template chatbot")


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

@main.command("view")
@click.argument("run_id", required=False, default=None)
@click.option("--config", "config_path", default="evals/config.yaml", show_default=True,
              help="Path to config.yaml (used to locate results dir)")
def view_cmd(run_id: Optional[str], config_path: str):
    """Open the HTML eval report in the default browser."""
    import webbrowser

    base_dir    = Path(config_path).resolve().parent
    results_dir = base_dir / "results"

    if run_id:
        html_path = results_dir / f"{run_id}-report.html"
        if not html_path.exists():
            click.echo(f"HTML report not found: {html_path}", err=True)
            sys.exit(1)
    else:
        if not results_dir.exists():
            click.echo(
                f"No results found at {results_dir}.\n"
                f"  Run 'fieldtest score' to generate results.",
                err=True,
            )
            sys.exit(1)
        html_files = sorted(results_dir.glob("*-report.html"), key=lambda p: p.stat().st_mtime)
        if not html_files:
            click.echo(
                f"No HTML reports found at {results_dir}.\n"
                f"  Run 'fieldtest score' to generate a report.",
                err=True,
            )
            sys.exit(1)
        html_path = html_files[-1]  # most recent by mtime

    webbrowser.open(str(html_path.resolve()))
    click.echo(f"Opening: {html_path}")


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------

@main.command("demo")
@click.option("--example", type=click.Choice(["email", "rag", "extraction"]), default="email",
              show_default=True, help="Which demo example to run")
@click.option("--offline", is_flag=True, default=False,
              help="Use pre-scored results — no API key required")
@click.option("--dir", "target_dir", default="fieldtest-demo", show_default=True,
              help="Directory to create the demo in")
def demo_cmd(example: str, offline: bool, target_dir: str):
    """Two steps from install to a live eval report. Requires ANTHROPIC_API_KEY."""
    import os
    import shutil
    import subprocess

    demo_source = Path(__file__).parent / "demo" / example
    if not demo_source.exists():
        click.echo(f"Error: demo '{example}' not found at {demo_source}", err=True)
        sys.exit(1)

    dest = Path(target_dir)
    if dest.exists():
        click.echo(
            f"Error: '{dest}' already exists.\n"
            f"  Use --dir to choose a different directory, or remove '{dest}' first.",
            err=True,
        )
        sys.exit(1)

    # Copy demo source tree (excluding results/ — we handle that separately)
    def _ignore_results(src, names):
        return ["results"] if "results" in names else []

    shutil.copytree(demo_source, dest, ignore=_ignore_results)

    # Rename demo's evals-style dirs to expected layout under dest/evals/
    # The demo source ships as: config.yaml, rules.py, fixtures/, outputs/
    # fieldtest score expects:  evals/config.yaml, evals/fixtures/, evals/outputs/
    evals_dir = dest / "evals"
    evals_dir.mkdir(exist_ok=True)
    for item in ["config.yaml", "rules.py", "fixtures", "outputs"]:
        src_item = dest / item
        if src_item.exists():
            src_item.rename(evals_dir / item)

    (evals_dir / "results").mkdir(exist_ok=True)

    if offline:
        # Copy pre-scored results into evals/results/
        src_results = demo_source / "results"
        dest_results = evals_dir / "results"
        if src_results.exists():
            for f in src_results.iterdir():
                shutil.copy2(f, dest_results / f.name)

        # Generate HTML from the bundled JSON (so fieldtest view works offline too)
        json_files = list(dest_results.glob("*-data.json"))
        if json_files:
            try:
                from fieldtest.config import parse_and_validate
                from fieldtest.results.html import write_html
                run_data = json.loads(json_files[0].read_text())
                config   = parse_and_validate(evals_dir / "config.yaml")
                run_id   = json_files[0].name.replace("-data.json", "")
                write_html(run_data, config, dest_results / f"{run_id}-report.html")
            except Exception:
                pass  # HTML generation is best-effort; don't fail offline mode

        # Print pre-rendered markdown report if available
        md_files = list(dest_results.glob("*-report.md"))
        if md_files:
            click.echo(md_files[0].read_text())
        else:
            click.echo("Offline results loaded. No markdown report found.")

        click.echo(f"\nFiles saved to {dest}/ — edit evals/outputs/ to experiment, then run fieldtest score")
        click.echo(f"Run 'fieldtest view' to open the HTML report in your browser.")
        return

    # Live mode — check API key (not required for extraction which uses rules only)
    if example != "extraction":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            click.echo(
                "Error: ANTHROPIC_API_KEY not set.\n"
                "  Set it with: export ANTHROPIC_API_KEY=sk-...\n"
                "  Or use --offline to view pre-scored results without an API key.",
                err=True,
            )
            sys.exit(1)

    # Run fieldtest score from the demo directory
    click.echo(f"Running fieldtest score in {dest}/evals/ ...")
    try:
        result = subprocess.run(
            ["fieldtest", "score", "--config", str(evals_dir / "config.yaml")],
            check=False,
        )
        if result.returncode != 0:
            click.echo("fieldtest score failed — check output above.", err=True)
            sys.exit(1)
    except Exception as e:
        _handle_error(e)

    click.echo(f"\nFiles saved to {dest}/ — edit evals/outputs/ to experiment, then run fieldtest score")


if __name__ == "__main__":
    main()
