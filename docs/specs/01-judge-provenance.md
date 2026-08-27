# Spec 01 — Judge provenance in run metadata

**Tier** 1 · **Depends on** none · **Touches** `results/writer.py`, `results/aggregator.py`, `cli.py`, `results/report.py`

## §1 Problem

`_build_json()` writes `run_id`, `set`, `dataset_version`, `fixture_count`, `runs`, `rows`,
`summary`, `delta`. It does not write which judge produced the scores.

`find_baseline()` filters candidate baselines on `set` and `dataset_version`. It does not filter
on the judge. So changing `defaults.model` from `claude-haiku-3-5` to `claude-sonnet-4` and
rescoring the same `outputs/` directory produces a `fieldtest diff` that is indistinguishable
from a system regression.

The same class of defect was already fixed once. `fixtures.version` exists because fixture drift
made deltas lie, and `find_baseline()`'s docstring states the reasoning: comparing across
populations shows movement that is an artifact of coverage rather than model behavior. Judge
drift produces the identical artifact and is the more frequent event, because judge model
versions deprecate on the provider's schedule rather than the team's.

A run is currently not self-describing with respect to its own measurement instrument.

## §2 Requirements

1. `-data.json` records a `judge` object describing every judge configuration used in the run.
2. The `judge` object includes per-eval overrides, since `Eval.model` and `Eval.provider` can
   diverge from `defaults` on any individual eval.
3. A run has a single derived `judge_fingerprint`: a stable hash over the full judge
   configuration, including overrides, sorted deterministically so that two runs with identical
   judge setups produce identical fingerprints.
4. `find_baseline()` skips candidates whose `judge_fingerprint` differs from the current run's.
5. `fieldtest diff` with an explicit `--baseline` warns when the fingerprint differs, in the same
   shape as the existing cross-version warning, and states which fields changed.
6. `fieldtest history` shows the judge model per run so a rate series is readable at a glance.
7. The markdown report header states the judge model and provider.

## §3 Contract

Added to the top level of `-data.json`:

```json
{
  "judge": {
    "provider": "anthropic",
    "model": "claude-haiku-3-5-20251001",
    "overrides": {
      "no_policy_invention": { "provider": "openai", "model": "gpt-5" }
    },
    "fingerprint": "a3f91c2e"
  }
}
```

`overrides` contains only evals whose `provider` or `model` differs from `defaults`. An empty
overrides map serializes as `{}`, never omitted, so consumers can index it unconditionally.

`fingerprint` is the first 8 hex characters of a SHA-256 over the canonical JSON serialization
of `{provider, model, overrides}` with sorted keys. Truncation is for human readability in
`history` output. Collisions are not a safety concern here; the fingerprint gates a warning, not
a correctness decision.

Signature change:

```python
def find_baseline(
    results_dir: Path,
    current_run_id: str,
    set_name: str,
    dataset_version: Optional[str] = None,
    judge_fingerprint: Optional[str] = None,
) -> Optional[Path]:
```

## §4 Compatibility

Baselines written before this spec have no `judge` key. When `judge_fingerprint` is supplied and
a candidate lacks the key, treat it as unknown rather than as a mismatch: accept it as a baseline
and emit a one-line note in the delta section that the baseline predates judge tracking. Silently
rejecting every historical baseline would blank out the delta for existing users on upgrade,
which is a worse failure than a caveated comparison.

## §5 Acceptance

Tests in `tests/test_aggregator.py` and `tests/test_cli.py`:

- `test_data_json_includes_judge_block`
- `test_judge_block_includes_per_eval_overrides`
- `test_judge_fingerprint_stable_across_identical_configs`
- `test_judge_fingerprint_changes_when_override_added`
- `test_find_baseline_skips_different_judge_fingerprint`
- `test_find_baseline_accepts_pre_judge_baseline_with_note`
- `test_diff_warns_on_judge_mismatch_with_explicit_baseline`

Behavioral acceptance: score a fixture set, change `defaults.model`, rescore the same
`outputs/` without touching the system, and confirm the run does not auto-baseline against the
prior run and that an explicit `--baseline` names the instrument change.

## §6 Out of scope

Judge generation parameters are spec 02. This spec records identity only. Once 02 lands,
`temperature` and `seed` join the fingerprint input, which is a one-line change to the hash
payload.
