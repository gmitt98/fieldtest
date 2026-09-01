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
import sys
from pathlib import Path
from typing import Optional

import click

from fieldtest.cli_common import _default_config_path
from fieldtest.results.aggregator import (
    run_id_from_path,
    find_result_by_run_id,
    result_files_newest_first,
)


def _summary_eval_keys(summary: dict) -> set:
    """
    Every eval a summary carries, as "use_case/eval_id".

    Qualified by use case because eval ids are unique within a use case and not
    across them — the same reason build_delta's entries carry use_case.
    """
    keys = set()
    for uc_id, tags in (summary or {}).items():
        if not isinstance(tags, dict):
            continue
        for evals in tags.values():
            if isinstance(evals, dict):
                keys.update(f"{uc_id}/{eval_id}" for eval_id in evals)
    return keys


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

    result_files = result_files_newest_first(results_dir)

    # Runs written before the -data.json naming are invisible to that glob.
    # A long-lived project can have most of its history in the old layout —
    # one had 24 of 32 — and listing the rest without a word reads as
    # "that is all there is".
    # Calibration runs write {run_id}-calibration.json beside the results.
    # They are not old-format results — fieldtest wrote them in the current
    # format, moments ago — and calling them files that "predate the current
    # naming" sent people looking for a migration that does not exist.
    calibrations = sorted(results_dir.glob("*-calibration.json"))
    unreadable: list = []
    legacy = [
        f for f in results_dir.glob("*.json")
        if not f.name.endswith("-data.json")
        and not f.name.endswith("-calibration.json")
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
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            # history's own rule is that anything present but unlisted gets
            # counted and named — "listing the rest without a word reads as
            # 'that is all there is'". A truncated -data.json from an
            # interrupted score was the one case it stayed silent about.
            unreadable.append(p.name)
            continue

        run_id        = data.get("run_id") or run_id_from_path(p)
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
            """
            Pass rate for the tag, pooled over every output in the run.

            One figure per tag for the whole run, across all use cases. The
            markdown report emits a Tag Health table per use case instead, so
            this is not "the same number under the same heading" — with two use
            cases passing 2 of 4 and 0 of 2, the report shows 50% and 0% and
            this shows 33%. Whole and parts, both correct; the doc sentence
            that called them identical was the wrong side.

            This printed the mean *failure* rate. A run the report called
            "RIGHT 95%" appeared here as "RIGHT 12%", so `history` said a system
            was failing badly while its own report said it was passing. It was
            also a mean of per-eval rates rather than a pooled one, so it could
            not have matched the report even with the sense corrected.
            """
            failures = total = 0.0
            bare = []            # summaries written before total_runs existed
            for uc_stats in summary.values():
                for stats in uc_stats.get(tag, {}).values():
                    fr = stats.get("failure_rate")
                    if fr is None:
                        continue
                    n = stats.get("total_runs") or 0
                    if n:
                        failures += fr * n
                        total    += n
                    else:
                        bare.append(fr)
            if total:
                return f"{round((1 - failures / total) * 100)}%"
            if bare:
                # No denominators to weight by: fall back to the unweighted mean
                # so an older result file still shows something sensible.
                return f"{round((1 - sum(bare) / len(bare)) * 100)}%"
            return "—"

        right = _tag_rate("right")
        good  = _tag_rate("good")
        safe  = _tag_rate("safe")

        click.echo(
            f"{run_id:<26}  {ts_display:<18}  {set_name:<12}  "
            f"{fixture_count:<10}  {judge_str:<28}  {right:<8}  {good:<8}  {safe:<8}"
        )

    if unreadable:
        click.echo(
            f"\n  ⚠ {len(unreadable)} result file(s) could not be read and are "
            f"not listed: {', '.join(sorted(unreadable))}. A run interrupted "
            f"mid-write leaves one; delete it or re-score."
        )

    if legacy:
        click.echo(
            f"\n  {len(legacy)} older result file(s) in this directory are not "
            f"listed — they predate the current naming and carry no summary "
            f"fieldtest can read."
        )

    if calibrations:
        click.echo(
            f"\n  {len(calibrations)} calibration run(s) in this directory are "
            f"not listed — they measure the judge, not the system. Read the "
            f"-calibration.md beside each."
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

    result_files = result_files_newest_first(results_dir)
    if not result_files:
        click.echo(
            f"No results found at {results_dir}.\n"
            f"  Run 'fieldtest score' to generate results."
        )
        return

    # Resolve current and baseline. find_result_by_run_id, not string
    # concatenation: filename and run id are not always the same string, and
    # the bundled demo is exactly that case — demo-offline-data.json whose
    # run_id is a timestamp. `view` resolves it and the auto-baseline branch
    # below resolves it; these two explicit-id branches were the siblings that
    # still built a path by hand, so `fieldtest diff <id>` rejected the id
    # `fieldtest history` had just printed, in the documented first workflow.
    if run_id:
        current_path = find_result_by_run_id(results_dir, run_id) or (
            results_dir / f"{run_id}-data.json")
    else:
        current_path = result_files[0]

    if baseline_id:
        baseline_path = find_result_by_run_id(results_dir, baseline_id) or (
            results_dir / f"{baseline_id}-data.json")
    else:
        # most recent that isn't current
        others = [f for f in result_files if f != current_path]
        baseline_path = others[0] if others else None

    if not current_path.exists():
        click.echo(f"Run not found: {current_path}", err=True)
        sys.exit(1)

    # Unguarded, this raised JSONDecodeError as a traceback. A result file
    # truncated by an interrupted score is an ordinary condition, not a bug.
    try:
        current_data = json.loads(current_path.read_text(encoding="utf-8"))
    except Exception as e:
        click.echo(
            f"Cannot read {current_path.name}: {str(e).splitlines()[0]}\n"
            f"  The file is unreadable — a run interrupted mid-write leaves one. "
            f"Delete it, or pass a different run id.",
            err=True,
        )
        sys.exit(1)

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

        try:
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
        except Exception as e:
            click.echo(
                f"Cannot read baseline {baseline_path.name}: "
                f"{str(e).splitlines()[0]}",
                err=True,
            )
            sys.exit(1)
        delta = build_delta(current_data.get("summary", {}), baseline_path)
    else:
        delta = current_data.get("delta", {})
        base_id = delta.get("baseline_run_id")
        if base_id:
            auto_path = find_result_by_run_id(results_dir, base_id)
            if auto_path is not None:
                try:
                    baseline_data = json.loads(auto_path.read_text(encoding="utf-8"))
                except Exception as e:
                    # An empty dict here drove every downstream branch to a
                    # false statement: diff asserted the baseline "predates
                    # judge tracking" about a run that records its judge, and
                    # silently suppressed the dataset-version and added/removed
                    # eval checks. The file was simply unreadable.
                    click.echo(
                        f"⚠ baseline {base_id} could not be read "
                        f"({str(e).splitlines()[0]}) — comparing against it is "
                        f"not possible; the deltas below are from the stored "
                        f"summary only.",
                        err=True,
                    )
                    baseline_data = {"__unreadable__": True}
            else:
                # The sibling branch, missed when the read-error one was fixed.
                # `fieldtest clean --results` deletes old runs, so the baseline
                # named in a stored delta is routinely gone — and an empty dict
                # here produced the identical false claim the comment above
                # describes: "predates judge tracking" about a run whose judge
                # block fieldtest wrote itself, minutes earlier.
                click.echo(
                    f"⚠ baseline {base_id} is no longer in {results_dir} — "
                    f"the deltas below are from the stored summary, and the "
                    f"comparison cannot be re-checked.",
                    err=True,
                )
                baseline_data = {"__unreadable__": True}

    # The run's recorded identity, as `history` prints it — not the file stem.
    # run_id_from_path is the filename concept (`clean` builds sibling paths
    # with it, and must keep doing so); for the bundled demo the two differ,
    # and diff named the run `demo-offline` while history called it a
    # timestamp. Same precedence as history: embedded run_id, then the stem.
    current_id = current_data.get("run_id") or run_id_from_path(current_path)
    click.echo(f"Comparing: {current_id}")
    # The key is always present and is None when there is no baseline, so a
    # `.get(..., '—')` default is unreachable and the line read "Baseline:
    # None" — the literal string, as though a run were named that.
    base_run_id = delta.get("baseline_run_id")
    # Why there is no baseline was already worked out at score time and stored
    # on the delta; report.py and html.py both print it. `diff` used to assert
    # "no earlier run to compare against" instead, which is false whenever the
    # baseline was rejected rather than absent — a different set, a bumped
    # dataset version, a changed judge.
    no_baseline_reason = delta.get("no_baseline_reason")
    if base_run_id:
        click.echo(f"Baseline:  {base_run_id}")
    elif no_baseline_reason:
        click.echo(f"Baseline:  none — {no_baseline_reason}")
    elif len(result_files) > 1:
        # No stored reason is not evidence of a first run either — older result
        # files predate the field. Say what is known.
        click.echo("Baseline:  none — no baseline recorded for this run")
    else:
        click.echo("Baseline:  none — no earlier run to compare against")
    click.echo("")

    # Warn if the user is comparing across dataset snapshots — only fires when
    # both runs declare a version and they differ. Either side unversioned is
    # treated as backwards-compat silence.
    cur_ver     = current_data.get("dataset_version")
    base_ver    = baseline_data.get("dataset_version")
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
    elif (cur_judge and base_run_id and base_judge is None
          and not baseline_data.get("__unreadable__")):
        # Guarded on readability: an unreadable baseline also has no judge
        # block, and this line then asserted something false about a run that
        # records its judge perfectly well.
        click.echo(
            "⚠ Baseline predates judge tracking — the judge that produced it is unknown."
        )
        click.echo("")

    # A baseline that lost most of its judge calls is a rate over whatever
    # survived. Comparing against it is not like-for-like, and `diff` is where
    # someone reads these numbers most closely.
    changed = delta.get("sample_changed") or []
    if base_run_id and changed:
        shown = ", ".join(changed[:4]) + (" …" if len(changed) > 4 else "")
        click.echo(
            f"⚠ {len(changed)} eval(s) scored a different number of outputs than "
            f"the baseline ({shown}) — these deltas include a change of population."
        )
        click.echo("")

    share = delta.get("baseline_error_share") or 0.0
    if base_run_id and share >= 0.1:
        click.echo(
            f"⚠ Baseline lost {share * 100:.0f}% of its judge calls to errors — "
            f"its rates cover only what survived, so these deltas are not "
            f"like-for-like."
        )
        click.echo("")

    # build_delta compares only evals present on both sides — an eval added or
    # removed between the runs is skipped with no entry in any bucket, so a
    # rename dropped a row out of the diff without a word. Say which ones were
    # left out rather than reporting a comparison over a changed eval set as if
    # it covered everything.
    # The sentinel is truthy, so guarding only the judge line above let this
    # sibling run against an empty base_keys and report every eval in the run
    # as "only in this run" — a false sentence the Phase 4 fix introduced
    # while closing a different one. When the baseline could not be loaded,
    # added/removed evals are unknowable; the warning already said so.
    if baseline_data and not baseline_data.get("__unreadable__"):
        cur_keys  = _summary_eval_keys(current_data.get("summary", {}))
        base_keys = _summary_eval_keys(baseline_data.get("summary", {}))
        only_current = sorted(cur_keys - base_keys)
        only_base    = sorted(base_keys - cur_keys)
        for names, where in ((only_current, "this run"), (only_base, "the baseline")):
            if names:
                click.echo(
                    f"Not compared — {len(names)} eval(s) only in {where}: "
                    f"{', '.join(names)}"
                )
        if only_current or only_base:
            click.echo("")

    increased = delta.get("increased", [])
    decreased = delta.get("decreased", [])
    unchanged = delta.get("unchanged", [])

    unchanged_keys = delta.get("unchanged_keys") or []

    # Which eval ids need their use case spelled out. Ids are unique within a
    # use case, not across them, so `shared: 0.500 → 0.000` printed twice with
    # nothing to say which use case moved.
    seen_use_cases: dict = {}
    for item in increased + decreased + unchanged_keys:
        if isinstance(item, dict) and item.get("eval_id"):
            seen_use_cases.setdefault(item["eval_id"], set()).add(item.get("use_case"))

    def _label(item: dict) -> str:
        eval_id = item["eval_id"]
        uc = item.get("use_case")
        if uc and len(seen_use_cases.get(eval_id, ())) > 1:
            return f"{uc}/{eval_id}"
        return eval_id

    # A delta written by an older run carries no `metric`, so fall back to the
    # summary it was computed from: a scored eval is the one with a mean.
    summary_metric: dict = {}
    for uc_id, tags in (current_data.get("summary") or {}).items():
        if not isinstance(tags, dict):
            continue
        for evals in tags.values():
            if not isinstance(evals, dict):
                continue
            for eval_id, st in evals.items():
                if not isinstance(st, dict):
                    continue
                if st.get("mean") is not None:
                    summary_metric[(uc_id, eval_id)] = "mean"
                elif st.get("failure_rate") is not None:
                    summary_metric[(uc_id, eval_id)] = "failure_rate"

    def _metric(item: dict) -> Optional[str]:
        m = item.get("metric")
        if m in ("mean", "failure_rate"):
            return m
        return summary_metric.get((item.get("use_case"), item.get("eval_id")))

    # Neutral names, deliberately: the README's stance is that the user decides
    # what counts as a regression. What it cannot leave to the user is *which
    # number* moved — a +2.000 on a 1-5 mean and a +0.500 on a failure rate are
    # opposite news and were printed identically.
    metric_names = {"failure_rate": "failure rate", "mean": "mean score"}

    def _echo_bucket(name: str, items: list) -> None:
        # Stable order, and one heading per metric present. Metrics not named
        # above (a delta from a future version) group under a bare heading
        # rather than being silently relabelled.
        buckets: dict = {}
        for item in items:
            buckets.setdefault(_metric(item), []).append(item)
        for metric in sorted(buckets, key=lambda m: (m is None, m or "")):
            heading = name
            if metric in metric_names:
                heading = f"{name} — {metric_names[metric]}"
            click.echo(f"{heading}:")
            for item in buckets[metric]:
                click.echo(
                    f"  {_label(item)}: {item['previous']:.3f} → "
                    f"{item['current']:.3f} ({item['delta']:+.3f})"
                )
            click.echo("")

    if increased:
        _echo_bucket("Increased", increased)

    if decreased:
        _echo_bucket("Decreased", decreased)

    if unchanged:
        # unchanged is a list of bare ids kept for jq; unchanged_keys carries
        # the use case, so print from that where it is available.
        if unchanged_keys and len(unchanged_keys) == len(unchanged):
            names = [_label(k) for k in unchanged_keys]
        else:
            names = list(unchanged)
        click.echo(f"Unchanged: {', '.join(names)}")

    if not increased and not decreased and not unchanged:
        if not base_run_id:
            # "No comparable evals found between runs" reads as two runs that
            # shared nothing. Say why there is no baseline instead — and only
            # claim this is the only run when the directory says so, since the
            # usual cause is a baseline that was rejected, not one that is
            # missing.
            if no_baseline_reason:
                click.echo(
                    f"Nothing to compare — no usable baseline: {no_baseline_reason}."
                )
            elif len(result_files) > 1:
                others = len(result_files) - 1
                click.echo(
                    f"Nothing to compare — no baseline was recorded for "
                    f"{current_id}, though {others} other run(s) are present in "
                    f"{results_dir}."
                )
            else:
                click.echo(
                    f"Nothing to compare — {current_id} "
                    f"is the only run in {results_dir}."
                )
        else:
            click.echo("No comparable evals found between runs.")
