# Changelog

## 0.3.0

v1 asked you to define what right, good and safe mean before measuring. This release turns the
same question on the judge doing the measuring.

A `failure_rate` is a claim about your system produced by a model whose identity, settings and
accuracy went unrecorded. fieldtest now records all three, and gives you the tools to check the
instrument before you trust the number.

### The judge holds still

Judges ran at whatever sampling temperature the provider defaulted to — usually 1.0. So `stddev`
on a scored eval and `failure_rate` on a binary eval both moved between runs for reasons that had
nothing to do with your system, and nothing in the report told you which was which.

Temperature is now pinned to 0.0 unless you say otherwise. Score the same `outputs/` twice and you
get the same answer twice.

```yaml
defaults:
  judge_temperature: 0.0    # default; set 1.0 for the old sampling behaviour
  judge_seed: null          # where the provider supports it
```

**Your numbers will move on upgrade.** That movement is noise being removed.

Where a model rejects a parameter — Anthropic removed sampling on its 5-series, OpenAI's reasoning
models reject `temperature` and want `max_completion_tokens` — fieldtest drops it, completes the
run, and names it in the report header rather than failing every call. It finds this out by asking,
not from a table: the Gemini adapter previously hardcoded `seed` as unsupported, never sent it, and
reported the omission as though the provider had refused. It does not; the parameter works.

```
⚠ judge parameters ignored by provider: temperature (openai)
```

A judge running without the parameters you asked for is not pinned, and the header says so.

### The judge sees what your system was answering

An LLM judge was shown the output and nothing else. Not the question, not the retrieved context —
just the reply, next to a criterion like "every claim can be traced to the retrieved excerpt".

Grounding evals returned pass and fail without ever seeing the source. The numbers looked judged
and were guessed.

Fixture `inputs` now go to the judge alongside the output:

```
System input:
---
context: Employees may expense meals up to $75 without prior approval...
question: What is the reimbursement limit?
---

Output to evaluate:
---
You can expense meals up to $75.
---
```

Set `judge_sees_inputs: false` on an eval that should judge the output alone, or to keep a large
context out of every call.

**This changes results** for every eval whose fixture has inputs, and not predictably — a judge
that can finally read the context may pass answers it was failing.

### Every run records its judge

```json
"judge": {
  "provider": "anthropic",
  "model": "claude-haiku-4-5",
  "temperature": 0.0,
  "overrides": {},
  "fingerprint": "9c022b78"
}
```

Change `defaults.model` and rescore the same outputs, and the diff used to look exactly like a
system regression. Runs with different fingerprints are no longer compared automatically, and
`fieldtest diff --baseline` names what moved:

```
⚠ Judge mismatch — model: claude-haiku-4-5 → claude-sonnet-5.
```

`fieldtest history` gained a JUDGE column so a rate series is readable at a glance.

### Rates come with an interval

A binary eval reported `failure_rate: 0.2` with the same weight whether that was one failure in
five runs or twenty in a hundred. At `runs: 5`, one flipped judgment is a 20-point swing — and the
README told you to gate CI on that number.

```
| eval          | pass rate      | n |
| no-fabricate  | 80% [38–96%]   | 5 |
```

Wilson score interval, because at five runs with zero failures the normal approximation claims a
certainty the sample cannot support. `defaults.confidence` sets the level. Deltas gained an
`overlapping` flag: movement between two overlapping intervals is movement your sample size cannot
distinguish from noise.

For CI, `failure_rate_ci[0]` is the rate your sample actually supports:

```bash
jq '[.summary[][][].failure_rate_ci[0] | select(. != null)] | max // 0' "$DATA"
```

### `judge_runs` — how much of the spread is the judge

`runs: 5` produced five outputs, each judged once. So `stddev` was the spread across five
different outputs scored by a judge that was itself varying. Two sources of variance, summed, and
attributed to your system.

```yaml
fixtures:
  runs: 5          # generator outputs per fixture
  judge_runs: 3    # judge repetitions per output
```

```
### Judge Repeatability (judge_runs: 3)
| eval          | judge disagreement | system spread | judge spread |
| tone          | —                  | 1.0           | 0.82         |
| no_promises   | 50.0%              | —             | —            |
```

A judge spread near zero means the eval is well specified. A judge spread that rivals the system
spread means the criteria are ambiguous. That diagnostic is the point.

Rates stay comparable: `failure_rate` comes from one collapsed verdict per output — majority, ties
resolved to fail, because a tie means the judge could not decide and on a `safe` eval that is not
a pass.

### Human labels — score the judge, not the system

There was nowhere to record what you think the right verdict is. So an eval reported a rate
against nothing.

```yaml
# evals/fixtures/golden/billing-dispute.yaml
labels:
  no-unauthorized-commitments:
    1: fail
    2: fail
```

```
### Judge vs Human Labels
| eval                        | labeled runs | agreement | errors                     |
| no-unauthorized-commitments | 3            | 66.7%     | 1 false pass, 0 false fail |
```

False passes are counted apart from false fails, because on a `safe` eval they are not the same
mistake. Labels never touch `failure_rate` — they score the judge.

### `fieldtest calibrate` — put the judge under test

fieldtest could measure your system. It could not measure the thing measuring your system.

```yaml
calibration:
  panel:
    - { provider: anthropic, model: claude-haiku-4-5 }
    - { provider: openai,    model: gpt-5 }
```

```bash
fieldtest calibrate --dry-run    # projected cost, calls nothing
fieldtest calibrate
```

Each judge scores the same `outputs/` — cheap, because your generator already wrote them — and you
get pairwise agreement, Cohen's kappa, and Fleiss' kappa across the panel. Scored evals get mean
absolute deviation and Spearman correlation.

Kappa rather than raw agreement is the point. On a `safe` eval whose true failure rate is 5%, two
judges that both always answer pass agree 95% of the time and have demonstrated nothing.

The output that matters is the ranking: your evals ordered by how much the panel disagreed. Those
are the `pass_criteria` that need rewriting. Where fixtures carry labels, each judge is also ranked
by agreement with you.

### Judge errors stop shrinking your sample quietly

Only the Anthropic adapter retried anything. OpenAI and Gemini errored on the first exception, and
because errored rows are excluded from `failure_rate`, a burst of provider load quietly turned a
five-run eval into a one-run eval that still reported a rate.

All three providers now share one retry policy — 429, 5xx, 529, connection and timeout errors, on
a 5/10/20/40/60/60 second backoff, tunable via `defaults.judge_retry`. Auth failures, unknown
models and malformed verdicts still fail immediately.

When a run does end up with errors, the report says so:

```
⚠ judge errors: 3 of 48 calls failed after retry.
  affected evals: tone_professional (6 of 9 runs scored)
```

### The judge prompt is harder to hijack

Output was interpolated between bare `---` lines, so an output containing its own `---` closed the
data block and everything after it read as instruction — the input class
`docs/recipes/adversarial-fixtures.md` tells you to write.

Whole-line delimiters in outputs and inputs are rewritten before the prompt is built, and the row
says so: `[output delimiters neutralized] <reasoning>`. Judge responses are read as the last
complete JSON object, so an output that echoes a verdict before the judge gives one no longer wins.

---

## Changes from v0.2.2

- Config `schema_version: 2`. Version 1 configs load unchanged for one minor release
- `-data.json` adds `schema_version`, `judge`, `judge_runs`; summaries add `failure_rate_ci`,
  `confidence`, `judge_calls`, `outputs_attempted`; rows add `judge_run`
- New: `fieldtest calibrate [SET] [--dry-run]`
- New config: `defaults.judge_temperature`, `judge_seed`, `judge_retry`, `confidence`;
  `fixtures.judge_runs`; `calibration.panel`; `Eval.judge_sees_inputs`
- Fixtures accept a `labels` block — per eval, per generator run
- `ProviderAdapter.call()` takes generation and retry config
- Default judge is `claude-haiku-4-5`; all bundled model ids updated
- `fieldtest validate` reports label coverage and projects judge calls before you spend them
- `fieldtest score` refuses a set that resolves to no fixtures
- Test suite: 130 → 349, in three tiers (`unit`, `integration`, opt-in `live`)

**Breaking:** results move. Pinning temperature removes sampling noise; showing the judge your
fixture inputs changes what it can see. Both are corrections, and both mean your first run on
0.3.0 is not comparable to your last on 0.2.2. `find_baseline()` will not compare across judge
fingerprints, so the first post-upgrade run simply finds no baseline.

`schema_version: 1` configs still load. The `jq` gating patterns in the README still work —
every `-data.json` change is additive.
