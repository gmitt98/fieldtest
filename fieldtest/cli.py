"""
fieldtest/cli.py

Click entry point.

Holds the command group and the commands that run a scoring pass — validate,
score, calibrate. The rest live in cli_reports.py and cli_project.py and are
registered at the bottom of this file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import sys

import click

from fieldtest.config import resolve_set
from fieldtest.errors import ConfigError
from fieldtest.fixtures import find_fixture_path
from fieldtest.cli_common import (
    _default_config_path,
    _handle_error,
    _load_config,
    _provider_report,
)
from fieldtest.cli_project import clean, dataset, demo_cmd, init_cmd, view_cmd
from fieldtest.cli_reports import diff, history




class _HelpFriendlyGroup(click.Group):
    """
    Accepts the two help forms people type out of habit.

    `fieldtest --help calibrate` printed the top-level help and dropped the
    command name without saying so — the worst kind of wrong answer, because it
    looks like an answer. git accepts that form, so people type it.
    """

    def parse_args(self, ctx, args):
        if "--help" in args:
            rest = [a for a in args if a != "--help"]
            if len(rest) == 1:
                if rest[0] in self.commands:
                    args = [rest[0], "--help"]
                else:
                    # Same reasoning: printing the general help here would
                    # answer a question the user did not ask.
                    known = ", ".join(sorted(c for c in self.commands if c != "help"))
                    click.echo(
                        f"No such command '{rest[0]}'. Available: {known}", err=True
                    )
                    ctx.exit(2)
        return super().parse_args(ctx, args)


def _version() -> str:
    """Installed version, or a marker when running from an uninstalled tree."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("fieldtest")
    except PackageNotFoundError:      # running from a source checkout
        return "unknown (not installed)"


@click.group(cls=_HelpFriendlyGroup)
@click.version_option(version=_version(), prog_name="fieldtest")
def main():
    """fieldtest — structured AI eval practice for any project."""
    pass


@main.command("help")
@click.argument("command", required=False)
@click.pass_context
def help_cmd(ctx, command: Optional[str]):
    """Show help for a command: fieldtest help calibrate"""
    if command is None:
        click.echo(main.get_help(ctx.parent or ctx))
        return
    cmd = main.commands.get(command)
    if cmd is None:
        known = ", ".join(sorted(c for c in main.commands if c != "help"))
        click.echo(f"No such command '{command}'. Available: {known}", err=True)
        sys.exit(2)
    # parent=None: with this command's context as the parent, click prefixes
    # the usage line with "fieldtest help [COMMAND]". The program name comes
    # from the actual invocation rather than a hardcoded "fieldtest", so the
    # usage line stays true under an alias or `python -m`.
    prog = ctx.find_root().info_name or "fieldtest"
    with click.Context(cmd, info_name=f"{prog} {command}") as sub:
        click.echo(cmd.get_help(sub))




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

    # The same floor score() enforces. Without it, validate blessed a
    # `runs: 0` / `judge_runs: 0` config that score immediately refused —
    # after the user had already paid for generation.
    from fieldtest.config import validate_run_counts
    try:
        validate_run_counts(config)
    except ConfigError as e:
        _handle_error(e)

    base_dir    = path.resolve().parent

    # Coverage summary
    total_evals   = sum(len(uc.evals) for uc in config.use_cases)
    tag_counts    = {"right": 0, "good": 0, "safe": 0}
    listed_fixtures: set = set()
    rules_errors_seen: set = set()
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
                rules_error = None
                if rules_path.exists():
                    from fieldtest.judges.registry import load_rules
                    try:
                        load_rules(rules_path)
                    except Exception as e:
                        # Swallowing this made validate report a false cause: it
                        # said the rule was not registered when the decorators
                        # were there and the file had failed to import, sending
                        # the user to add something already present. score fails
                        # on the same tree with the true message.
                        rules_error = str(e).splitlines()[0]
                if rules_error is not None:
                    if rules_error not in rules_errors_seen:
                        rules_errors_seen.add(rules_error)
                        warnings.append(f"  ⚠ {rules_path} did not import: {rules_error}")
                elif get_rule(ev.id) is None:
                    warnings.append(
                        f"  ⚠ use_case '{uc.id}', eval '{ev.id}': "
                        f"type:rule but no @rule('{ev.id}') registered in evals/rules.py"
                    )

        # Count fixtures referenced in sets. Distinct ids, not set entries: a
        # fixture named by both `full` and `smoke` is one fixture, and summing
        # set lengths reported 4 for a three-fixture dataset.
        for set_val in uc.fixtures.sets.values():
            if isinstance(set_val, list):
                listed_fixtures.update(set_val)
                # Warn: fixtures referenced but not on disk
                for fid in set_val:
                    # find_fixture_path reports both "missing" and "ambiguous",
                    # so validate says which rather than only that it is absent.
                    try:
                        find_fixture_path(base_dir / uc.fixtures.directory, fid)
                    except ConfigError as e:
                        warnings.append(
                            f"  ⚠ fixture '{fid}' referenced in '{uc.id}': "
                            f"{str(e).splitlines()[0]}"
                        )

    # Every declared set must resolve. This used to live inside the cost
    # projection, which is skipped for a use case with no llm evals — so a
    # malformed set value in a rules-only project passed validate and then
    # failed the moment `score --set <that>` ran.
    for uc in config.use_cases:
        for set_name in uc.fixtures.sets:
            try:
                resolve_set(set_name, uc, base_dir)
            except ConfigError as e:
                warnings.append(
                    f"  ⚠ set '{set_name}' in '{uc.id}' cannot be resolved — "
                    f"`--set {set_name}` will fail: {str(e).splitlines()[-1]}"
                )

    # A set declared in one use case and not another cannot be scored at all:
    # resolve_set raises for the use case that lacks it. The config looks fine
    # until you spend the command.
    if len(config.use_cases) > 1:
        by_uc = {uc.id: set(uc.fixtures.sets) for uc in config.use_cases}
        everywhere = set.intersection(*by_uc.values()) if by_uc else set()
        for uc_id, names in by_uc.items():
            for missing in sorted(names - everywhere):
                absent = sorted(o for o, s in by_uc.items() if missing not in s)
                warnings.append(
                    f"  ⚠ set '{missing}' is declared in '{uc_id}' but not in "
                    f"{', '.join(repr(a) for a in absent)} — "
                    f"`--set {missing}` will fail"
                )

    click.echo(f"✓ config valid: {path}")
    click.echo(f"  {len(config.use_cases)} use case(s), {total_evals} eval(s)")
    click.echo(
        f"  by tag — right: {tag_counts['right']}, "
        f"good: {tag_counts['good']}, safe: {tag_counts['safe']}"
    )
    click.echo(f"  {len(listed_fixtures)} explicitly listed fixture(s)")

    # Which providers this config reaches, and whether the credential each one
    # names is present. Before the run, not twenty errored rows into it.
    for line in _provider_report(config):
        click.echo(line)

    from fieldtest.config import summarize_file_inputs
    resolved = summarize_file_inputs(config, base_dir)
    if resolved:
        total = sum(len(keys) for keys in resolved.values())
        click.echo(
            f"  {total} file input(s) resolved across "
            f"{len(resolved)} fixture(s) — the judge sees the document, not the path"
        )

    # Cost is multiplicative: runs × judge_runs × llm evals × fixtures. Say it
    # before the bill, not after — judge_runs: 3 is a 3x charge.
    from fieldtest.config import resolve_judge_runs, resolve_runs
    from fieldtest.config import resolve_set as _resolve_set

    # Project the largest declared set rather than assuming "full" exists. A
    # project with only smoke/regression sets is exactly the one that needs the
    # number, and silently printing nothing would defeat the purpose.
    projected: dict[str, int] = {}
    judge_runs_used = 1
    for uc in config.use_cases:
        llm_evals = sum(1 for ev in uc.evals if ev.type == "llm")
        if not llm_evals:
            continue
        judge_runs = resolve_judge_runs(config, uc)
        judge_runs_used = max(judge_runs_used, judge_runs)
        runs = resolve_runs(config, uc)
        for set_name in uc.fixtures.sets:
            try:
                uc_fixtures = len(_resolve_set(set_name, uc, base_dir))
            except Exception:
                # Cost only. Whether the set resolves at all is checked for
                # every use case above, not just those with llm evals.
                continue
            projected[set_name] = (
                projected.get(set_name, 0) + uc_fixtures * runs * judge_runs * llm_evals
            )

    # Guard on the count. projected is a {set: count} dict, and a dict whose only
    # value is 0 is still truthy — which printed "≈ 0 judge call(s)" on a fresh
    # scaffold, before the user has added a single fixture.
    if projected and max(projected.values()) > 0:
        largest = max(projected, key=lambda name: projected[name])
        detail = f"  ≈ {projected[largest]} judge call(s) for the '{largest}' set"
        if judge_runs_used > 1:
            detail += f" (judge_runs: {judge_runs_used})"
        click.echo(detail)

    # Ground truth: how thin is it, and does it line up with the config?
    from fieldtest.config import validate_fixture_labels

    label_errors, label_coverage = validate_fixture_labels(config, base_dir)
    warnings.extend(label_errors)

    if label_coverage:
        click.echo("")
        click.echo("  human labels:")
        for eval_id in sorted(label_coverage):
            click.echo(f"    {eval_id}: {label_coverage[eval_id]} labeled run(s)")

    if warnings:
        click.echo("")
        for w in warnings:
            click.echo(w)


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------

@main.command()
@click.argument("set_name", default="full", metavar="[SET]")
# --set as well as the positional, because `score` accepts both and someone who
# learned it there should not meet "No such option: --set" here.
@click.option("--set", "set_name_opt", default=None, help="Fixture set to calibrate")
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Path to config.yaml (default: evals/config.yaml)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the projected call count and exit without calling anything")
@click.option("--concurrency", default=5, type=int,
              help="Max parallel judge calls (default: 5)")
def calibrate(set_name: str, set_name_opt: Optional[str], config_path: Optional[str],
              dry_run: bool, concurrency: int):
    """Run a panel of judges over the same outputs and report how much they agree."""
    set_name = set_name_opt or set_name
    from fieldtest.calibrate import (
        project_calls,
        require_panel,
        run_calibration,
        write_calibration,
    )

    path = Path(config_path) if config_path else _default_config_path()
    config = _load_config(path)

    try:
        panel = require_panel(config)
        projection = project_calls(config, path.resolve().parent, set_name)
    except Exception as e:
        _handle_error(e)
        return

    click.echo(f"Panel: {len(panel)} judge(s)")
    for judge in panel:
        click.echo(f"  {judge.provider}/{judge.model}")
    click.echo(
        f"Projected: {projection['total']} judge call(s) "
        f"({projection['per_judge']} per judge) — "
        f"{projection['multiplier']}× a normal run."
    )

    if dry_run:
        click.echo("")
        click.echo("Dry run — nothing called.")
        return

    click.echo("")
    try:
        run_id, data = run_calibration(
            config=config,
            config_path=path,
            set_name=set_name,
            concurrency=concurrency,
            progress=lambda label: click.echo(f"  judging with {label}…"),
        )
    except Exception as e:
        _handle_error(e)
        return

    results_dir = path.resolve().parent / "results"
    write_calibration(data, results_dir, run_id)

    click.echo("")
    ranked = data.get("ranked_by_disagreement", [])
    if ranked:
        click.echo("Most contested evals:")
        for eval_id in ranked[:3]:
            score_ = data["evals"][eval_id]["disagreement"]
            click.echo(f"  {eval_id} — {round(score_ * 100, 1)}% disagreement")
    click.echo("")
    click.echo(f"Calibration written to: {results_dir / f'{run_id}-calibration.md'}")

    # A panel where every call errored measured nothing. The report says so in
    # its errors column, but the exit code said success, so a CI job running
    # calibrate went green on a run that produced no comparison at all. This is
    # not the "high failure rate" case the README declines to fail on — there is
    # no rate here, only a judge that never answered.
    panel = data.get("panel", [])
    calls = sum(j.get("calls", 0) for j in panel)
    errors = sum(j.get("errors", 0) for j in panel)
    if calls and errors == calls:
        click.echo("")
        click.echo(
            f"All {calls} judge call(s) failed — nothing was measured. "
            f"Check the provider credential and the models named in "
            f"calibration.panel.",
            err=True,
        )
        sys.exit(1)


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
        click.echo(md_path.read_text(encoding="utf-8"))
    click.echo(f"\nResults written to: {results_dir / run_id}")

    # Same rule as calibrate: a run where every judge call errored measured
    # nothing, and exiting 0 told CI it had. This is not the "high failure rate"
    # case the README declines to fail on — there is no rate, only a judge that
    # never answered. A run with SOME errors still exits 0 and reports them.
    # Scoped to llm rows. The gate asked "did anything score at all", so one
    # passing regex disarmed it while every call to the judge failed — and the
    # README promises the opposite. A deterministic-only project has no judge
    # calls to fail and is never caught by this.
    judged  = [r for r in rows if r.type == "llm"]
    scored  = [r for r in judged if r.passed is not None or r.score is not None]
    errored = [r for r in judged if r.error]
    if errored and not scored:
        click.echo("")
        click.echo(
            f"All {len(errored)} judge call(s) failed — nothing was scored. "
            f"Check the provider credential and defaults.model.",
            err=True,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------



# Registered rather than decorated in place: defining them here would put every
# command back in one file, and decorating them in their own modules would make
# those modules import this one, which imports them.
# Registration must precede the __main__ guard: `python -m fieldtest.cli` runs
# this module as __main__, and main() never returns — with the loop below the
# guard, `python -m fieldtest.cli demo` reported "No such command 'demo'".
for _command in (history, diff, clean, init_cmd, view_cmd, demo_cmd, dataset):
    main.add_command(_command)


if __name__ == "__main__":
    main()
