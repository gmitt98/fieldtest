# fieldtest

The eval landscape is crowded at the execution layer and nearly empty at the practice layer.

Most eval tools assume you already know what to evaluate: you install a framework, run some metrics, see numbers. The numbers you get that way feel like quality yet they're not: they are measurements without meaning, because nobody defined what the measurements are supposed to catch before running them.

**fieldtest is a tool for the layer that's missing: the reasoning that produces the evals.**

The config asks you — in order — to name your use cases, define what right, good, and safe means for each, and specify how you'll test them. This is an easy sequence to skip, which is why teams can end up with evals that measure what's easy rather than what matters for their product. The structure of the testing enforces the reasoning behind your quality needs.

If you've used DeepEval, Promptfoo, Inspect, or Ragas and felt that running the eval was the easy part, that deciding *what* to evaluate was where the work actually lived, then you've experienced the gap that fieldtest is built for. This is not another judge framework — it is a config-first framework that forces you to name what "correct," "well-formed," and "safe" mean for your system before you can score anything. The output is structured, diff-friendly, and scored as distributions instead of pass/fail verdicts, so failure tells you where to look and what kind of fix it is. The trade is that fieldtest only reports on failure modes you named; unanticipated ones surface in the `-data.csv` reasoning column, not in the report ([why](docs/philosophy.md#what-fieldtest-will-not-find)).

---

## See it in 30 seconds

No API key needed. No setup. Just install and run:

```bash
pip install fieldtest
fieldtest demo --offline
```

You'll see a full scored eval report in the terminal — tag health across RIGHT / GOOD / SAFE, a fixture × eval matrix, and specific failure details. The demo scaffolds everything into `fieldtest-demo/`; step into it to open the visual HTML report:

```bash
cd fieldtest-demo
fieldtest view
```

This opens a self-contained HTML report in your browser: color-coded matrix, label filter bar, click any cell to see per-run pass/fail detail.

That's it. You just ran a structured eval suite with four eval types (rule, regex, LLM, reference), right/good/safe tags, and failure analysis — no API key, no configuration.

---

## Write your first eval against a bundled dataset

**→ [Full walkthrough](docs/walkthrough.md)** — fifteen minutes, no API key:
install, read the artifacts, run the evals that ship with them, write one
yourself, watch it catch a defect the others miss.

The demos show you a finished eval suite. A dataset gives you the artifacts and
leaves the evals to you.

```bash
fieldtest dataset list
fieldtest dataset use expense-report
fieldtest score --set full            # works with no API key
```

`expense-report` ships a prompt, a travel policy, receipt files, and nine
outputs as though a generator had just written them. Four evals are filled in —
one `rule`, one `regex`, one `reference`, one more `rule` — and all four are
deterministic, so the first run works before you have a key. Three more are
`TODO` with the question each has to answer.

Six of the nine outputs carry a deliberate fault. Three are catchable with
no API call at all; the other three need a judge. Working out which is which
is the exercise.

`support-agent` is the other one: nine JSON agent traces — tool calls, tool
results, and the message the agent sent. An output is text, so a rule eval
parses the trace however it likes, and four of its six faults need no API call.
One trace tells the customer a case was escalated when the tool returned an
error.

## Three demo modes

### Mode 1 — Offline (no API key, instant)

```bash
fieldtest demo --offline
cd fieldtest-demo
fieldtest view
```

Uses pre-scored results bundled with the package. Runs in under 2 seconds. Good for quick demos, job interviews, or machines without credentials set.

### Mode 2 — Live extraction (no API key, real scoring)

```bash
fieldtest demo --example extraction
cd fieldtest-demo
fieldtest view
```

Runs real `fieldtest score` on the extraction example. Rule and regex evals execute fully. LLM evals are gracefully skipped (marked as errors, excluded from rates) since no API key is present. Shows how the tool handles partial eval coverage cleanly.

### Mode 3 — Full live run (requires `ANTHROPIC_API_KEY`)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
fieldtest demo                      # email example (default)
fieldtest demo --example rag
fieldtest demo --example extraction
cd fieldtest-demo
fieldtest view
```

Runs all four eval types including LLM judges. Each example uses `claude-haiku-4-5` as the judge model by default (fast, cheap). Every example has at least one intentional failure so you can see how regressions surface in the report.

### Available examples

| Example | System | What it demonstrates |
|---------|--------|----------------------|
| `email` | Clearbook Support Assistant | LLM judge (tone, policy compliance), rule (greeting check), regex (forbidden terms), reference (golden fixture) |
| `rag` | Meridian Handbook Assistant | RAG grounding eval, hallucination detection, answer-length rule, citation regex |
| `extraction` | Invoice Data Extractor | JSON structure rules, field-presence rules, regex forbidden-field check — its deterministic evals run without an API key; its two LLM evals are skipped as errors (see Mode 2) |

### Demo flags

```bash
fieldtest demo                               # email example, live scoring
fieldtest demo --example rag                 # choose example
fieldtest demo --offline                     # pre-scored results, no API key
fieldtest demo --dir my-demo                 # scaffold to a custom directory (default: fieldtest-demo)
```

Each demo scaffolds a real working eval directory with config, fixtures, outputs, and rules. After it runs, explore freely — edit outputs, add fixtures, try `fieldtest score` again.

---

## Install

```bash
pip install fieldtest
export ANTHROPIC_API_KEY=sk-ant-...   # for LLM judge calls
```

Or with an alternative judge provider:

```bash
pip install fieldtest[openai]         # OpenAI judge support
pip install fieldtest[gemini]         # Google Gemini judge support
```

Set the provider in your config:

```yaml
defaults:
  provider: gemini                    # or anthropic, openai
  model: gemini-3.7-flash
```

And the corresponding API key:

```bash
export OPENAI_API_KEY=sk-...          # for openai provider
export GEMINI_API_KEY=...             # for gemini provider
```

### Interval width — `confidence_level`

Every binary eval reports a Wilson score interval beside its rate:

```
| total_matches_line_items | 78% [45–94%] | 9 |
```

Seven of nine passed. The bracket says the true rate is somewhere between 45%
and 94%, because nine runs is not much evidence. `defaults.confidence_level`
sets the level, default `0.95`:

```yaml
defaults:
  confidence_level: 0.95   # 0.80 narrows the bracket, 0.99 widens it
```

**This has nothing to do with asking a model how confident it is.** No judge is
consulted. The interval is arithmetic on the pass and fail counts — the same
calculation you would run on a coin. A model's self-reported confidence is
poorly calibrated and fieldtest never asks for it; a judge returns a verdict,
and the uncertainty comes from how few verdicts you have.

Wilson rather than the textbook normal approximation because small `n` is the
normal case here: at `runs: 5` with zero failures the normal interval collapses
to `[0, 0]`, claiming certainty five samples cannot support.

Scored evals get no interval — `stddev` already conveys their spread.

### Any OpenAI-compatible endpoint

vLLM, Ollama, OpenRouter, Together, Fireworks and xAI all speak the OpenAI
chat-completions protocol. Name the endpoint in config rather than in your shell:

```yaml
defaults:
  provider: openai_compatible
  model: meta-llama/llama-3.3-70b-instruct

providers:
  openai_compatible:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY    # omit for an endpoint needing no key
```

`api_key_env` is the *name* of an environment variable. Writing a key into
config.yaml is rejected rather than ignored, so a config file stays committable.

The endpoint is part of the judge fingerprint. The same model name served from
two places is two instruments, and `fieldtest diff` says so:

```
⚠ Judge mismatch — openai_compatible endpoint: http://localhost:8000/v1 → https://openrouter.ai/api/v1.
```

OpenRouter is worth calling out: one key and one base URL reach models from
Anthropic, OpenAI, Google, xAI, Meta, Qwen, DeepSeek and Mistral behind
`vendor/model` slugs, so a calibration panel spanning four labs does not need
four accounts.

One caveat, stated as narrowly as the evidence allows. Through OpenRouter,
`openai/o3-mini` accepted `temperature` and `max_tokens` and fieldtest reported
nothing dropped, while `gpt-5` called natively reports `unsupported:
["temperature"]`. Those are different models, so this does not establish that
OpenRouter strips parameters. What it does establish is that the drop path did
not fire through OpenRouter, so a judge reached that way may be running unpinned
with nothing in the report to reveal it.

### A provider fieldtest does not ship

For an endpoint that does not speak the OpenAI protocol, register an adapter the
same way you register a rule eval:

```python
# evals/providers.py
from fieldtest import provider

@provider("my-inference-service")
def call(model, prompt, gen, retry) -> dict:
    """Return the judge's parsed JSON dict, or {"error": str}."""
    ...
```

```yaml
defaults:
  provider: my-inference-service
  model: local-7b
```

A registered name works anywhere a built-in one does: `defaults.provider`, a
per-eval override, or a `calibration.panel` entry. `gen` carries the temperature
and seed you configured; honour what your endpoint supports and ignore the rest.
An adapter that raises costs one errored row rather than the run.

`fieldtest validate` reports which providers your config reaches and whether the
credential each one names is present, before you spend anything:

```
  provider 'anthropic' — ANTHROPIC_API_KEY set
  ⚠ provider 'openai_compatible' → https://openrouter.ai/api/v1 — OPENROUTER_API_KEY NOT set
```

### Judge generation settings

The judge runs at temperature 0.0 by default, not at the provider default. A judge left
sampling puts its own noise into every rate fieldtest reports, and you cannot tell that noise
apart from movement in the system you are measuring.

```yaml
defaults:
  judge_temperature: 0.0              # default; set 1.0 for the old sampling behaviour
  judge_seed: null                    # optional, where the provider supports it
```

Not every provider supports every parameter. Where one does not, fieldtest drops the parameter,
completes the run, and names it once in the report header rather than failing.

| provider | temperature | seed | max_tokens |
|---|---|---|---|
| anthropic | model-dependent — see below | no | yes |
| openai | yes | yes | yes |
| gemini | yes | yes | yes |

The table is a starting point, not a contract — **support is per model, not per provider, and it
changes on the provider's schedule.** Anthropic removed sampling parameters on `claude-sonnet-5`,
`claude-opus-5`, `claude-fable-5` and `claude-opus-4-7`/`4-8`, which reject `temperature`
outright; `claude-haiku-4-5` and the 4.6 family still accept it. Reasoning models from other
providers are moving the same way.

So fieldtest does not keep a list. When any provider rejects a generation parameter by name, the
adapter drops that parameter, retries, completes the run, and names it once in the report header:

```
⚠ judge parameters ignored by provider: temperature (anthropic)
```

A judge running without the parameters you asked for is **not pinned**. Treat its run-to-run
variation as real rather than as system noise, and reach for `judge_runs` to measure it. **The default judge is `claude-haiku-4-5` precisely because it can be
pinned.**

Temperature 0.0 reduces run-to-run judge disagreement but does not eliminate it — no provider
guarantees determinism. What is left is a property of the provider, not of your system.

### Measuring the judge itself — `fieldtest calibrate`

fieldtest can measure your system. `calibrate` measures the thing measuring your system.

Declare a panel in config:

```yaml
calibration:
  panel:
    - { provider: anthropic, model: claude-haiku-4-5 }
    - { provider: anthropic, model: claude-sonnet-5 }
    - { provider: openai,    model: gpt-5 }
    - { provider: gemini,    model: gemini-3.7-flash }
```

```bash
fieldtest calibrate --dry-run     # projected call count, calls nothing
fieldtest calibrate
```

Each judge scores the same `outputs/` directory — which costs a directory read, because your
generator already wrote them — and the report gives you, per eval, pairwise agreement, Cohen's
kappa, and Fleiss' kappa across the panel. Scored evals get mean absolute deviation and Spearman
correlation instead.

**Kappa rather than raw agreement.** Two judges that both always answer pass agree with
each other on every output — 100% raw agreement — and their kappa is exactly zero, because
none of that agreement is beyond chance. Raw agreement alone would certify a useless judge.

The actionable output is the ranked list: evals ordered by how much the panel disagreed, most
contested first. Those are the evals whose `pass_criteria` need rewriting. Where your fixtures
carry `labels`, each judge is also ranked by agreement with the human — the number that actually
matters, since judge-to-judge agreement without ground truth measures shared bias as readily as
shared accuracy.

Results are written as `{run_id}-calibration.json` and `{run_id}-calibration.md`. They never
participate in `fieldtest diff`: a calibration run is not a measurement of your system.

**A four-judge panel with `judge_runs: 3` is twelve times a normal run.** `--dry-run` prints the
number before you spend it.

### Telling the judge it was wrong

An eval reports a rate against nothing. `failure_rate: 0.2` says the judge disagreed with the
system on one output in five, and gives you no way to ask whether the judge was right to
disagree. Two judges that agree with each other and are both wrong look identical to two judges
that agree and are both right.

Fixtures can carry human verdicts, per eval and per generator run:

```yaml
id: billing-dispute
inputs:
  customer_email: "..."
labels:
  addresses-the-ask:
    1: pass
    2: pass
  no-unauthorized-commitments:
    1: fail      # commits to a refund amount and a timeline
```

Run numbers match `outputs/{fixture_id}/run-N.txt`. Everything is optional — no block, some evals,
some runs. Partial coverage is the normal state.

The report gains a Judge vs Human Labels table with agreement, and false passes counted separately
from false fails, because on a `safe` eval a false pass is the error that matters and one
agreement number hides it. Scored evals report mean absolute deviation from the human score.

**Labels never score your system.** They score the judge — `failure_rate` is identical whether the
labels are there or not. `fieldtest validate` checks them against your config and prints how many
runs are labeled per eval, so you can see how thin the ground truth is.

### Judging each output more than once

Temperature 0.0 reduces judge disagreement but does not eliminate it, and `stddev` on a scored
eval is the spread across different outputs *scored by a judge that was itself varying*. Two
sources of variance, summed and reported as one number attributed to your system.

Set `judge_runs` to judge each output more than once and see them separated:

```yaml
fixtures:
  directory: fixtures/
  runs: 5          # generator outputs per fixture
  judge_runs: 3    # judge repetitions per output (default 1)
```

The report gains a Judge Repeatability table: `system spread` is the variation between your
outputs, `judge spread` is the variation the judge introduced judging the same output twice, and
for binary evals `judge disagreement` is the share of outputs the judge could not decide the same
way every time.

A judge spread near zero is a well-specified eval. A judge spread comparable to the system spread
means the eval's criteria are ambiguous.

**This multiplies your bill.** `runs × judge_runs × llm evals × fixtures` judge calls;
`fieldtest validate` prints the projection for the full set so you meet the number before paying
it. `failure_rate` is computed from collapsed verdicts (majority, ties resolved to fail), so it
always counts outputs rather than judge calls. The *unit* is stable; the number is not. Ties go
to fail, so an even `judge_runs` is stricter than an odd one — at a true 0.9 pass rate the
reported failure rate is 0.100 at `judge_runs: 1`, 0.190 at 2, and 0.028 at 3. Change
`judge_runs` and you have changed the instrument, not the system: compare runs that share a
setting, and read a jump across a change as an artefact until proven otherwise.

### Judge retries

Judge errors do not fail a run. They are excluded from the failure rate and counted separately,
which means an overloaded provider quietly shrinks your sample instead of telling you. Every
provider therefore shares one retry policy, and any run with errors says so in the report header
and marks the affected evals in the per-eval table.

```yaml
defaults:
  judge_retry:
    max_attempts: 6                   # retries after the first call
    initial_delay: 5.0                # seconds
    max_delay: 60.0
    multiplier: 2.0                   # default schedule: 5, 10, 20, 40, 60, 60
```

Retried: HTTP 429, 500, 502, 503, 504, 529, and the SDK connection and timeout errors.
Not retried: a missing package or API key, an authentication failure, an unknown model, and a
judge response that is not valid JSON — none of which a second attempt can fix.

---

## How it works

fieldtest expects your project to have an `evals/` directory with a `config.yaml` file. All commands default to `evals/config.yaml` relative to your working directory. Use `--config <path>` to override.

```
your-project/
  evals/
    config.yaml        ← fieldtest reads this
    fixtures/          ← your test inputs
    outputs/           ← your generator writes here
    results/           ← fieldtest score writes here
```

Run all fieldtest commands from your project root (the directory that contains `evals/`).

---

## Quickstart

### 1. Scaffold your eval directory

```bash
fieldtest init
```

This creates:

```
evals/
  config.yaml              ← fill this out first
  fixtures/
    golden/                ← fixtures with expected output (used for regression)
    variations/            ← fixtures without expected output
  outputs/                 ← your generator writes here (git-ignored)
  results/                 ← fieldtest score writes here
  .gitignore               ← outputs/ excluded from git
```

Use `--template` to start from a pre-filled config based on one of the demo examples:

```bash
fieldtest init --template email       # support email response config
fieldtest init --template rag         # RAG / Q&A config
fieldtest init --template chatbot     # conversational assistant config
```

Templates include all required sections with realistic evals already written — except each eval's `tag`, which ships blank on purpose: decide whether it is `right`, `good`, or `safe` before the config validates. Then swap in your system prompt and fixtures.

### 2. Fill out config.yaml

The config walks you through the reasoning in order. Here's a complete example for a resume tailoring assistant:

```yaml
# evals/config.yaml
schema_version: 1

system:
  name: Resume tailoring assistant
  domain: >
    English-language resumes tailored to job descriptions.
    Input: plain-text base resume + job description.
    Output: Markdown resume tailored to the specific role.

use_cases:
  - id: tailor_resume
    description: >
      User submits a base resume and job description.
      System returns a Markdown resume tailored to the role.

    evals:

      # RIGHT — correctness evals
      # Failure → grounding or reasoning problem in your system

      - id: no_fabrication
        tag: right
        type: llm
        description: Output does not invent facts not present in the source
        pass_criteria: >
          Every company name, date, metric, and credential in the output
          can be traced to the source material. Minor rephrasing is fine.
        fail_criteria: >
          The output contains a company, date, metric, or credential that
          does not appear in the source material.

      - id: contact_preserved
        tag: right
        type: rule
        description: Name and email in output match the base resume

      - id: golden_regression
        tag: right
        type: reference
        description: Output contains the fixture's expected strings

      # GOOD — quality evals
      # Failure → prompt engineering or format problem; iterate instructions

      - id: format_compliance
        tag: good
        type: rule
        description: Output follows required Markdown structure

      - id: bullet_quality
        tag: good
        type: llm
        description: Bullets are specific, quantified, and free of filler language
        pass_criteria: >
          Bullets begin with action verbs, are specific, include quantified
          results where the source provides data, and contain no filler phrases
          (responsible for, helped with, worked on).
        fail_criteria: >
          Bullets are vague, omit available quantification, or use filler phrases.

      # SAFE — guardrail evals
      # Failure → architectural problem; structural fix, not prompt iteration

      - id: no_preamble
        tag: safe
        type: regex
        description: Output starts with the resume, not commentary
        pattern: "^# "
        match: true

      - id: no_horizontal_rules
        tag: safe
        type: regex
        description: No --- in output (forbidden by format spec)
        pattern: "(?m)^---$"
        match: false

    fixtures:
      directory: fixtures/
      sets:
        smoke:
          # A few fixtures covering each eval type.
          # Run after any prompt change for fast signal.
          - experienced-swe__senior-swe
          - recent-grad__data-scientist
          - marketing-manager__product-manager
        regression:
          # Golden fixtures only — deterministic reference + rule + regex evals.
          # No LLM judge cost. Use this in CI on every PR.
          - experienced-swe__senior-swe
          - recent-grad__senior-swe
        full: all   # everything — run before releases
      runs: 3       # how many times to run each fixture

defaults:
  provider: anthropic
  model: claude-haiku-4-5   # judge model — NOT your system's model
  runs: 3
```

**Sets** are just named lists of fixture IDs you define. Use whatever names make sense. `all` is a special keyword meaning every fixture in the directory.

**`providers`** is optional and only needed for a provider that names an endpoint rather than reading a well-known key from the environment. The three built-in providers need no entry:

```yaml
defaults:
  provider: openai_compatible
  model: meta-llama/llama-3.3-70b-instruct

providers:
  openai_compatible:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY   # variable name, never the key itself
```

See [Any OpenAI-compatible endpoint](#any-openai-compatible-endpoint) for the full picture, including registering your own provider.

### 3. Add fixtures

A fixture is a YAML file in `evals/fixtures/` describing one test case. The filename is the fixture ID.

**`evals/fixtures/experienced-swe__senior-swe.yaml`:**

```yaml
id: experienced-swe__senior-swe
description: >
  Experienced SWE applying to a senior SWE role — ideal match.
  Baseline fixture; should score well across all evals.

inputs:
  resume: "file:fixtures/resumes/experienced-swe.txt"
  job:    "file:fixtures/jobs/senior-swe.txt"
  is_recent_grad: false
  expected_name:  "Alex Rivera"
  expected_email: "alex.rivera@email.com"

# `file:` reads the file and passes its contents. Without the prefix the value
# is a literal string, which is what your generator may want — but an LLM judge
# would then be shown the path rather than the resume, and a grounding eval
# would be scoring a filename. Paths are relative to evals/.

# The expected block makes this a "golden" fixture.
# These are deterministic string checks — no API cost.
# Base them on actual outputs you've reviewed and accepted.
# The block is read only by evals with `type: reference` (the
# `golden_regression` eval in the step-2 config). With no reference
# eval declared, an expected block is silently ignored.
expected:
  contains:
    - "alex.rivera@email.com"
    - "Stripe"
    - "## EXPERIENCE"
    - "## EDUCATION"
  not_contains:
    - "responsible for"
    - "helped with"
    - "---"
```

A fixture without an `expected` block is a **variation fixture** — only rule, regex, and LLM evals run on it (reference evals show `—` for it in the matrix). Use variations when you don't have reviewed expected output yet. Add them to `golden/` once you've reviewed outputs and written the `expected` block.

The `inputs` block is yours to define. Whatever your generator needs — file paths, flags, metadata — put it here. Your generator reads `inputs` directly.

### 4. Write your generator

The generator is a script you write (~30 lines). It calls your system and writes outputs to `evals/outputs/[fixture-id]/run-N.txt`. fieldtest only reads those files — it never calls your system directly.

**`evals/generate.py`:**

```python
import os
import pathlib
import sys
import yaml
import anthropic

SYSTEM_PROMPT = "You are a resume tailoring assistant..."
MODEL = "claude-sonnet-5"

def tailor_resume(resume_text, job_text):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"JOB DESCRIPTION:\n{job_text}\n\nBASE RESUME:\n{resume_text}"}],
    )
    return message.content[0].text

def read_input(base_dir, value):
    # fixtures mark file inputs with a `file:` prefix (see step 3);
    # fieldtest resolves it for judges, your generator resolves it here
    return (base_dir / value.removeprefix("file:")).read_text()

def main():
    config    = yaml.safe_load(pathlib.Path("evals/config.yaml").read_text())
    set_name  = sys.argv[1] if len(sys.argv) > 1 else "full"
    base_dir  = pathlib.Path("evals")
    fixtures  = config["use_cases"][0]["fixtures"]
    # fixtures.runs wins over defaults.runs — the same precedence
    # `fieldtest score` uses when counting the run-N.txt files it expects.
    runs      = fixtures.get("runs") or config.get("defaults", {}).get("runs", 5)

    fixture_ids = fixtures["sets"][set_name]
    if fixture_ids == "all":
        fixture_ids = [p.stem for p in sorted((base_dir / "fixtures").rglob("*.yaml"))]

    for fixture_id in fixture_ids:
        fixture = yaml.safe_load((base_dir / "fixtures" / f"{fixture_id}.yaml").read_text())
        inputs  = fixture["inputs"]

        resume_text = read_input(base_dir, inputs["resume"])
        job_text    = read_input(base_dir, inputs["job"])

        out_dir = base_dir / "outputs" / fixture_id
        out_dir.mkdir(parents=True, exist_ok=True)

        for run in range(1, runs + 1):
            print(f"  {fixture_id} run {run}/{runs}...", end=" ", flush=True)
            output = tailor_resume(resume_text, job_text)
            (out_dir / f"run-{run}.txt").write_text(output)
            print("✓")

if __name__ == "__main__":
    main()
```

Run it for a specific set:

```bash
python3 evals/generate.py smoke    # run only the smoke set
python3 evals/generate.py full     # run everything
```

### 5. Register your rule evals

The config declares two `type: rule` evals — `contact_preserved` and
`format_compliance`. Each needs a matching Python function, or
`fieldtest score` stops with `No rule registered for eval 'contact_preserved'`.
Create **`evals/rules.py`**:

```python
from fieldtest import rule

@rule("contact_preserved")
def check_contact(output: str, inputs: dict) -> dict:
    header = "\n".join(output.splitlines()[:3])
    missing = [v for v in (inputs.get("expected_name", ""), inputs.get("expected_email", "")) if v and v not in header]
    if missing:
        return {"passed": False, "detail": f"{missing} not in first 3 lines"}
    return {"passed": True, "detail": "name and email present"}

@rule("format_compliance")
def check_format(output: str, inputs: dict) -> dict:
    required = ["## EXPERIENCE", "## EDUCATION"]
    missing = [s for s in required if s not in output]
    if missing:
        return {"passed": False, "detail": f"missing sections: {missing}"}
    return {"passed": True, "detail": "required sections present"}
```

Rules return `{"passed": bool, "detail": str}` — see [Eval types](#eval-types) for the full contract.

### 6. Score

```bash
fieldtest score
```

`fieldtest score` prints the full markdown report to stdout and ends with the
absolute path it wrote:

```
Results written to: /path/to/your/project/evals/results/2026-03-24T14-30-00-a3f9
```

Five files are written to `evals/results/` on every run:

```
2026-03-24T14-30-00-a3f9-data.json     full result data, machine-readable
2026-03-24T14-30-00-a3f9-data.csv      flat rows, one per fixture × eval × run
2026-03-24T14-30-00-a3f9-report.md     human report
2026-03-24T14-30-00-a3f9-report.csv    spreadsheet report
2026-03-24T14-30-00-a3f9-report.html   visual matrix report — open with fieldtest view
```

Open the HTML report:

```bash
fieldtest view          # most recent run
fieldtest view 2026-03-24T14-30-00-a3f9   # specific run
```

The HTML report is self-contained — no server, no external dependencies. It opens in your default browser and works offline. Features: color-coded fixture × eval matrix, label filter bar, click any cell to expand per-run detail with pass/fail reasoning.

The `-report.md` looks like (abridged — a real report also shows Wilson
confidence intervals on each pass rate, an `n` column, and rows for the
`golden_regression` reference eval, omitted here):

```
# Eval Report
2026-03-24 14:30 | set: full | 3 fixtures × 3 runs = 9 scored output(s) per eval

---

## tailor_resume

### Tag Health
| tag   | pass rate | passed / total |
|-------|-----------|----------------|
| RIGHT | 100%      | 18 / 18        |
| GOOD  | 89%       | 16 / 18        |
| SAFE  | 100%      | 18 / 18        |

### RIGHT
| eval              | labels | pass rate | mean | floor hits | errors | vs prior |
|-------------------|--------|-----------|------|-----------|--------|---------|
| no_fabrication    | —      | 100%      | —    | 0          | 0      | ↔        |
| contact_preserved | —      | 100%      | —    | 0          | 0      | ↔        |

### GOOD
| eval              | labels | pass rate | mean | floor hits | errors | vs prior |
|-------------------|--------|-----------|------|-----------|--------|---------|
| format_compliance | —      | 100%      | —    | 0          | 0      | ↔        |
| bullet_quality    | —      | 78%       | —    | 0          | 0      | +3%      |

### SAFE
| eval                | labels | pass rate | mean | floor hits | errors | vs prior |
|---------------------|--------|-----------|------|-----------|--------|---------|
| no_preamble         | —      | 100%      | —    | 0          | 0      | ↔        |
| no_horizontal_rules | —      | 100%      | —    | 0          | 0      | ↔        |

### Fixture × Eval Matrix
| fixture                     | no_fabrication | contact_preserved | format_compliance | bullet_quality | no_preamble | no_horizontal_rules |
| ---                         | ---            | ---               | ---               | ---            | ---         | ---                 |
| experienced-swe__senior-swe        | 3/3     | 3/3               | 3/3               | 3/3            | 3/3         | 3/3                 |
| recent-grad__data-scientist        | 3/3     | 3/3               | 3/3               | 2/3            | 3/3         | 3/3                 |
| marketing-manager__product-manager | 3/3     | 3/3               | 3/3               | 2/3            | 3/3         | 3/3                 |

### Failure Details

**bullet_quality**
- `recent-grad__data-scientist` run 2: Bullets omit available quantification from source
- `marketing-manager__product-manager` run 1: "Responsible for managing" — filler phrase present
```

**The tool reports distributions. You decide what's a regression.** `bullet_quality` failing on 2 of 9 runs might be acceptable or might need a prompt fix — you know your system's risk tolerance; the tool doesn't.

---

## Labels

Every eval accepts an optional `labels` field — a list of free-form strings for analytics grouping and filtering:

```yaml
- id: no_fabrication
  tag: right
  labels: [accuracy, content-safety]   # optional; multiple allowed
  type: llm
  ...
```

Labels flow through to JSON, CSV, the markdown report, and the HTML report's filter bar. Use them to group evals by feature area, severity, or phase — any grouping that's useful for your analytics. Labels are additive to `tag` (which is the diagnostic lens); labels are for organizing and filtering.

Click a label chip in the HTML report to filter the matrix to only evals with that label. Useful when a suite has many evals and you want to focus on one category.

---

## CLI Reference

### `fieldtest demo`

Scaffold a working eval example and run it immediately. Two commands from install to a live scored report.

```bash
fieldtest demo                               # email example, live scoring (requires API key)
fieldtest demo --offline                     # pre-scored results, no API key required
fieldtest demo --example rag                 # choose example: email | rag | extraction
fieldtest demo --example extraction          # rule+regex evals only — works without API key
fieldtest demo --dir my-demo                 # scaffold to a custom directory
```

The demo scaffolds a complete eval directory with config, fixtures, pre-built outputs, and rules. After it runs, everything is editable — try changing outputs, adding fixtures, running `fieldtest score` again.

**Offline mode** (`--offline`) uses pre-scored results bundled with the package — no API calls, no credentials, runs in under 2 seconds. The full HTML report is generated from the bundled results and works with `fieldtest view`.

---

### `fieldtest view`

Open the HTML eval report in your default browser.

```bash
fieldtest view                               # most recent run
fieldtest view 2026-03-24T14-30-00-a3f9     # specific run by ID
fieldtest view --config path/to/config.yaml  # custom config location
```

The HTML report is self-contained — single file, all CSS and JS inline, no server, no external dependencies, works fully offline. Features:

- **Tag health cards** — RIGHT / GOOD / SAFE pass rates at a glance
- **Label filter bar** — click a label chip to filter the matrix to evals with that label
- **Fixture × eval matrix** — color-coded cells: green = all pass, red = any fail, yellow = judge error
- **Cell expansion** — click any colored cell to see per-run PASS/FAIL with judge reasoning or detail text

---

### `fieldtest validate`

Check that your config is valid before running anything.

```bash
fieldtest validate
fieldtest validate --config path/to/config.yaml
```

```
✓ config valid: evals/config.yaml
  1 use case(s), 6 eval(s)
  by tag — right: 2, good: 2, safe: 2
  1 explicitly listed fixture(s)
  provider 'anthropic' — ANTHROPIC_API_KEY set
  ≈ 27 judge call(s) for the 'full' set

  human labels:
    addresses-the-ask: 3 labeled run(s)
    no-unauthorized-commitments: 3 labeled run(s)
```

The provider line names every provider the config reaches — defaults, per-eval
overrides and calibration panel — and whether its credential is present. The
judge-call projection is multiplicative (`runs × judge_runs × llm evals ×
fixtures`), so it is worth reading before a run rather than after the bill.

On error:

```
Config error at use_cases -> 0 -> evals -> 0: Value error, pass_criteria required for type: llm binary
```

---

### `fieldtest dataset`

```bash
fieldtest dataset list                              # what is bundled
fieldtest dataset use expense-report                # copy into ./evals
fieldtest dataset use expense-report --dest sandbox # copy elsewhere
```

Copies rather than references, because the point is to edit it. Refuses to
overwrite a non-empty destination unless you pass `--force`; `results/` is not
copied, since results belong to whoever runs it.

### `fieldtest score`

Score all fixtures in the `full` set (the default).

```bash
fieldtest score
fieldtest score --set smoke        # fast subset
fieldtest score --set regression   # golden fixtures only
fieldtest score --config path/to/config.yaml
```

**Sets** are defined in your config under `fixtures.sets`. There's nothing special about the names `smoke`, `regression`, or `full` — use whatever names fit your workflow. The only special value is `all`, which means every fixture in the directory.

```yaml
fixtures:
  sets:
    smoke:      [fixture-a, fixture-b]   # named list of fixture IDs
    regression: golden/*                 # all fixtures in a subdirectory
    full:       all                      # every fixture in fixtures/
```

**Golden fixtures** are just fixtures with an `expected` block. The `regression` set conventionally contains these — but "golden" and "regression" are just conventions, not enforced by the tool. What makes a fixture golden is whether it has `expected.contains` or `expected.not_contains` entries, not which set it's in.

---

### `fieldtest score --allow-partial`

By default, `fieldtest score` exits with an error if any expected output file is missing. Use `--allow-partial` to skip missing outputs and continue scoring what exists.

```bash
fieldtest score --allow-partial
```

The printed report's header flags the run and names what was skipped:

```
# Eval Report
2026-03-24 14:30 | set: full | 3 fixtures × 3 runs (PARTIAL — 2 outputs missing, skipped)
⚠ partial results: recent-grad__data-scientist run 2, recent-grad__data-scientist run 3 not found — excluded from rates
```

Skipped runs are excluded from failure rates — they don't count as passes or failures. The report header flags the run as partial so you know the rates are based on incomplete data. All available outputs are still scored normally.

Use this when you're iterating on evals and don't have complete generated outputs yet, or when a generator run partially failed.

---

### `fieldtest score --concurrency 1`

By default fieldtest dispatches judge calls in parallel (5 threads) and prints the full report only at the end. `--concurrency 1` runs judges sequentially and prints each result as it completes — useful when debugging a judge error.

```bash
fieldtest score --concurrency 1
```

```
  no_fabrication                 experienced-swe__senior-swe  run 1  ✓ pass
  no_fabrication                 experienced-swe__senior-swe  run 2  ✓ pass
  no_fabrication                 experienced-swe__senior-swe  run 3  ✓ pass
  contact_preserved              experienced-swe__senior-swe  run 1  ✓ pass
  bullet_quality                 recent-grad__data-scientist  run 1  ✗ fail
  bullet_quality                 recent-grad__data-scientist  run 2  ✓ pass
  no_fabrication                 marketing-manager__product-manager  run 1  ⚠ error
  ...
```

When a judge is erroring (API failure, malformed response), `--concurrency 1` shows you exactly which fixture and run is triggering it. With parallel execution the errors surface only in the final report, mixed with everything else.

---

### `fieldtest help`

```bash
fieldtest help              # the command list
fieldtest help calibrate    # one command's options
fieldtest calibrate --help  # the same thing
fieldtest --help calibrate  # also the same thing
```

All three forms for a single command are equivalent. `fieldtest --help calibrate` used to print the general help and drop the command name without saying so, which is worse than an error because it looks like an answer. An unrecognised name now exits 2 and lists what exists.

### `fieldtest calibrate`

Score the same outputs with several judges and report how much they agree.

```bash
fieldtest calibrate                 # the panel in config, over the 'full' set
fieldtest calibrate smoke           # a named set
fieldtest calibrate --dry-run       # projected call count, calls nothing
fieldtest calibrate --concurrency 1 # serialise the judge calls
```

```
--config PATH          Path to config.yaml (default: evals/config.yaml)
--dry-run              Print the projected call count and exit without calling anything
--concurrency INTEGER  Max parallel judge calls (default: 5)
```

Needs a `calibration.panel` in config with at least two distinct judges. Rescoring an existing `outputs/` directory costs one extra pass per judge and no generation, so `--dry-run` first is a habit worth having — the bill is `judges × runs × judge_runs × llm evals × fixtures`.

Panel results are not written as a baseline: a panel member's pass is a measurement of the judge, not of your system, so it must never reach `find_baseline()`.

See [Measuring the judge itself](#measuring-the-judge-itself--fieldtest-calibrate) for what the report contains.

### `fieldtest history`

List all past runs, newest first, with tag-level failure rates.

```bash
fieldtest history
```

```
RUN ID                      TIMESTAMP           SET           FIXTURES    JUDGE                         RIGHT     GOOD      SAFE
2026-03-24T14-30-00-a3f9    2026-03-24 14:30    full          11          claude-haiku-4-5              0%        9%        0%
2026-03-24T11-31-00-da96    2026-03-24 11:31    full          11          claude-haiku-4-5              0%        18%       0%
2026-03-23T18-52-00-79fb    2026-03-23 18:52    smoke         6           claude-haiku-4-5              0%        12%       0%
```

The rates shown are **pass** rates per tag, pooled over outputs — the same figure the run's own Tag Health table gives. They used to be average failure rates, under the same RIGHT / GOOD / SAFE headings the report uses for pass rates, so `history` said 12% where the report for that run said 95%. Use this to spot when a change improved or hurt a whole category. Open the `-report.md` or run `fieldtest view [run-id]` for the specific run to see which evals moved.

---

### `fieldtest diff`

Compare two runs. Default: most recent vs prior (same set).

```bash
fieldtest diff                                           # most recent vs prior
fieldtest diff 2026-03-24T14-30-00-a3f9                 # specific run vs its prior
fieldtest diff 2026-03-24T14-30-00-a3f9 \
  --baseline 2026-03-23T18-52-00-79fb                   # explicit comparison
```

```
Comparing: 2026-03-24T14-30-00-a3f9
Baseline:  2026-03-23T18-52-00-79fb

Increased:
  bullet_quality: 0.090 → 0.180 (+0.090)

Decreased:
  education_placement: 0.240 → 0.180 (-0.060)

Unchanged: no_fabrication, contact_preserved, format_compliance, no_preamble, no_horizontal_rules
```

Deltas use neutral language — "increased" means the failure rate went up, "decreased" means it went down. You decide if a change is a regression. A decrease in `education_placement` failure rate after a prompt fix is expected. An increase in `no_fabrication` is always worth investigating.

**Dataset versioning.** When the fixture set itself changes, a delta against runs from the old set measures the dataset, not the system. Tag the snapshot:

```yaml
use_cases:
  - id: tailor_resume
    fixtures:
      version: "2026-03"   # optional dataset snapshot tag
```

The tag is recorded in every run's `-data.json` as `dataset_version`. The automatic baseline lookup skips runs from a different version, and an explicit `--baseline` that crosses versions gets a warning. Configs that omit `version` are treated as unversioned — no filtering, no warning.

---

### `fieldtest clean`

Remove accumulated run artifacts.

```bash
# Interactive — shows what would be removed, asks to confirm
fieldtest clean

# Clear outputs/ (your generator's output files)
fieldtest clean --outputs

# Prune old results, keeping the 10 most recent
fieldtest clean --results --keep 10

# Both
fieldtest clean --outputs --results --keep 5
```

Interactive mode names every file it will delete, then asks:

```
Would remove:
  outputs/ — 9 file(s), and the directory's contents:
    outputs/june-trip/run-1.txt
    outputs/june-trip/run-2.txt
    outputs/march-trip/run-1.txt
    … and 6 more
  results/ — 15 file(s) from old runs (keeping 20):
    results/2026-01-02T10-00-00-aaaa-data.json
    … and 14 more
Proceed? [y/N]:
```

`--outputs` clears the whole `outputs/` directory, not only the `run-N.txt`
files fieldtest wrote — anything you put there goes too, and `fieldtest init`
gitignores it, so it is not recoverable. `--results` removes only the five
artifacts of each pruned run; a file of your own named after a run id is left
alone.

`clean` refuses to run unless it is looking at a valid fieldtest config. A bare
`config.yaml` beside an `outputs/` directory describes most projects, and
deleting one of those was not worth the convenience.

Only what's listed in the prompt gets removed. If only results need pruning, outputs are untouched.

`--keep` defaults to 20. Each result set is 5 files (`-data.json`, `-data.csv`, `-report.md`, `-report.csv`, `-report.html`); all five are removed together when pruning.

---

### `fieldtest init`

Scaffold the eval directory structure in your project. Safe to run in an existing project — won't overwrite files unless you pass `--force`.

```bash
fieldtest init                          # creates evals/ in current directory
fieldtest init --template email         # pre-filled email support template
fieldtest init --template rag           # pre-filled RAG / Q&A template
fieldtest init --template chatbot       # pre-filled conversational assistant template
fieldtest init --dir ci/evals           # custom location
fieldtest init --force                  # overwrite existing files
```

```
✓ Scaffolded eval structure at evals/
  evals/config.yaml       — fill this out first
  evals/fixtures/golden/  — fixtures with expected outputs
  evals/fixtures/variations/ — fixtures without expected outputs
  evals/.gitignore        — outputs/ excluded from git

Next steps:
  1. Edit evals/config.yaml
  2. Add fixtures to evals/fixtures/
  3. Run your system → write outputs to evals/outputs/
  4. fieldtest score
```

---

## Right / Good / Safe

Every eval requires a `tag`. The tag is the diagnostic path when something fails.

| tag | what it means | failure → |
|-----|--------------|-----------|
| `right` | correctness — did the system do the correct thing? | grounding, retrieval, or reasoning fix |
| `good` | quality — did the system do it well? | prompt engineering or format fix |
| `safe` | guardrails — did the system violate a hard constraint? | architectural fix, not prompt iteration |

A single quality score hides which category failed. `right` and `safe` failures have completely different fixes — one is a reasoning problem, one is a structural problem. Tagging forces you to classify before you measure.

---

## Eval types

| type | when to use | example |
|------|-------------|---------|
| `rule` | deterministic Python logic; can read fixture `inputs` | contact info check, section ordering |
| `regex` | pattern matching; `match: true` = must match, `match: false` = must not match | forbidden strings, required format |
| `llm` | semantic judgment that requires reading the output; Pass/Fail by default, or a scored scale with `binary: false` | fabrication, quality, keyword alignment |
| `reference` | compare against `expected` block in fixture file | golden output regression check |

Writing rules:

```python
# evals/rules.py
from fieldtest import rule

@rule("contact_preserved")
def check_contact(output: str, inputs: dict) -> dict:
    name  = inputs.get("expected_name", "")
    email = inputs.get("expected_email", "")
    header = "\n".join(output.splitlines()[:3])
    if name and name not in header:
        return {"passed": False, "detail": f"'{name}' not in first 3 lines"}
    if email and email not in header:
        return {"passed": False, "detail": f"'{email}' not in first 3 lines"}
    return {"passed": True, "detail": "name and email present"}
```

Rules always return `{"passed": bool, "detail": str}`. The detail is shown in the HTML report when you click a cell — make it informative on both pass and fail.

### Scored LLM evals (`binary: false`)

An `llm` eval returns a Pass/Fail verdict by default. Set `binary: false` for a scored eval: the judge rates each output on an integer `scale`, with `anchors` saying what the points mean.

```yaml
- id: explanation_clarity
  tag: good
  type: llm
  binary: false            # a number, not a verdict
  description: How clearly the reductions are explained
  scale: [1, 5]            # [min, max]
  anchors:                 # what the points mean
    1: No explanation, or one that does not say why an amount changed.
    3: States what was reduced, but the reader still has to check the policy.
    5: States what was reduced, by how much, and why.
```

A scored eval reports a mean, a stddev, and a count of floor hits (outputs at the bottom of the scale) instead of a failure rate — the distribution rather than the verdict. `scale` and `anchors` are required when `binary: false`; `pass_criteria` and `fail_criteria` are required when it is binary (the default).

### Few-shot examples for the judge

A binary `llm` eval can carry `examples` — labelled outputs rendered into the judge prompt, useful for pinning down a criterion the judge keeps reading differently than you do:

```yaml
- id: bullet_quality
  # ...
  examples:
    - output: "Led migration of 40 services to Kubernetes, cutting deploy time 70%"
      label: pass
      reasoning: Specific, quantified, starts with an action verb.
    - output: "Responsible for helping with various infrastructure tasks"
      label: fail
      reasoning: Filler phrase, no specifics, nothing quantified.
```

`examples` applies to binary evals only — a scored (`binary: false`) eval ignores it, since a pass/fail label does not map onto a scale.

---

## Two LLMs, two purposes

Your generator calls **your system**. `fieldtest score` calls its own **judge LLM**. Completely separate — different models, different credentials, different purposes.

```
YOUR SYSTEM (generator)              JUDGE (fieldtest score)
─────────────────────────────────    ──────────────────────────────────
calls your model or pipeline         calls a judge LLM to score outputs
configured by: your generator code   configured by: defaults.model in config.yaml
auth: your credentials               auth: ANTHROPIC_API_KEY in environment
```

`defaults.model` in config is the judge model. Set it independently of whatever your system uses.

---

## Results files

Five files per run, named `[run-id]-data.*` or `[run-id]-report.*`:

| file | what it is |
|------|-----------|
| `[run-id]-data.json` | Full result data — rows, summary, delta. Machine-readable, CI-parseable. |
| `[run-id]-data.csv` | Flat rows, one per fixture × eval × run. Analyst-ready. |
| `[run-id]-report.md` | Human report — tag health, per-eval tables, fixture × eval matrix, failure details. |
| `[run-id]-report.csv` | Spreadsheet report — same three views, designed to open in Excel or Numbers. |
| `[run-id]-report.html` | Visual matrix report — open with `fieldtest view`. Self-contained, works offline. |

---

## CI gating

`fieldtest score` exits 0 on success, 1 on error. It does **not** exit non-zero on high failure rates — the tool measures; you judge. It *does* exit 1 when **every** judge call errored, because that run has no rate to read: nothing was measured, and a green CI job would be reporting a measurement that never happened. A run with some errors and some verdicts still exits 0 and names the errors in the report. `fieldtest calibrate` follows the same rule. Hardcoding thresholds in the tool would convert a measurement practice into a test suite. Thresholds belong in your CI config, where they stay versioned and team-owned.

To gate CI on specific failure rates, parse the `-data.json` summary:

```bash
# Fail CI if any eval's failure rate exceeds 20%
DATA=$(ls -t evals/results/*-data.json | head -1)
WORST=$(jq '[.summary[][][].failure_rate | select(. != null)] | max // 0' "$DATA")
awk -v w="$WORST" 'BEGIN { exit (w > 0.20) }' || {
  echo "Worst eval failure rate $WORST exceeds threshold 0.20"
  exit 1
}
```

Target a specific eval:

```bash
# Fail CI if a specific eval has any failures
jq -e '.summary["uc1"]["safe"]["no-policy-invention"].failure_rate == 0' "$DATA" \
  || { echo "no-policy-invention regressed"; exit 1; }
```

Gate only on `safe` evals (looser thresholds for `right`/`good`):

```bash
jq '[.summary[].safe[].failure_rate | select(. != null)] | max // 0' "$DATA"
```

Refuse a run that did not score everything, before reading any rate off it:

```bash
jq -e '.partial != true' "$DATA" \
  || { echo "partial run: $(jq -r '.partial_details | join(", ")' "$DATA")"; exit 1; }
```

### `data.json` summary schema

The fields most commonly used for CI gating:

```json
{
  "schema_version": 2,
  "run_id": "2026-03-22T14-30-00-a3f9",
  "set": "regression",
  "dataset_version": "v2",
  "partial": false,
  "partial_details": [],
  "judge": {
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
    "temperature": 0.0,
    "seed": null,
    "overrides": {},
    "blinded_evals": [],
    "fingerprint": "a3f91c2e"
  },
  "summary": {
    "<use_case_id>": {
      "<tag>": {
        "<eval_id>": {
          "failure_rate": 0.10,
          "failure_rate_ci": [0.0347, 0.2653],
          "confidence_level": 0.95,
          "total_runs": 30,
          "error_count": 0,
          "judge_calls": 30,
          "outputs_attempted": 30,
          "floor_hits": 0,
          "mean":   3.5,
          "stddev": 0.4,
          "min": 3,
          "max": 5
        }
      }
    }
  }
}
```

- `failure_rate` is `null` for scored evals; use `mean` instead.
- One row per **fixture × eval × generator run × judge repetition**. `run` is which
  output of that fixture — `outputs/<fixture>/run-N.txt` — and `judge_run` is which
  verdict on that same output. With `runs: 3` and `judge_runs: 3` an llm eval produces
  nine rows across three outputs.
- `total_runs` counts scored **outputs**; `judge_calls` counts judge invocations. They
  differ exactly when `judge_runs > 1`.
- `failure_rate` is per output, not per row: the repetitions are collapsed to one verdict
  by majority, ties resolved to fail. That keeps the denominator in outputs at any
  `judge_runs`, but it does **not** make the rates comparable across settings. Because ties
  resolve to fail, even settings are stricter than odd ones: a true 0.9 pass rate reports 0.100
  at 1, 0.190 at 2 and 0.028 at 3. `find_baseline()` does not know this, so a `judge_runs`
  change reads as a system movement in the delta — change it in its own run and say so.
- `failure_rate_ci` is a two-sided Wilson score interval at `confidence_level`, and `null` whenever `failure_rate` is. Scored evals do not carry one — `stddev` already conveys their spread.
- `error_count` counts judge-call errors, which are **excluded** from `failure_rate`'s denominator. Gate on this separately if you want CI to fail when too many judge calls error out.
- `judge_calls` is judge calls attempted and `outputs_attempted` is outputs attempted. At `judge_runs: 1` they are equal and both equal `total_runs + error_count`; above 1 they diverge, and `failure_rate`'s denominator is `total_runs` in outputs, not calls.
- `partial` is true when `--allow-partial` skipped a missing output. The rates are then
  over a smaller population than `fixture_count × runs` implies, and `partial_details`
  names what was missing. Gate on it: a run that silently lost half its outputs otherwise
  reports whatever the survivors did. Absent in runs from before v0.3, so test `!= true`
  rather than `== false` if your gate may still meet an older file.
- `dataset_version` is optional; absent in older runs.
- `judge` records the instrument that produced the scores, with `fingerprint` a short stable hash over provider, model, temperature, seed, and per-eval overrides. Runs whose fingerprints differ are not compared automatically. Absent in runs from before v0.3. An `endpoints` key (provider name → base URL) appears inside `judge` only when the judge reached a provider configured by endpoint; with the built-in providers it is absent.
- `schema_version` is `2`. Runs written before v0.3 have no such key; treat a missing key as `1`.

**Gating on a point estimate at `runs: 5` is gating on noise.** One flipped judgment moves `failure_rate` by 0.2 on its own. `failure_rate_ci[0]` is the conservative alternative — the rate the sample can actually support:

```bash
jq '[.summary[][][].failure_rate_ci[0] | select(. != null)] | max // 0' "$DATA"
```

fieldtest reports the interval; deciding it is too wide to act on is your call, the same way thresholds live in your CI config rather than in the tool.

A complete GitHub Actions workflow (with artifact upload) is in [`examples/generate-patterns.md`](https://github.com/gmitt98/fieldtest/blob/master/examples/generate-patterns.md#ci-integration-github-actions-example).

---

## Examples and patterns

- [`examples/generate_anthropic.py`](https://github.com/gmitt98/fieldtest/blob/master/examples/generate_anthropic.py) — complete generator calling Claude directly
- [`examples/generate_openai.py`](https://github.com/gmitt98/fieldtest/blob/master/examples/generate_openai.py) — complete generator calling OpenAI
- [`examples/generate_subprocess.py`](https://github.com/gmitt98/fieldtest/blob/master/examples/generate_subprocess.py) — complete generator calling any CLI tool
- [`examples/generate-patterns.md`](https://github.com/gmitt98/fieldtest/blob/master/examples/generate-patterns.md) — sets, CI integration, scheduling, multiple generators, production traffic sampling
- [`examples/eval-patterns.md`](https://github.com/gmitt98/fieldtest/blob/master/examples/eval-patterns.md) — eval design cookbook: refusals, format compliance, forbidden content, conditional behavior, classification, and more

---

## Why upfront reasoning

Engineering teams will say: *we figure out what "good" means as we go.* This sounds like pragmatism. It isn't.

[Read the full argument →](https://github.com/gmitt98/fieldtest/blob/master/docs/philosophy.md)

---

*The practice is the point. The tool makes the practice tractable.*
