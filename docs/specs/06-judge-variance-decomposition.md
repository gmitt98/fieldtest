# Spec 06 — Judge variance decomposition

**Tier** 2 · **Depends on** 01, 02 · **Touches** `config.py`, `runner.py`, `results/aggregator.py`, `results/report.py`

## §1 Problem

`runs: 5` means the generator produced five outputs at `outputs/{fixture_id}/run-1.txt` through
`run-5.txt`, and `runner.score()` builds one judge task per `(eval, output)` pair. Each output is
judged exactly once.

So the `stddev` reported on a scored eval is the spread across five different system outputs
scored by a judge that was itself sampling at provider default temperature. Two sources of
variance are summed and reported as one number attributed to the system.

Spec 02 pins the judge at temperature 0, which reduces the judge's contribution but does not
eliminate it and does not measure it. Providers do not guarantee determinism at temperature 0,
and Inspect's own documentation says as much: setting temperature to zero reduces but does not
always eliminate run-to-run disagreement, which is why they recommend running epochs and
inspecting grade variance.

fieldtest currently has no mechanism to run the equivalent. There is no way to judge the same
output twice.

The framing that matters: this is a gauge problem. fieldtest measures parts with an instrument
whose repeatability has never been characterized, and reports the total spread as if it belonged
to the part. The v1 argument says you cannot trust a metric until you have defined what it is
supposed to catch. The same argument says you cannot trust a spread until you know how much of
it is the instrument.

## §2 Requirements

1. Config expresses how many times each output is judged, independently of how many outputs the
   generator produced.
2. `ResultRow` carries which judge repetition produced it, so raw rows remain decomposable in
   `-data.csv` without needing the summary.
3. `build_summary()` reports system spread and judge spread as separate figures.
4. For binary evals, judge instability is reported as a disagreement rate: the fraction of
   outputs for which the judge did not return the same verdict on every repetition.
5. For scored evals, judge spread is the mean within-output standard deviation across
   repetitions, and system spread is the standard deviation of per-output means.
6. When `judge_runs: 1`, output is byte-identical to today's, and no judge spread fields appear.
   The default is 1, so nobody pays for this unless they ask.
7. A collapsed verdict per output is defined for binary evals so downstream consumers have one
   value per `(eval, output)`: majority verdict, ties resolved to fail. A tie means the judge
   could not decide, and for a `safe` eval the conservative reading is the right default.
8. `failure_rate` is computed from collapsed verdicts, not from raw repetition rows. Otherwise
   `judge_runs: 3` triples the denominator and makes rates incomparable across configurations.

## §3 Contract

`FixturesConfig` gains:

```yaml
fixtures:
  directory: fixtures/
  runs: 5          # generator outputs per fixture — unchanged meaning
  judge_runs: 3    # judge repetitions per output — new, default 1
```

`ResultRow` gains `judge_run: int = 1`.

Binary summary entry gains:

```json
{
  "failure_rate": 0.2,
  "judge_disagreement_rate": 0.0667,
  "judge_runs": 3,
  "total_runs": 5
}
```

Scored summary entry gains:

```json
{
  "mean": 3.5,
  "stddev": 0.42,
  "system_stddev": 0.38,
  "judge_stddev": 0.18,
  "judge_runs": 3
}
```

`stddev` retains its current definition over all score values so existing CI expressions do not
change meaning. `system_stddev` and `judge_stddev` are the decomposition and appear only when
`judge_runs > 1`.

Cost is multiplicative and must be stated in the CLI. `runs × judge_runs × evals × fixtures`
judge calls. `fieldtest validate` prints the projected call count for the configured set, so a
user discovers a 3x bill before paying it rather than after.

## §4 Compatibility

`judge_runs` defaults to 1. Every existing config produces identical rows, identical summaries,
and identical deltas.

`find_baseline()` must treat `judge_runs` as part of the comparability check, in the same family
as `set` and `dataset_version`: a 1-repetition baseline and a 3-repetition current run are not
comparable on judge fields, though their collapsed `failure_rate` values are.

## §5 Acceptance

Tests in `tests/test_config.py`, `tests/test_aggregator.py`, `tests/test_cli.py`:

- `test_judge_runs_defaults_to_one`
- `test_judge_runs_one_produces_identical_output_to_v1`
- `test_result_row_carries_judge_run`
- `test_binary_disagreement_rate_computed`
- `test_binary_verdict_collapses_by_majority`
- `test_binary_tie_collapses_to_fail`
- `test_failure_rate_denominator_unaffected_by_judge_runs`
- `test_scored_variance_decomposes_into_system_and_judge`
- `test_validate_prints_projected_call_count`

Behavioral acceptance: take a demo example, set `judge_runs: 5`, and confirm the report states
how much of the observed spread is the judge. On a well-specified eval that number should be
near zero, and an eval where it is not is an eval whose criteria are ambiguous. That diagnostic
is the point of the feature.

## §6 Out of scope

Agreement across different judge models. That is reproducibility rather than repeatability, and
it is spec 08.
