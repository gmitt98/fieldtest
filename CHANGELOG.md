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
`fieldtest diff --baseline` names what changed (`claude-haiku-3-5 → claude-sonnet-4`) instead of
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

