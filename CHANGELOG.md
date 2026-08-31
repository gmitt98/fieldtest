# Changelog

## 0.3.0

This release is about the judge. fieldtest now pins it, records which one ran, reports how
certain a rate is, and gives you two ways to check whether the judge deserves to be believed.

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

Some models reject these parameters. Anthropic removed sampling on its 5-series; OpenAI's
reasoning models reject `temperature` and require `max_completion_tokens`. fieldtest sends the
parameter, and if the provider refuses it, drops it and completes the run:

```
⚠ judge parameters ignored by provider: temperature (openai)
```

A judge that dropped `temperature` is not pinned. Treat its run-to-run variation as real.

A client library refusing a parameter is a different thing from a model refusing it, and the two
are no longer conflated. anthropic 1.x removed `temperature` from `messages.create()` while the
API went on accepting it, so a fresh install — which is exactly what `pip install fieldtest`
gets — would have run every default judge unpinned and merely said so. Those parameters now
travel in `extra_body` and stay in force. If the model itself refuses one, it is still dropped
and named.

### The judge sees what your system was answering

LLM judges previously saw the output and nothing else — not the question, not the retrieved
context. A grounding eval asking whether every claim traces to the source was answering without the
source. Fixture `inputs` now go to the judge alongside the output:

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

**This changes results** for every eval whose fixture has inputs. The direction is not
predictable: a judge that can read the context may pass answers it was failing, or fail answers it
was passing.

### Every run records its judge

```json
{
  "schema_version": 2,
  "judge": {
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
    "temperature": 0.0,
    "seed": null,
    "overrides": {},
    "blinded_evals": [],
    "fingerprint": "4f10569a"
  }
}
```

Changing `defaults.model` and rescoring the same outputs used to produce a diff indistinguishable
from a system regression. Runs with different fingerprints are no longer compared automatically,
and `fieldtest diff --baseline` names what changed:

```
⚠ Judge mismatch — model: claude-haiku-4-5 → claude-sonnet-5.
```

`fieldtest history` has a JUDGE column.

### Rates come with an interval

`failure_rate: 0.2` read the same whether it came from one failure in five runs or twenty in a
hundred. At `runs: 5`, one flipped judgment moves it by 0.2.

```
| eval              | pass rate      | n |
| golden-reply      | 100% [44–100%] | 3 |
| addresses-the-ask | 78% [45–94%]   | 9 |
```

The interval is Wilson score; at five runs with zero failures the normal approximation gives
[0, 0]. `defaults.confidence_level` sets the level, default 0.95. Delta entries gain an `overlapping`
flag when the two intervals overlap.

For CI, `failure_rate_ci[0]` is the rate your sample actually supports:

```bash
jq '[.summary[][][].failure_rate_ci[0] | select(. != null)] | max // 0' "$DATA"
```

### `judge_runs` — how much of the spread is the judge

`runs: 5` produced five outputs, each judged once, so `stddev` mixed two sources of variance:
your outputs differing, and the judge scoring the same output differently.

```yaml
fixtures:
  runs: 5          # generator outputs per fixture
  judge_runs: 3    # judge repetitions per output
```

```
### Judge Repeatability (judge_runs: 3)
| eval                        | judge disagreement | system spread | judge spread |
| appropriate-tone            | —                  | 1.0           | 0.8165       |
| no-unauthorized-commitments | 50.0%              | —             | —            |
```

Judge spread near zero means the eval is well specified. Judge spread close to system spread
means the criteria are ambiguous.

`failure_rate` still comes from one verdict per output — majority across repetitions, ties resolved
to fail — so rates stay comparable across `judge_runs` settings.

### Human labels — score the judge, not the system

Record what you think the correct verdict is, per eval and per generator run:

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
| addresses-the-ask           | 3            | 100.0%    | 0 false pass, 0 false fail |
| no-unauthorized-commitments | 3            | 100.0%    | 0 false pass, 0 false fail |
```

False passes are counted separately from false fails; on a `safe` eval they are not the same
mistake. Labels do not affect `failure_rate`.

### `fieldtest calibrate` — put the judge under test

Run several judges over the same outputs and compare them.

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

Rescoring an existing `outputs/` directory is cheap, so this costs one extra pass per judge. Per
eval you get pairwise agreement, Cohen's kappa and Fleiss' kappa; scored evals get mean absolute
deviation and Spearman correlation.

Kappa rather than raw agreement, because two judges that both always answer pass agree 95% of the
time on an eval whose true failure rate is 5%.

The report ranks your evals by how much the panel disagreed. Those are the `pass_criteria` to
rewrite. Where fixtures carry labels, each judge is also ranked by agreement with you.

### Judge errors stop shrinking your sample quietly

Only the Anthropic adapter retried. Since errored rows are excluded from `failure_rate`, provider
load could turn a five-run eval into a one-run eval that still reported a rate.

All three providers now share one retry policy: 429, 5xx, 529, connection and timeout errors, on a
5/10/20/40/60/60 second backoff, tunable via `defaults.judge_retry`. Auth failures, unknown models
and malformed judge responses still fail immediately.

Runs with errors say so:

```
⚠ judge errors: 3 of 48 calls failed after retry.
  affected evals: tone_professional (6 of 9 runs scored)
```

### The judge prompt is harder to hijack

Output was interpolated between bare `---` lines, so an output containing its own `---` line
closed the data block early and anything after it read as instruction.

Whole-line delimiters in outputs and inputs are now rewritten before the prompt is built, and the
row records it: `[output delimiters neutralized] <reasoning>`. Judge responses are parsed as the
last complete JSON object, so an output that echoes a verdict before the judge gives one is not
read as the verdict.

### Judge with anything, and record which anything

fieldtest shipped three provider names. Anything else raised `ProviderError`.

The gap was narrower than it looked — the `openai` SDK honours `OPENAI_BASE_URL`,
so the existing adapter already reached OpenRouter, vLLM and Ollama — but the
endpoint lived in a shell variable. It was not versioned with your config, not
visible to anyone reading it, and not in the judge fingerprint, so two runs
against the same model name on different endpoints compared as though one
instrument produced both.

Name it in config instead:

```yaml
defaults:
  provider: openai_compatible
  model: meta-llama/llama-3.3-70b-instruct

providers:
  openai_compatible:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY    # omit for an endpoint needing no key
```

`api_key_env` names an environment variable. A literal key in config.yaml is
rejected rather than ignored, so the file stays committable. The endpoint joins
the fingerprint, and `fieldtest diff` names a change of it.

For an endpoint that does not speak the OpenAI protocol, register an adapter
where you already register rule evals:

```python
# evals/providers.py
from fieldtest import provider

@provider("my-inference-service")
def call(model, prompt, gen, retry) -> dict:
    ...
```

A registered name works in `defaults.provider`, in a per-eval override, and in a
`calibration.panel` entry. An adapter that raises costs one errored row rather
than the run.

`fieldtest validate` now reports which providers your config reaches and whether
each credential is set, before the run rather than twenty errored rows into it.

### A dataset to write your first eval against

The demos show a finished eval suite. Every eval in them is already written,
which makes them a poor place to learn how to write one.

```bash
fieldtest dataset use expense-report
fieldtest score --set full          # no API key needed
```

`expense-report` ships the artifacts and leaves the evals to you: a prompt, a
travel policy, receipt files, and nine outputs as though a generator had just
written them. Six carry a deliberate fault. Two of those are catchable with no
API call, because the shipped scaffold's filled-in evals are `rule`, `regex`
and `reference` — so the first run produces real failures before you have a key
or have written anything. Three more evals are `TODO`.

`support-agent` is the second: nine JSON agent traces, with tool calls, tool
results and the message the agent sent. fieldtest needs no new concept for this
— an output is text, and a rule eval parses the trace however it likes. Four of
its six faults are deterministic, including a trace that tells the customer a
case was escalated when the tool returned an error.

Each ships a `reference-evals.yaml` answer key covering all five judge types. They are
worth reading for their failures too: `expense-report`'s `caps_applied` eval is
scoped in writing to judge two daily caps, and the judge still fails outputs for
unrelated defects. The fixtures carry human labels, so the report says so — 100%
judge/human agreement on every deterministic eval, 66.7% on that one.

### The HTML report shows the judge too

The markdown report has named the judge since the start of this release, and
carried judge-repeatability and judge-vs-label tables alongside it. The HTML
report — the one `fieldtest view` opens — showed none of them, while embedding
every figure in its own data.

It now carries a judge line in the header:

```
Judge: anthropic/claude-haiku-4-5 · temp 0.0 · judged 3× each · 4f10569a
```

plus a **Judge vs your labels** table wherever fixtures carry labels, and a
**Judge repeatability** table wherever `judge_runs > 1`. Agreement below 80% is
marked. Neither appears when there is nothing to report.

Both reports also stop listing rule and regex evals in the repeatability table.
`judge_runs` applies to LLM evals — a rule is evaluated once however high you
set it — but the summary recorded the configured number on every eval, so rule
evals appeared at "0.0% disagreement" as though a judge had been asked twice
and agreed. The field now records how many times each eval was actually
judged.

### Fixture inputs can name a file

```yaml
inputs:
  policy: "file:sources/travel-policy.md"
```

Previously the judge would have been shown the string `sources/travel-policy.md`
and asked whether the output was grounded in it. `file:` reads the document at
fixture load, so rule evals and LLM evals are handed the same thing, and a
missing target fails `fieldtest validate` rather than the twentieth judge call.

Values without the prefix are unchanged, so `question: "see notes/faq.md"` stays
a literal string.

---

### What the release audits caught

Three adversarial audit rounds ran against 0.3.0 before shipping. The fixes
below are part of this release; each one is something that could have cost you
data, money, or a wrong conclusion.

**`fieldtest clean` no longer deletes work it never named.** In 0.2.2, `clean
--outputs` deleted `./outputs/` in any directory containing a `config.yaml` —
without checking it was a fieldtest project. A config beside an `outputs/`
directory describes most ML projects, so running it in the wrong directory
silently deleted checkpoints and exited 0. It now parses the config first and
refuses anything that is not a fieldtest project. The confirmation prompt
counts and lists every file it will remove (it previously counted only `*.txt`
while deleting the whole tree), and `--results` removes only the five known
artifacts per run instead of everything matching the run id — so a write-up
you named after a run survives.

**Results are ordered by recency, not filename.** Result files were sorted by
name, which looks right because run ids are timestamps — until the bundled
`demo-offline` file sits beside them and sorts above every real run. In a demo
directory, `clean --results --keep 1` deleted both of your real runs and kept
the shipped one; `diff` and `find_baseline` compared your new run against the
demo data instead of your own previous run; `history` listed the demo first.
All of it now orders by modification time.

**A run that measured nothing no longer reports success.** `fieldtest score`
and `fieldtest calibrate` exited 0 when 100% of judge calls errored — a CI job
gating on them went green on a run that produced no verdicts at all. Both now
exit 1 when every call errored, naming the count and pointing at the
credential and model. A run with some errors and some verdicts still exits 0
and reports the errors.

**The numbers were checked, and two were wrong.** The Judge Repeatability
variance split was biased in both directions at once — enough to report a
noisy judge as a noisy system and send you to fix the wrong thing. It now uses
a one-way random-effects estimator (true system 5.0 / judge 5.0, previously
reported as 5.72 / 3.59, now 4.96 / 4.95). And a rule eval returning no
`passed` value was counted as a pass in the per-eval table while Tag Health
counted the same row a failure; an unusable rule return is an error row now.

**Eval ids are scoped to their use case everywhere.** The same eval id in two
use cases used to lose one definition's type (a binary eval could report
`failure_rate: null` forever), and a delta from one use case could be printed
against the other's row — a stable eval reading −90% while the eval that moved
read unchanged. Both fixed; existing `jq` patterns keep working.

**First-run paths work.** `fieldtest view` no longer crashes without
`--config`. Fixtures in subdirectories — the layout `init` itself scaffolds —
now load, and two fixture files sharing a stem are refused rather than one
being silently scored twice. A judge answering 3.5 on a 1–5 scale no longer
aborts the run. `fieldtest demo` without a key leaves no half-created
directory behind, so the suggested `--offline` retry works.

**Config validation got stricter where silence cost money.** A per-eval
`provider` typo no longer passes `validate` and errors twenty paid calls into
a run. `judge_retry` rejects misspelled keys and negative values.
`judge_runs: 0` and `runs: 0` are errors instead of silently deleting every
LLM eval or writing a green empty result set.

**Packaging and typing.** The wheel ships `py.typed`, so mypy checks your code
against fieldtest's real types — and a stray comment that made mypy abort on
`fieldtest/config.py` (taking your own project's typechecking down with it) is
gone. Dependency floors are now installable on every supported Python (3.10 to
3.14): lowest-resolution installs (`uv --resolution lowest`, constraints
files) previously failed on 3.12+ because `pyyaml 6.0` and `pydantic 2.0`
cannot build there. The publish workflow refuses a tag that does not match the
package version and runs the full suite before building.

Also: the HTML report escapes everything user-controlled; `--concurrency 0`, a
non-UTF-8 output file, and a read-only `results/` directory produce one-line
errors instead of tracebacks; file writes carry an explicit UTF-8 encoding so
reports survive Windows; `fieldtest history` shows the pooled pass rate under
the same headings the report uses, instead of a failure rate that read "RIGHT
12%" for a run whose report said "RIGHT 95%"; and the report and README stop
claiming failure rates are comparable across `judge_runs` settings — ties
resolve to fail, so they are not.

## Changes from v0.2.2

- Config `schema_version: 2`. Version 1 configs load unchanged for one minor release
- `-data.json` adds `schema_version`, `judge`, `judge_runs`; summaries add `failure_rate_ci`,
  `confidence_level`, `judge_calls`, `outputs_attempted`; rows add `judge_run`
- New: `fieldtest calibrate [SET] [--dry-run]`
- New: `fieldtest dataset list` / `fieldtest dataset use <name>`
- Fixed: every Anthropic judge call failed on a fresh install. anthropic 1.2.0
  removed `temperature` from `messages.create()`, and the drop path did not
  recognise a client-library `TypeError` as a refusal. It does now — the call
  completes and the header names the dropped parameter
- `fieldtest validate` warns when a fixture set is declared in one use case and
  not another — `--set <name>` fails for the use case that lacks it, and nothing
  said so before the command was spent
- `fieldtest history` says how many older result files it could not read
- The report header no longer claims a per-eval output count across use cases —
  it multiplied total fixtures by runs, so a project with 11 resume and 3
  cover-letter fixtures was told "42 scored output(s) per eval" when no eval
  had more than 33
- Evals whose sample size differs from the baseline are named, so a redefined
  fixture set is not read as a change in the system
- A baseline that lost judge calls to errors is flagged in both the report and
  `fieldtest diff` — its rates cover only what survived, so the deltas are not
  like-for-like
- Judge-error remediation names the provider's stated cause (out of credit, over
  quota, rate limited, key rejected, model unknown) instead of generic advice
- New: `fieldtest --version`
- New: `fieldtest help [COMMAND]`; `fieldtest --help <command>` now shows that
  command's help instead of the general help
- `fieldtest calibrate` accepts `--set` as well as the positional set name, matching `score`
- Fixed: a calibration panel could fail with "No rule registered for eval ..." — the
  rule-file load memo recorded a path before executing it, so a second judge thread
  proceeded against an empty registry
- Commands find `config.yaml` when run from inside `evals/`, not only from its parent
- Fixture inputs accept a `file:` prefix, read at load time
- New provider `openai_compatible`, plus a `providers` config block and the
  `@provider` decorator loaded from `evals/providers.py`
- New config: `defaults.judge_temperature`, `judge_seed`, `judge_retry`, `confidence_level`;
  `fixtures.judge_runs`; `calibration.panel`; `Eval.judge_sees_inputs`
- Fixtures accept a `labels` block — per eval, per generator run
- `ProviderAdapter.call()` takes generation and retry config
- Default judge is `claude-haiku-4-5`; all bundled model ids updated
- `fieldtest validate` reports label coverage and projects judge calls before you spend them
- `fieldtest score` refuses a set that resolves to no fixtures
- Test suite: 130 → 719, in three tiers (`unit`, `integration`, opt-in `live`),
  plus `scripts/verify_tiers.py`, which reintroduces five defects that shipped
  and checks each is still caught

**Breaking:** results move. Pinning temperature removes sampling noise; showing the judge your
fixture inputs changes what it can see. Both are corrections, and both mean your first run on
0.3.0 is not comparable to your last on 0.2.2. `find_baseline()` will not compare across judge
fingerprints, so the first post-upgrade run simply finds no baseline.

A key fieldtest does not recognise is now an error naming the key, rather than a value
silently dropped. That is the second half of the `confidence` → `confidence_level` rename:
without it an upgraded config kept the old key, lost the setting, and reported intervals at
the default width with nothing to show for it. The same check catches a `runs:` written one
level above `fixtures:`, which quietly ran the default five times instead of yours. If your
config carries a stray key, `fieldtest validate` now names it and says where it belongs.

Otherwise `schema_version: 1` configs still load. The `jq` gating patterns in the README
still work — every `-data.json` change is additive.
