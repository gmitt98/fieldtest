# Spec 07 — Human labels in fixtures

**Tier** 2 · **Depends on** none · **Touches** `config.py`, `judges/dispatch.py`, `results/aggregator.py`, `results/report.py`

## §1 Problem

A fixture today carries `id`, `inputs`, and optionally `expected` with `contains` and
`not_contains` lists. `judge_reference()` uses `expected` to do substring checking and returns
`skipped=True` with a reason when the block is absent.

There is nowhere to record what a human thinks the correct verdict is for a given eval on a
given output. So an eval reports a rate against nothing. `failure_rate: 0.2` says the judge
disagreed with the system on one of five outputs, and gives no way to ask whether the judge was
right to disagree.

Without this, spec 08 can only measure judges against each other. Two judges that agree with
each other and are both wrong look identical to two judges that agree and are both right.

## §2 Requirements

1. Fixtures may carry per-eval human verdicts.
2. Labels are per `(eval_id, generator run)`, because the whole point is that different generator
   outputs for the same fixture warrant different verdicts. A label keyed only by `eval_id` would
   assume the system is deterministic, which is the assumption fieldtest exists to reject.
3. Labels are optional at every level: no block, some evals labeled, some runs labeled. Partial
   coverage is the normal state and must not degrade anything.
4. Where a label exists, the summary reports judge agreement with it.
5. Where no label exists, behavior is exactly as today. No skip rows, no errors, no new fields.
6. Label format matches the eval type: `pass` or `fail` for binary, an integer within `scale` for
   scored. Mismatches are config errors caught by `fieldtest validate`, not runtime failures.
7. Labels are not used to score. They are used to score the judge. An eval's `failure_rate` is
   unaffected by the presence of labels.

## §3 Contract

Fixture schema addition:

```yaml
id: billing-dispute
inputs:
  customer_message: "..."
expected:
  contains: ["refund policy"]
labels:
  tone_professional:
    1: pass
    2: pass
    3: fail
  answer_quality:
    1: 4
    2: 3
```

Keys under each eval id are generator run numbers matching `outputs/{id}/run-N.txt`. Absent run
numbers are unlabeled.

Summary entry gains, only when at least one label exists for that eval:

```json
{
  "failure_rate": 0.2,
  "labeled_runs": 3,
  "judge_agreement": 0.667,
  "judge_false_pass": 1,
  "judge_false_fail": 0
}
```

`judge_false_pass` counts outputs a human failed and the judge passed. For a `safe` eval that is
the asymmetric error that matters, and collapsing it into a single agreement number hides it.
Scored evals report `mean_absolute_deviation` from the human score in place of the two counts.

Validation additions in `parse_and_validate()` and the `validate` command:

- Label keys reference eval ids that exist in the use case.
- Label values match the eval type and, for scored evals, fall within `scale`.
- Run numbers do not exceed `runs`.
- `validate` reports label coverage per eval so a user can see how thin the ground truth is.

## §4 Compatibility

Fixtures without a `labels` block behave identically. The three new summary fields appear only
when labels exist, so `-data.json` consumers are unaffected until a user opts in.

## §5 Acceptance

Tests in `tests/test_config.py` and `tests/test_aggregator.py`:

- `test_fixture_without_labels_unchanged`
- `test_labels_parsed_per_eval_per_run`
- `test_partial_label_coverage_allowed`
- `test_label_type_mismatch_is_config_error`
- `test_label_score_outside_scale_is_config_error`
- `test_label_run_number_exceeding_runs_is_config_error`
- `test_judge_agreement_computed_from_labels`
- `test_false_pass_and_false_fail_counted_separately`
- `test_failure_rate_unaffected_by_labels`
- `test_validate_reports_label_coverage`

Behavioral acceptance: label a handful of runs in a demo example, deliberately write an eval
whose `pass_criteria` is vague, and confirm the report shows the judge disagreeing with the human
on the vague eval while agreeing on the precise one.

## §6 Out of scope

Any labeling UI or workflow. Labels are hand-edited YAML, consistent with the rest of the
fixture format. A three-annotator workflow with inter-annotator agreement is a real need and a
different tool; if it lands here it lands after spec 08 proves the single-annotator case is used.
