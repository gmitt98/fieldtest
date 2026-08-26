# Spec 04 — Failure rate intervals

**Tier** 1 · **Depends on** none · **Touches** `results/aggregator.py`, `results/report.py`, `results/html.py`, `config.py`

## §1 Problem

`build_summary()` computes `failure_rate = failed_count / total_runs` for binary evals and
reports it rounded to six places alongside `total_runs` and `error_count`. Scored evals get
`mean`, `stddev`, `min`, `max`, and `floor_hits`, which is a genuine distribution.

Binary evals get a point estimate with no uncertainty attached. At the default `runs: 5`, a
single failure produces `failure_rate: 0.2` with the same visual weight as twenty failures in a
hundred runs. `build_delta()` then compares those point estimates and classifies any movement
above 0.001 as increased or decreased, which means one flipped judgment on a five-run eval
registers as a 20 point swing.

The README claims outputs are "scored as distributions instead of pass/fail verdicts." On the
binary path that is a rate, not a distribution, and the claim currently overpromises.

This is also the one dimension where Inspect is ahead: its built-in graders ship `accuracy()`
and `stderr()` together, so an Inspect user sees an uncertainty band by default and a fieldtest
user does not.

## §2 Requirements

1. Binary eval summaries carry a two-sided confidence interval on `failure_rate`.
2. The interval is Wilson score, not normal approximation. At `runs: 5` with zero failures the
   normal interval is degenerate, and small `n` is the common case here, not the edge case.
3. Confidence level is configurable and defaults to 0.95.
4. No new runtime dependency. Wilson is closed form and `math` is already imported.
5. `total_runs` appears next to every rate in the markdown report and the HTML matrix, since an
   interval is uninterpretable without its `n`.
6. `build_delta()` marks a movement as `overlapping` when the current and baseline intervals
   overlap, in addition to the existing `increased` / `decreased` / `unchanged` classification.
   The existing three buckets keep their current semantics; `overlapping` is an additional flag
   on the entry, not a fourth bucket, so existing CI `jq` expressions keep working.
7. Scored evals are unchanged. `stddev` over the score values already conveys spread, and adding
   an interval on the mean is a separate question deferred to spec 06.

## §3 Contract

Binary eval summary entry gains:

```json
{
  "failure_rate": 0.2,
  "failure_rate_ci": [0.0362, 0.6194],
  "confidence": 0.95,
  "total_runs": 5,
  "error_count": 0,
  "floor_hits": 0
}
```

`failure_rate_ci` is `null` when `failure_rate` is `null`, which is the existing `total_runs == 0`
case and the scored-eval case.

Delta entries gain a boolean:

```json
{
  "eval_id": "no_policy_invention",
  "previous": 0.0,
  "current": 0.2,
  "delta": 0.2,
  "overlapping": true
}
```

`config.Defaults` gains `confidence: float = 0.95`, validated to the open interval (0, 1).

## §4 Compatibility

Additive to `-data.json`. The `failure_rate` field keeps its meaning and its position, so the
`jq` gating patterns published in the README continue to work unchanged.

The README's CI section should gain one paragraph noting that gating on a point estimate at
`runs: 5` is gating on noise, and pointing at `failure_rate_ci[0]` as the conservative
alternative. That is a documentation change, not a behavior change: fieldtest still measures and
the user still judges.

## §5 Acceptance

Tests in `tests/test_aggregator.py`:

- `test_wilson_interval_computed_for_binary_eval`
- `test_wilson_interval_nondegenerate_at_zero_failures`
- `test_failure_rate_ci_null_when_rate_null`
- `test_confidence_level_configurable`
- `test_scored_eval_summary_unchanged`
- `test_delta_flags_overlapping_intervals`
- `test_delta_buckets_unchanged_by_overlap_flag`

Behavioral acceptance: a five-run eval with one failure and a hundred-run eval with twenty
failures render with visibly different intervals in the markdown report and the HTML matrix.

## §6 Out of scope

Recommending a sample size. The tool reports the interval; deciding that it is too wide to act
on is the user's call, consistent with the existing position that thresholds live in CI config
rather than in the tool.
