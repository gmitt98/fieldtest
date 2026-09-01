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
import os
import sys

import yaml
from pathlib import Path
from typing import Optional

import click

from fieldtest.cli_common import _default_config_path, _handle_error
from fieldtest.results.writer import (
    CALIBRATION_JSON,
    CALIBRATION_MD,
    DATA_JSON,
    REPORT_HTML,
    REPORT_MD,
    RESULT_SUFFIXES,
)
from fieldtest.results.aggregator import (
    calibration_files_newest_first,
    run_id_from_path,
    find_result_by_run_id,
    result_files_newest_first,
)
from fieldtest.templates import AVAILABLE_TEMPLATES


@click.command()
@click.option("--outputs", is_flag=True, default=False, help="Clear outputs/ directory")
@click.option("--results", is_flag=True, default=False,
              help="Remove old result files (keeps most recent N)")
@click.option("--keep", default=20, type=int, help="Number of results to keep (default: 20)")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def clean(outputs: bool, results: bool, keep: int, config_path: Optional[str]):
    """Clean up accumulated run artifacts."""
    import shutil


    path     = Path(config_path) if config_path else _default_config_path()
    base_dir = path.resolve().parent

    # Refuse to delete anything until this is confirmed to be a fieldtest
    # project. `_default_config_path()` falls back to ./config.yaml, and
    # `config.yaml` beside an `outputs/` directory describes most ML projects
    # ever written — `clean --outputs` in one of those silently deleted the
    # user's checkpoints and exited 0.
    if not path.exists():
        click.echo(
            f"No config found at {path}.\n"
            f"  clean removes files from a fieldtest project; run it from one, "
            f"or pass --config.",
            err=True,
        )
        sys.exit(1)
    # The question is "is this a fieldtest project", not "is this config
    # finished". Full validation answered the second: every shipped template
    # carries `tag: ""` on purpose, so `clean` refused to work in the project
    # `init --template` had just created, and told the user there was nothing
    # here to remove while their outputs sat in it.
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        click.echo(f"{path} is not readable YAML: {e}", err=True)
        sys.exit(1)
    if not isinstance(raw, dict) or "schema_version" not in raw or "use_cases" not in raw:
        click.echo(
            f"{path} is not a fieldtest config — it has no schema_version and "
            f"use_cases. clean removes files from a fieldtest project; run it "
            f"from one, or pass --config.",
            err=True,
        )
        sys.exit(1)

    outputs_dir = base_dir / "outputs"
    results_dir = base_dir / "results"

    def output_victims() -> list:
        """Everything rmtree would take — not just the files we recognise."""
        if not outputs_dir.is_dir():
            return []
        return sorted(p for p in outputs_dir.rglob("*") if p.is_file())

    def result_victims() -> list:
        """The five artifacts of each pruned run, named exactly."""
        if not results_dir.is_dir():
            return []
        runs = result_files_newest_first(results_dir)[keep:]
        out = []
        for p in runs:
            run_id = run_id_from_path(p)
            for suffix in RESULT_SUFFIXES:
                f = results_dir / f"{run_id}{suffix}"
                if f.is_file():
                    out.append(f)
        return out

    def calibration_victims() -> list:
        """
        The two artifacts of each pruned calibration run, named exactly.

        Its own keep pool, never merged into result_victims(): calibration runs
        are cheap to repeat and score runs are the baselines `diff` depends on,
        so a shared pool would let a handful of re-calibrations evict the
        history. The .json is the anchor and the .md only rides along, so an
        orphan .md is never a candidate.
        """
        if not results_dir.is_dir():
            return []
        out = []
        for p in calibration_files_newest_first(results_dir)[keep:]:
            out.append(p)
            md = results_dir / (p.name[: -len(CALIBRATION_JSON)] + CALIBRATION_MD)
            if md.is_file():
                out.append(md)
        return out

    def describe(files: list, root: Path) -> list:
        shown = [f"    {f.relative_to(root.parent)}" for f in files[:8]]
        if len(files) > 8:
            shown.append(f"    … and {len(files) - 8} more")
        return shown

    if not outputs and not results:
        out_files = output_victims()
        res_files = result_victims()
        cal_files = calibration_victims()
        if not out_files and not res_files and not cal_files:
            click.echo("Nothing to clean.")
            return

        click.echo("Would remove:")
        if out_files:
            # Count every file, and say plainly that the directory goes with
            # them. Counting only *.txt announced "1 run files" and then took a
            # hand-written notes.md and a whole subdirectory with it.
            click.echo(f"  outputs/ — {len(out_files)} file(s), and the directory's contents:")
            for line in describe(out_files, outputs_dir):
                click.echo(line)
        if cal_files:
            click.echo(
                f"  results/ — {len(cal_files)} file(s) from old calibration "
                f"runs (keeping {keep}):"
            )
            for line in describe(cal_files, results_dir):
                click.echo(line)

        if res_files:
            click.echo(f"  results/ — {len(res_files)} file(s) from old runs (keeping {keep}):")
            for line in describe(res_files, results_dir):
                click.echo(line)

        if not click.confirm("Proceed?"):
            click.echo("Cancelled.")
            return
        outputs = bool(out_files)
        results = bool(res_files) or bool(cal_files)

    if outputs and outputs_dir.exists():
        if outputs_dir.is_symlink():
            click.echo(
                f"outputs/ is a symlink to {os.readlink(outputs_dir)} — refusing to "
                f"clear it. Remove the link yourself if that is what you meant.",
                err=True,
            )
            sys.exit(1)
        victims = output_victims()
        shutil.rmtree(outputs_dir)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        click.echo(f"✓ outputs/ cleared — {len(victims)} file(s) removed")

    if results and results_dir.exists():
        # Only the five known artifacts per run. Globbing `{run_id}-*` swept up
        # anything a user had named after a run — a write-up beside the results
        # was deleted and never appeared in the count.
        victims = result_victims()
        runs = len({f.name.rsplit("-", 1)[0] for f in victims})
        cal_victims = calibration_victims()
        cal_runs = sum(1 for f in cal_victims if f.name.endswith(CALIBRATION_JSON))
        for f in victims + cal_victims:
            f.unlink()
        kept = len(result_files_newest_first(results_dir))
        cal_kept = len(calibration_files_newest_first(results_dir))
        msg = f"✓ results/ pruned — kept {kept}, removed {runs} run(s)"
        if cal_runs or cal_kept:
            # Counted apart from score runs because they are pruned apart; one
            # number for both would report a keep that neither pool honours.
            msg += (f"; kept {cal_kept}, removed {cal_runs} calibration run(s)")
        click.echo(msg)


def _echo_gitignore(evals_dir: Path, added: list) -> None:
    """Say what actually happened to .gitignore, rather than implying creation."""
    if added == ["(created)"]:
        click.echo(f"  {evals_dir}/.gitignore        — outputs/ excluded from git")
    elif added:
        click.echo(f"  {evals_dir}/.gitignore        — appended "
                   f"{', '.join(added)} (your existing entries kept)")


@click.command("init")
@click.option("--dir", "target_dir", default="evals", show_default=True,
              help="Directory to scaffold (default: ./evals)")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite if directory already exists")
@click.option("--template", type=click.Choice(AVAILABLE_TEMPLATES), default=None,
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

    # A .gitignore is the user's, not ours. --force used to replace it whole,
    # so a file carrying `.env` and `*.pem` became the single line `outputs/`
    # and the next `git add .` staged their secrets. Nothing in the output said
    # so. Missing lines are appended; existing content is never touched.
    gitignore_path = evals_dir / ".gitignore"
    gitignore_added: list = []
    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8")
        have = {ln.strip() for ln in existing.splitlines()}
        missing = [ln for ln in GITIGNORE_CONTENT.splitlines()
                   if ln.strip() and not ln.strip().startswith("#") and ln.strip() not in have]
        if missing:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            gitignore_path.write_text(
                existing + sep + "\n".join(missing) + "\n", encoding="utf-8")
            gitignore_added = missing
    else:
        gitignore_path.write_text(GITIGNORE_CONTENT, encoding="utf-8")
        gitignore_added = ["(created)"]

    if template:
        # No existence check here: --template is a click.Choice over
        # AVAILABLE_TEMPLATES, which is globbed from this same directory, so an
        # unknown name is rejected with exit 2 before this command body runs and
        # nothing is scaffolded at all. The check that used to sit here could
        # never fire, and the comment above it claimed it ran before the
        # directories and .gitignore were written, which it did not.
        # test_every_template_choice_has_a_file_and_the_help_names_them_all
        # pins the invariant that makes it unnecessary.
        template_path = Path(__file__).parent / "templates" / f"{template}.yaml"

        config_path = evals_dir / "config.yaml"
        overwrote = config_path.exists() and force
        shutil.copy2(template_path, config_path)

        click.echo(f"✓ Scaffolded from {template} template at {evals_dir}/")
        if overwrote:
            click.echo(f"  ⚠ replaced the existing {config_path} with the {template} template")
        click.echo(f"  {evals_dir}/config.yaml       — fill in system, domain, tags")
        click.echo(f"  {evals_dir}/fixtures/golden/  — fixtures with expected outputs")
        click.echo(f"  {evals_dir}/fixtures/variations/ — fixtures without expected outputs")
        _echo_gitignore(evals_dir, gitignore_added)
        click.echo("")
        click.echo("Next steps:")
        click.echo(f"  1. Fill in system name and domain in {evals_dir}/config.yaml")
        click.echo("  2. Tag each eval: right, good, or safe")
        click.echo(f"  3. Add fixtures to {evals_dir}/fixtures/")
        click.echo(f"  4. Run your system → write outputs to {evals_dir}/outputs/")
        click.echo("  5. fieldtest score")
    else:
        config_path = evals_dir / "config.yaml"
        # --force overwrites an existing config in place, and the output was
        # byte-identical to scaffolding an empty directory — nothing named the
        # file that had just been replaced.
        overwrote = config_path.exists() and force
        if not config_path.exists() or force:
            config_path.write_text(STARTER_CONFIG, encoding="utf-8")

        click.echo(f"✓ Scaffolded eval structure at {evals_dir}/")
        if overwrote:
            click.echo(f"  ⚠ replaced the existing {config_path} with the starter template")
        click.echo(f"  {evals_dir}/config.yaml       — fill this out first")
        click.echo(f"  {evals_dir}/fixtures/golden/  — fixtures with expected outputs")
        click.echo(f"  {evals_dir}/fixtures/variations/ — fixtures without expected outputs")
        _echo_gitignore(evals_dir, gitignore_added)
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
# default=None so this goes through _default_config_path(), which falls back to
# ./config.yaml when the caller is inside evals/. Hardcoding the default here
# meant `score` worked from that directory and `view` did not.
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
def view_cmd(run_id: Optional[str], config_path: Optional[str]):
    """Open the HTML eval report in the default browser."""
    import webbrowser

    # Path(None) raises. The option defaults to None precisely so the fallback
    # runs, and the call was never added — so bare `fieldtest view`, the command
    # the demo's own last line tells every new user to run, ended in a
    # TypeError and a "please file a bug". Every test written for this command
    # passed --config, which is why none of them saw it.
    path        = Path(config_path) if config_path else _default_config_path()
    base_dir    = path.resolve().parent
    results_dir = base_dir / "results"

    if run_id:
        html_path = results_dir / f"{run_id}{REPORT_HTML}"
        if not html_path.exists():
            # Filename and run id are not always the same string — the bundled
            # demo ships demo-offline-*.html whose run_id is a timestamp — so an
            # id `history` printed was rejected here.
            data = find_result_by_run_id(results_dir, run_id)
            if data is not None:
                candidate = data.with_name(
                    run_id_from_path(data) + REPORT_HTML)
                if candidate.exists():
                    html_path = candidate
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
        html_files = sorted(results_dir.glob(f"*{REPORT_HTML}"), key=lambda p: p.stat().st_mtime)
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
    # is_symlink too: exists() is False for a dangling link, so the guard
    # fell through and copytree raised FileExistsError as a traceback.
    if dest.exists() or dest.is_symlink():
        click.echo(
            f"Error: '{dest}' already exists.\n"
            f"  Use --dir to choose a different directory, or remove '{dest}' first.",
            err=True,
        )
        sys.exit(1)

    # Live mode needs a key (except extraction, which uses rules only).
    # Check BEFORE copying anything, so a failed run leaves nothing behind —
    # otherwise the suggested retry with --offline hits the dest-exists guard.
    if not offline and example != "extraction":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            click.echo(
                "Error: ANTHROPIC_API_KEY not set.\n"
                "  Set it with: export ANTHROPIC_API_KEY=sk-...\n"
                "  Or use --offline to view pre-scored results without an API key.",
                err=True,
            )
            sys.exit(1)

    # Copy demo source tree, excluding results/ (handled separately) and
    # install-time byte-code clutter (__pycache__ is compiled into
    # site-packages by pip and must not land in the user's project).
    shutil.copytree(
        demo_source, dest,
        ignore=shutil.ignore_patterns("results", "__pycache__", ".DS_Store"),
    )

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
        json_files = list(dest_results.glob(f"*{DATA_JSON}"))
        if json_files:
            try:
                from fieldtest.config import parse_and_validate
                from fieldtest.results.html import write_html
                run_data = json.loads(json_files[0].read_text(encoding="utf-8"))
                config   = parse_and_validate(evals_dir / "config.yaml")
                run_id   = run_id_from_path(json_files[0])
                write_html(run_data, config, dest_results / f"{run_id}{REPORT_HTML}")
            except Exception:
                pass  # HTML generation is best-effort; don't fail offline mode

        # Print pre-rendered markdown report if available
        md_files = list(dest_results.glob(f"*{REPORT_MD}"))
        if md_files:
            click.echo(md_files[0].read_text(encoding="utf-8"))
        else:
            click.echo("Offline results loaded. No markdown report found.")

        # Both commands resolve config from the current directory, so naming
        # them without the cd sent every reader of this line into "No results
        # found" — or, before the config default was fixed, into a traceback.
        click.echo(f"\nFiles saved to {dest}/. To explore:")
        click.echo(f"  cd {dest}")
        click.echo("  fieldtest view            # open the HTML report")
        # This demo's evals are judged by an LLM. Suggesting a bare re-score
        # after `--offline` — which the user chose because they have no key —
        # points at a command that now correctly exits 1.
        needs_key = "  (needs ANTHROPIC_API_KEY)" if offline else ""
        click.echo(
            f"  fieldtest score           # re-score after editing evals/outputs/{needs_key}")
        return

    # Live mode — the API key was already checked before anything was copied.

    # Run fieldtest score from the demo directory
    click.echo(f"Running fieldtest score in {dest}/evals/ ...")
    try:
        # sys.executable -m, not the console script off PATH. Resolving
        # "fieldtest" by name fails for a working install invoked as
        # `python -m fieldtest.cli`, or from a venv whose bin/ is not on PATH —
        # and it failed here AFTER copytree, so the half-created directory then
        # defeated the `--offline` retry this command suggests.
        result = subprocess.run(
            [sys.executable, "-m", "fieldtest.cli", "score",
             "--config", str(evals_dir / "config.yaml")],
            check=False,
        )
        if result.returncode != 0:
            # The copy succeeded and the scoring did not, so dest stays — but
            # the obvious retry then hits the dest-exists guard. Say what to do
            # from here rather than leaving the user to discover that.
            # The invocation this install actually has. The subprocess above
            # uses `sys.executable -m` precisely because the console script is
            # not always on PATH; suggesting bare `fieldtest` to the user it
            # was written for hands them two commands that do not run.
            import shutil
            ft = ("fieldtest" if shutil.which("fieldtest")
                  else f"{sys.executable} -m fieldtest.cli")
            click.echo(
                f"fieldtest score failed — see the output above.\n"
                f"  {dest}/ is already set up, so retry in place with:\n"
                f"    cd {dest} && {ft} score\n"
                f"  or, for the pre-scored results with no key:\n"
                f"    rm -rf {dest} && {ft} demo --example {example} "
                f"--offline --dir {dest}",
                err=True,
            )
            sys.exit(1)
    except Exception as e:
        _handle_error(e)

    click.echo(f"\nFiles saved to {dest}/. To explore:")
    click.echo(f"  cd {dest}")
    click.echo("  fieldtest view            # open the HTML report")
    click.echo("  fieldtest score           # re-score after editing evals/outputs/")


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
            for line in readme.read_text(encoding="utf-8").splitlines():
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
    if target.exists() and not target.is_dir():
        # exists() is True for a file, and iterdir() then raises
        # NotADirectoryError straight through click — a typo in --dest produced
        # a stack trace instead of a sentence.
        click.echo(
            f"{target} is a file, not a directory. --dest names a directory to "
            f"copy the dataset into.",
            err=True,
        )
        sys.exit(1)

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
    todos = Path(target, "config.yaml").read_text(encoding="utf-8").count("# TODO")
    plural = "is" if todos == 1 else "are"
    click.echo(f"  {target}/config.yaml your evals — {todos} {plural} TODO")

    # `score` resolves evals/config.yaml (or ./config.yaml from inside evals/).
    # With --dest naming anywhere else, the bare command printed here failed
    # with "Config not found: evals/config.yaml" — the next step the tool
    # itself just told the user to take.
    target_config = Path(target, "config.yaml")
    if target_config == Path("evals", "config.yaml"):
        run_cmd = "fieldtest score --set full"
    else:
        run_cmd = f"fieldtest score --config {target_config} --set full"
    click.echo(f"\nRun it now (no API key needed):  {run_cmd}")
