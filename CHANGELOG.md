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
[0, 0]. `defaults.confidence` sets the level, default 0.95. Delta entries gain an `overlapping`
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

---

## Changes from v0.2.2

- Config `schema_version: 2`. Version 1 configs load unchanged for one minor release
- `-data.json` adds `schema_version`, `judge`, `judge_runs`; summaries add `failure_rate_ci`,
  `confidence`, `judge_calls`, `outputs_attempted`; rows add `judge_run`
- New: `fieldtest calibrate [SET] [--dry-run]`
- New provider `openai_compatible`, plus a `providers` config block and the
  `@provider` decorator loaded from `evals/providers.py`
- New config: `defaults.judge_temperature`, `judge_seed`, `judge_retry`, `confidence`;
  `fixtures.judge_runs`; `calibration.panel`; `Eval.judge_sees_inputs`
- Fixtures accept a `labels` block — per eval, per generator run
- `ProviderAdapter.call()` takes generation and retry config
- Default judge is `claude-haiku-4-5`; all bundled model ids updated
- `fieldtest validate` reports label coverage and projects judge calls before you spend them
- `fieldtest score` refuses a set that resolves to no fixtures
- Test suite: 130 → 399, in three tiers (`unit`, `integration`, opt-in `live`),
  plus `scripts/verify_tiers.py`, which reintroduces four defects that shipped
  and checks each is still caught

**Breaking:** results move. Pinning temperature removes sampling noise; showing the judge your
fixture inputs changes what it can see. Both are corrections, and both mean your first run on
0.3.0 is not comparable to your last on 0.2.2. `find_baseline()` will not compare across judge
fingerprints, so the first post-upgrade run simply finds no baseline.

`schema_version: 1` configs still load. The `jq` gating patterns in the README still work —
every `-data.json` change is additive.
