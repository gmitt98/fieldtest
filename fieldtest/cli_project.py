"""
fieldtest/cli_project.py

Commands that act on the project directory rather than on results:
clean, init, view and demo.

Split out of cli.py, which had grown past 900 lines. Commands are plain
click.Command objects registered by cli.py with add_command(), rather than
decorated with @main.command() here, so these modules do not import cli.py and
no cycle exists.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from fieldtest.cli_common import _default_config_path, _handle_error


@click.command()
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

@click.command("init")
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
        click.echo("  2. Tag each eval: right, good, or safe")
        click.echo(f"  3. Add fixtures to {evals_dir}/fixtures/")
        click.echo(f"  4. Run your system → write outputs to {evals_dir}/outputs/")
        click.echo("  5. fieldtest score")
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
        click.echo("  4. fieldtest score")
        click.echo("")
        click.echo("Or start from a template: fieldtest init --template chatbot")

@click.command("view")
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

@click.command("demo")
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
        click.echo("Run 'fieldtest view' to open the HTML report in your browser.")
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


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------

def _datasets_root() -> Path:
    return Path(__file__).resolve().parent / "datasets"


def available_datasets() -> list[str]:
    root = _datasets_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / "config.yaml").is_file())


@click.group()
def dataset():
    """Sample datasets to write evals against."""
    pass


@dataset.command("list")
def dataset_list():
    """List the datasets bundled with fieldtest."""
    names = available_datasets()
    if not names:
        click.echo("No datasets bundled with this install.")
        return
    click.echo("Bundled datasets:")
    for name in names:
        readme = _datasets_root() / name / "README.md"
        summary = ""
        if readme.is_file():
            for line in readme.read_text().splitlines():
                if line.strip() and not line.startswith("#"):
                    summary = f" — {line.strip()}"
                    break
        click.echo(f"  {name}{summary}")
    click.echo("\nCopy one into this project with:  fieldtest dataset use <name>")


@dataset.command("use")
@click.argument("name")
@click.option("--dest", default="evals", type=click.Path(),
              help="Where to copy it (default: evals/)")
@click.option("--force", is_flag=True, help="Overwrite an existing destination")
def dataset_use(name: str, dest: str, force: bool):
    """Copy a bundled dataset into this project so you can edit it."""
    import shutil

    src = _datasets_root() / name
    if not (src / "config.yaml").is_file():
        available = ", ".join(available_datasets()) or "none"
        click.echo(f"Unknown dataset '{name}'. Available: {available}", err=True)
        sys.exit(1)

    target = Path(dest)
    if target.exists() and any(target.iterdir()) and not force:
        # Copying over someone's evals is not recoverable, and a dataset is
        # exactly what a new project directory looks like.
        click.echo(
            f"{target}/ already exists and is not empty.\n"
            f"  Use --dest to copy elsewhere, or --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    # Results belong to whoever runs it, not to the shipped copy.
    shutil.copytree(
        src, target, dirs_exist_ok=force,
        ignore=shutil.ignore_patterns("results", "__pycache__", ".DS_Store"),
    )
    click.echo(f"Copied '{name}' to {target}/")
    click.echo(f"  {target}/README.md   what is in it and what to write")
    click.echo(f"  {target}/config.yaml your evals — three are TODO")
    click.echo("\nRun it now (no API key needed):  fieldtest score --set full")
