# Spec 08 — `fieldtest calibrate`

**Tier** 2 · **Depends on** 01, 02, 06, 07 · **Touches** `cli.py`, `config.py`, new `calibrate.py`, new `results/calibration.py`

## §1 Problem

fieldtest can measure a system. It cannot measure the thing measuring the system.

Every eval of type `llm` currently runs one judge. `dispatch_judge()` routes to
`judge_llm_binary` or `judge_llm_scored`, each of which makes exactly one provider call and
returns one `ResultRow`. `Eval.model` and `Eval.provider` allow a per-eval override but not a
per-eval panel. There is no voting, no agreement computation, and no way to ask whether the judge
deserves the authority the report gives it.

Inspect gets partway there. `model_graded_qa()` accepts a list of grader models and resolves by
majority vote through `multi_scorer()`, and the `score()` function can be called repeatedly over
a saved log with `action="append"` and a different grader each time. What Inspect does not ship
is any analysis of the resulting disagreement: no kappa, no agreement matrix, no calibration
report, no notion of a judge under test as an object distinct from the model under test. You get
the raw material and write the statistics yourself.

Neither tool ships the artifact. fieldtest is better positioned to, because the outputs are inert
text files on disk. Rescoring the same outputs with a different judge costs a directory read,
where Inspect's equivalent requires reading `.eval` logs through their API.

This is also the natural extension of the v1 thesis. The README argues that measurements without
a prior definition of good are measurements without meaning. A judge whose agreement with human
judgment has never been checked produces exactly that.

## §2 Requirements

1. A new command runs a panel of judges over the same `outputs/` directory and reports how much
   they agree.
2. The panel is declared in config, versioned with everything else, not passed as ad hoc CLI
   flags.
3. Where fixture labels exist (spec 07), agreement against human judgment is reported per judge,
   which is the number that actually matters. Judge-to-judge agreement without ground truth
   measures shared bias as readily as shared accuracy.
4. Reported per eval, not only per run. The question a user has is which of their evals is
   ambiguously specified, and a single run-level number cannot answer it.
5. Repetition within a judge (spec 06) composes: the panel measures reproducibility across
   judges, `judge_runs` measures repeatability within one, and both appear in the same report.
6. Writes its own artifacts alongside the existing five, in the same run-id naming convention.
7. Reuses `runner.score()` rather than forking the scoring path. A calibration run is N scoring
   runs over one output set, differing only in judge config.
8. Costs are stated before the run. A four-judge panel with `judge_runs: 3` is twelve times a
   normal run.

## §3 Contract

Config addition:

```yaml
calibration:
  panel:
    - { provider: anthropic, model: claude-haiku-4-5 }
    - { provider: anthropic, model: claude-sonnet-5 }
    - { provider: openai,    model: gpt-5 }
    - { provider: gemini,    model: gemini-2.5-flash }
```

CLI:

```
fieldtest calibrate [SET] [--config PATH] [--dry-run]
```

`--dry-run` prints the projected call count and cost shape and exits without calling anything.

Output files, alongside the existing five:

```
{run_id}-calibration.json
{run_id}-calibration.md
```

Statistics per eval:

| eval type | judge-to-judge | judge-to-human |
|---|---|---|
| binary | pairwise raw agreement, Cohen's kappa, Fleiss' kappa across the full panel | agreement, false pass count, false fail count |
| scored | pairwise mean absolute deviation, Spearman correlation | mean absolute deviation, signed bias |

Kappa rather than raw agreement is the point. On a `safe` eval where the true failure rate is
5%, two judges that both always answer pass show 95% raw agreement and a kappa near zero. Raw
agreement alone would certify a useless judge.

Report structure:

- Panel summary: which judges, which fingerprints, how many calls, how many errors per judge.
- Per eval: the statistics above, plus a flag when kappa falls below a configurable threshold.
- Ranked list of evals by judge disagreement, most contested first. That ordering is the
  actionable output: those are the evals whose `pass_criteria` need rewriting.
- Where labels exist, a per-judge accuracy ranking so a user can pick a judge model on evidence.

## §4 Compatibility

Purely additive. A config without a `calibration` block runs everything else unchanged, and
`fieldtest calibrate` on such a config exits with a config error naming the missing block and
showing the shape, consistent with the existing §17 error contract.

Calibration artifacts do not participate in `find_baseline()` or `fieldtest diff`. A calibration
run is not a measurement of the system and must not be mistaken for one.

## §5 Acceptance

Tests in `tests/test_cli.py` and a new `tests/test_calibration.py`:

- `test_calibrate_requires_panel_in_config`
- `test_calibrate_runs_each_panel_judge_over_same_outputs`
- `test_calibrate_reuses_score_path`
- `test_pairwise_agreement_computed_per_eval`
- `test_cohens_kappa_low_when_judges_agree_by_chance`
- `test_fleiss_kappa_across_full_panel`
- `test_scored_panel_reports_mad_and_correlation`
- `test_human_agreement_reported_when_labels_present`
- `test_human_agreement_absent_when_no_labels`
- `test_evals_ranked_by_disagreement`
- `test_dry_run_makes_no_provider_calls`
- `test_calibration_artifacts_excluded_from_find_baseline`

Behavioral acceptance: write two evals for the same demo example, one with sharp
`pass_criteria` and one deliberately vague, run a four-judge panel, and confirm the vague eval
ranks first by disagreement. If it does not, the statistics are wrong.

## §6 Out of scope

Automatic judge selection. The report ranks judges by evidence and stops there. Picking one is a
decision with cost and latency inputs the tool does not have, and the existing position that the
tool measures while the user judges applies here as much as it does to CI thresholds.
