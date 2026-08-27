# Changelog

Notable changes to fieldtest. Each entry describes what you can now do, or what stopped
going wrong — not what commits landed.

## Unreleased

### Your judge now holds still between runs

Judges previously ran at whatever sampling temperature the provider defaulted to, which for
most providers is 1.0. That meant the `stddev` on a scored eval and the `failure_rate` on a
binary eval both moved between runs for reasons that had nothing to do with the system you
were measuring, and nothing in the report told you which was which.

The judge now runs at temperature 0.0 unless you say otherwise. Score the same `outputs/`
directory twice and you should get the same answer twice.

**Your numbers will move when you upgrade.** That movement is noise being removed, not a
regression in your system. If you want the old behaviour, set it explicitly:

```yaml
defaults:
  judge_temperature: 1.0
```

`defaults.judge_seed` is also available for providers that support it. Where a provider does
not support a parameter you asked for — Anthropic has no seed — fieldtest drops it, finishes
the run, and says so once in the report header instead of failing.

Gemini judges were also previously unbounded in output length, and are now capped like the
others.

### The judge can no longer be talked into a verdict by the output it is grading

The system's output was interpolated into the judge prompt between bare `---` lines. An output
containing its own `---` line closed the data block early, so anything after it read to the
judge as instruction rather than as text to evaluate — and `docs/recipes/adversarial-fixtures.md`
tells you to write fixtures that produce exactly that input class.

Whole-line delimiters in an output are now rewritten before the prompt is built, and when that
happens the row's detail says so: `[output delimiters neutralized] <the judge's reasoning>`.
Nothing changes for outputs that contain no delimiter — those prompts are byte-identical to
before, so no existing result moves.

Judge responses are also now read as the *last* complete JSON object rather than the whole
string, so an output that echoes a verdict before the judge gives its own no longer gets counted
as the judge's answer.

The demo carries `fixtures/adversarial/prompt-injection.yaml` as a worked example.

### An overloaded provider no longer shrinks your sample in silence

Only the Anthropic adapter retried anything. OpenAI and Gemini returned an error on the first
exception, and because errored rows are excluded from `failure_rate` and counted separately, a
burst of provider load quietly turned a five-run eval into a one-run eval that still reported a
rate. Which judge you used determined how much of your sample survived.

All three providers now share one retry policy — HTTP 429, 500, 502, 503, 504, 529 and the SDK
connection and timeout errors, on a 5/10/20/40/60/60 second backoff. The Anthropic schedule is
unchanged; OpenAI and Gemini runs that used to error under load will now take longer and finish.
Authentication failures, unknown models, missing packages, and malformed judge responses still
fail immediately.

Tune it per project:

```yaml
defaults:
  judge_retry:
    max_attempts: 2
    initial_delay: 1.0
```

When a run does end up with judge errors, the report header now states how many calls failed and
which evals were scored on a reduced sample — `quality_check (3 of 5 runs scored)` — and those
evals are marked in the per-eval table. The HTML report carries the same warning.

### A run now records which judge produced it — `schema_version: 2`

`-data.json` had no record of the instrument. Changing `defaults.model` and rescoring an
unchanged `outputs/` directory produced a `fieldtest diff` that was indistinguishable from a
system regression, which is the same defect `fixtures.version` already exists to prevent — and
the more frequent one, since judge models deprecate on the provider's schedule, not yours.

Every run now writes a `judge` block: provider, model, temperature, seed, per-eval overrides, and
a `fingerprint` over all of it. Runs whose fingerprints differ are no longer auto-compared, and
`fieldtest diff --baseline` names what changed (`claude-haiku-4-5 → claude-sonnet-5`) instead of
showing you a delta that means nothing. `fieldtest history` gained a JUDGE column so a rate series
is readable at a glance.

Baselines from before this release have no judge block. They are still accepted — blanking out
your delta history on upgrade would be worse — and carry a note saying the judge is unknown.

### Failure rates come with an interval

A binary eval reported `failure_rate: 0.2` with the same visual weight whether that was one
failure in five runs or twenty in a hundred. At `runs: 5`, one flipped judgment is a 20-point
swing, and the README told you to gate CI on exactly that number.

Binary summaries now carry `failure_rate_ci`, a two-sided Wilson score interval (Wilson because
at five runs with zero failures the normal approximation claims a certainty the sample cannot
support). `defaults.confidence` sets the level, default 0.95. The markdown report shows the
interval and `n` beside every rate, and the HTML matrix gained a per-eval row that does the same.

Deltas gained an `overlapping` flag: movement between two overlapping intervals is movement your
sample size cannot distinguish from noise. It is an extra field on the existing entries, not a
new bucket, so existing `jq` gating keeps working — and `failure_rate_ci[0]` is there when you
want to gate on what the sample actually supports.

**Config files are now `schema_version: 2`.** Version 1 configs still load for one minor release
and get every new field at its default, which reproduces v1 behaviour exactly. `fieldtest init`
scaffolds v2.

### You can now see how much of the spread is the judge

`runs: 5` produced five outputs, each judged exactly once. So the `stddev` on a scored eval was
the spread across five different outputs scored by a judge that was itself sampling — two
sources of variance summed into one number and attributed to your system. There was no way to
ask the judge the same question twice.

`fixtures.judge_runs` (default 1) sets how many times each output is judged. Above 1, the report
gains a Judge Repeatability table separating `system spread` from `judge spread`, and for binary
evals a `judge disagreement` rate: the share of outputs the judge did not rule on the same way
every time.

A judge spread near zero means the eval is well specified. A judge spread that rivals the system
spread means the criteria are ambiguous, and that is the diagnostic worth having.

Rates stay comparable across configurations: `failure_rate` is computed from one collapsed
verdict per output — majority, with ties resolved to fail, because a tie means the judge could
not decide and on a `safe` eval that is not a pass. The fixture × eval matrix and tag health
count the same collapsed verdicts, while `-data.csv` and `-data.json` keep every raw repetition
with a `judge_run` column so you can do your own decomposition.

Because the cost is multiplicative, `fieldtest validate` now prints the projected judge call
count for the full set.

### You can tell fieldtest when the judge got it wrong

There was nowhere to record what a human thinks the right verdict is. An eval reported a rate
against nothing: `failure_rate: 0.2` said the judge disagreed with the system on one of five
outputs, and offered no way to ask whether the judge was right to disagree.

Fixtures can now carry a `labels` block — per eval, per generator run, `pass`/`fail` for binary
evals or a score within `scale` for scored ones. Where a label exists, the report shows how often
the judge agreed with you, with false passes counted separately from false fails (on a `safe`
eval those are not the same mistake). Scored evals report mean absolute deviation from your score.

Labels are optional at every level and partial coverage is normal — one labeled run is enough to
learn something. They never affect `failure_rate`: they score the judge, not the system.
`fieldtest validate` checks label shape, eval ids, scale bounds and run numbers against your
config, and prints how many runs are labeled per eval so you can see how thin the ground truth is.

The email demo ships with `billing-dispute` labeled as a worked example.

### `fieldtest calibrate` — put the judge itself under test

fieldtest could measure your system. It could not measure the thing measuring your system. Every
`llm` eval ran exactly one judge, with no voting, no agreement computation, and no way to ask
whether that judge deserved the authority the report gave it.

Declare a panel in config and run `fieldtest calibrate`. Each judge scores the same `outputs/`
directory — cheap here, because your generator already wrote those files to disk — and you get,
per eval: pairwise agreement, Cohen's kappa, and Fleiss' kappa across the panel, or mean absolute
deviation and Spearman correlation for scored evals.

Kappa, not raw agreement, because on a `safe` eval whose true failure rate is 5%, two judges that
both always answer pass agree 95% of the time and have demonstrated nothing.

The output that matters is the ranked list: your evals ordered by how much the panel disagreed.
Those are the ones whose `pass_criteria` are ambiguous. Where fixtures carry `labels`, each judge
is also ranked by agreement with the human, with false passes and false fails kept apart.

`--dry-run` prints the projected call count and exits without calling anything — a four-judge
panel at `judge_runs: 3` is twelve times a normal run. Calibration writes its own
`{run_id}-calibration.json` and `.md`, and never participates in `fieldtest diff`: it is not a
measurement of your system.

The report ranks judges on evidence and stops there. Picking one has cost and latency inputs the
tool does not have.

### Fixes from review of the above

- **A malformed judge response no longer aborts the run.** A judge answering with a bare JSON
  scalar or an object-free array (`"looks fine"`, `[1,2,3]`) produced a non-dict that crashed
  scoring for every eval in the run, not just that one. It is now reported the same way any other
  unparseable verdict is — one errored row.
- **Judge errors are counted in the right units.** With `judge_runs` above 1, the report added
  judge calls to outputs and reported, for example, "3 of 4 calls failed" where the truth was 3
  of 6, and "1 of 4 runs scored" where it was 1 of 2. Summaries now carry `judge_calls` and
  `outputs_attempted` alongside `total_runs`.
- **Scored evals no longer report a `judge_agreement` figure.** It was exact equality between an
  integer human label and a mean across repetitions, so a judge returning 3, 4, 4 against a
  human's 4 scored zero agreement while matching perfectly. `mean_absolute_deviation` reports the
  same comparison honestly.
- **The `delta` object has one shape.** `baseline_pre_judge` and `baseline_judge_runs` were
  missing on runs with no baseline.
- **`fieldtest validate` projects the largest set you actually declare** instead of assuming
  `full` exists and silently printing nothing when it does not.

### Further fixes from a multi-agent review pass

- **`judge_run` is recorded correctly on LLM rows.** It was threaded into rule, regex and
  reference rows but dropped on the LLM path, so every repetition reported `judge_run: 1` — on
  the one eval type that repeats. The column `-data.csv` publishes for your own decomposition was
  a constant.
- **`fieldtest diff --baseline` compares against the run you name.** It was silently ignored: the
  command reused the delta frozen at score time against whatever baseline was auto-detected then.
  With judge fingerprints now filtering baselines, the run you name is often exactly the one
  auto-detection skipped, which is when you most need the flag.
- **Scored and binary evals report `n` in the same unit.** Under `judge_runs > 1` the binary
  branch counted outputs and the scored branch counted repetitions, so one report table showed an
  `n` column meaning two different things row to row.
- **A collapsed row's reasoning matches its verdict.** With repetitions, the row took the first
  repetition's reasoning even when that repetition argued the opposite way, so a majority-fail
  output could carry text explaining why it passed. Split decisions are now marked `[2/3 judges]`.
- **`fieldtest diff` reads the baseline file once** instead of three times, and the judge prompt
  is rewritten once per call instead of twice.

### Calibration fixes from review

- **The panel table counts judge calls**, not every row the pass produced. Regex, rule and
  reference rows were counted as judge calls, so the table contradicted the projection the same
  command had just printed.
- **A judge that produced no verdict is named.** One that errored on every call silently dropped
  out of the pairwise matrix and Fleiss' kappa, leaving a smaller panel's numbers presented as the
  configured panel's.
- **Every eval appears in the report.** An eval only one judge could rule on has no disagreement
  score, and was therefore dropped from the ranking *and* from the per-eval sections — hiding
  exactly the eval the panel could not evaluate.
- **Duplicate panel judges are rejected**, and `kappa_threshold` is bounded to [-1, 1]. The same
  model twice agrees with itself and inflates every figure; a threshold of `60` used to load
  cleanly and flag a perfect panel as failing.
- **Panel judges run concurrently**, sharing `--concurrency` as a total budget rather than
  multiplying it. Independent passes over the same files no longer cost the sum of their latencies.
- **Two use cases may declare the same eval id** without their verdicts being merged into one
  meaningless agreement figure.
- **The cost multiplier stopped double-counting `judge_runs`**, calibration artifacts are written
  all-or-nothing, and a suppressed-artifact run no longer resolves a baseline it will never use.

### Judges on the newest Anthropic models no longer fail outright

`claude-sonnet-5`, `claude-opus-5`, `claude-fable-5` and `claude-opus-4-7`/`4-8` removed sampling
parameters, so every judge call sending `temperature` came back `400 — temperature is deprecated
for this model`. Since fieldtest pins temperature to 0.0 by default, that meant *every* call to
those models errored.

This is not an Anthropic problem, so the fix is not an Anthropic fix. **Any** provider that
rejects **any** generation parameter by name now has that parameter dropped, the call retried, and
the fact named once in the report header — the same path `seed` already took. fieldtest keeps no
list of which model supports what, because that list would be wrong within weeks. A judge run that
way is not pinned, and the header says so.

**The default judge is now `claude-haiku-4-5`.** A judge that can be held still is worth more than
a larger one that cannot, and it costs less per call.

### Gemini judges work, and tell you what the model refused

The Gemini adapter had never made a real call. It does now, and two things came out of it.

`seed` is sent rather than assumed unsupported. Whether it is accepted is a fact about the model,
not about the provider: `GenerateContentConfig` exposes the field, and `gemini-3.7-flash` rejects
it at the API. The run completes and the report names it — `⚠ judge parameters ignored by
provider: seed (gemini)` — instead of silently pretending you got a pinned seed.

Documented example models moved off `gemini-2.5-flash`, which now returns 404 for new accounts
even though it still appears in Google's own model list.

### The judge can see what your system was answering

An LLM judge was shown the output and nothing else. Not the question, not the retrieved context —
just the reply, and a criterion like "every claim can be traced to the retrieved excerpt".

So a grounding eval returned pass and fail without ever seeing the source. The numbers looked
judged and were guessed, and nothing in the report distinguished the two.

Fixture `inputs` now go to the judge alongside the output, delimited and neutralized the same way
outputs already are. This changes results for every eval whose fixture has inputs, and the
direction is not predictable: a judge that can finally read the context may pass answers it was
failing, or fail answers it was passing on plausibility.

Set `judge_sees_inputs: false` on an eval that should judge the output alone, or to keep a large
context out of every call. That choice is part of the judge fingerprint, so a blinded run is never
silently compared against a sighted one.

Found by regenerating the bundled demo results: two rag evals jumped from 0.167 to 0.818, and the
judge's own reasoning said why — *"no handbook excerpt was provided to verify these details
against."* It was right.

