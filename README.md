# fieldtest

The eval landscape is crowded at the execution layer and nearly empty at the practice layer.

Most eval tools assume you already know what to evaluate. You install a framework, run some metrics, see numbers. The numbers feel like quality yet they're not: they are measurements without meaning, because nobody defined what the measurements are supposed to catch before running them.

**fieldtest is a tool for the layer that's missing: the reasoning that produces the evals.**

The config asks you — in order — to name your use cases, define what right, good, and safe means for each, and specify how you'll test them. That sequence is the thing most teams skip, which is why they end up with evals that measure what's easy rather than what matters. The structure of the testing enforces the reasoning.

---

## See it in 30 seconds

No API key needed. No setup. Just install and run:

```bash
pip install fieldtest
fieldtest demo --offline
```

You'll see a full scored eval report in the terminal — tag health across RIGHT / GOOD / SAFE, a fixture × eval matrix, and specific failure details. Then open the visual HTML report:

```bash
fieldtest view
```

This opens a self-contained HTML report in your browser: color-coded matrix, label filter bar, click any cell to see per-run pass/fail detail.

That's it. You just ran a structured eval suite with four eval types (rule, regex, LLM, reference), right/good/safe tags, and failure analysis — in two commands, no API key, no configuration.

---

## Three demo modes

### Mode 1 — Offline (no API key, instant)

```bash
fieldtest demo --offline
fieldtest view
```

Uses pre-scored results bundled with the package. Runs in under 2 seconds. Good for quick demos, job interviews, or machines without credentials set.

### Mode 2 — Live extraction (no API key, real scoring)

```bash
fieldtest demo --example extraction
fieldtest view
```

Runs real `fieldtest score` on the extraction example. Rule and regex evals execute fully. LLM evals are gracefully skipped (marked as errors, excluded from rates) since no API key is present. Shows how the tool handles partial eval coverage cleanly.

### Mode 3 — Full live run (requires `ANTHROPIC_API_KEY`)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
fieldtest demo                      # email example (default)
fieldtest demo --example rag
fieldtest demo --example extraction
fieldtest view
```

Runs all four eval types including LLM judges. Each example uses `claude-haiku-3-5-20251001` as the judge model by default (fast, cheap). Every example has at least one intentional failure so you can see how regressions surface in the report.

### Available examples

| Example | System | What it demonstrates |
|---------|--------|----------------------|
| `email` | Clearbook Support Assistant | LLM judge (tone, policy compliance), rule (greeting check), regex (forbidden terms), reference (golden fixture) |
| `rag` | Meridian Handbook Assistant | RAG grounding eval, hallucination detection, answer-length rule, citation regex |
| `extraction` | Invoice Data Extractor | JSON structure rules, field-presence rules, regex forbidden-field check — runs fully without an API key |

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
fieldtest init --template extraction  # structured extraction config
```

Templates include all required sections with realistic evals already written. Swap in your system prompt and fixtures.

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
  model: claude-haiku-3-5-20251001   # judge model — NOT your system's model
  runs: 3
```

**Sets** are just named lists of fixture IDs you define. Use whatever names make sense. `all` is a special keyword meaning every fixture in the directory.

### 3. Add fixtures

A fixture is a YAML file in `evals/fixtures/` describing one test case. The filename is the fixture ID.

**`evals/fixtures/experienced-swe__senior-swe.yaml`:**

```yaml
id: experienced-swe__senior-swe
description: >
  Experienced SWE applying to a senior SWE role — ideal match.
  Baseline fixture; should score well across all evals.

inputs:
  resume: fixtures/resumes/experienced-swe.txt
  job:    fixtures/jobs/senior-swe.txt
  is_recent_grad: false
  expected_name:  "Alex Rivera"
  expected_email: "alex.rivera@email.com"

# The expected block makes this a "golden" fixture.
# These are deterministic string checks — no API cost.
# Base them on actual outputs you've reviewed and accepted.
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

A fixture without an `expected` block is a **variation fixture** — only rule, regex, and LLM evals run on it. Use variations when you don't have reviewed expected output yet. Add them to `golden/` once you've reviewed outputs and written the `expected` block.

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
MODEL = "claude-sonnet-4-20250514"

def tailor_resume(resume_text, job_text):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": job_text}],
    )
    return message.content[0].text

def main():
    config    = yaml.safe_load(pathlib.Path("evals/config.yaml").read_text())
    set_name  = sys.argv[1] if len(sys.argv) > 1 else "full"
    base_dir  = pathlib.Path("evals")
    runs      = config["defaults"]["runs"]

    fixture_ids = config["use_cases"][0]["fixtures"]["sets"][set_name]
    if fixture_ids == "all":
        fixture_ids = [p.stem for p in sorted((base_dir / "fixtures").rglob("*.yaml"))]

    for fixture_id in fixture_ids:
        fixture = yaml.safe_load((base_dir / "fixtures" / f"{fixture_id}.yaml").read_text())
        inputs  = fixture["inputs"]

        resume_text = (base_dir / inputs["resume"]).read_text()
        job_text    = (base_dir / inputs["job"]).read_text()

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

### 5. Score

```bash
fieldtest score
```

Output:

```
scoring tailor_resume: 3 fixtures × 3 runs = 9 evaluations per eval
✓ results written to evals/results/2026-03-24T14-30-00-a3f9
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

The `-report.md` looks like:

```
# Eval Report
2026-03-24 14:30 | set: full | 3 fixtures × 3 runs = 9 evaluations per eval

---

## tailor_resume

### Tag Health
| tag   | pass rate | passed / total |
|-------|-----------|----------------|
| RIGHT | 100%      | 18 / 18        |
| GOOD  | 91%       | 33 / 36        |
| SAFE  | 100%      | 54 / 54        |

### RIGHT
| eval              | labels | pass rate | mean | floor hits | errors | vs prior |
|-------------------|--------|-----------|------|-----------|--------|---------|
| no_fabrication    | —      | 100%      | —    | 0          | 0      | ↔        |
| contact_preserved | —      | 100%      | —    | 0          | 0      | ↔        |

### GOOD
| eval              | labels | pass rate | mean | floor hits | errors | vs prior |
|-------------------|--------|-----------|------|-----------|--------|---------|
| format_compliance | —      | 100%      | —    | 0          | 0      | ↔        |
| bullet_quality    | —      | 91%       | —    | 0          | 0      | +3%      |

### SAFE
| eval                | labels | pass rate | mean | floor hits | errors | vs prior |
|---------------------|--------|-----------|------|-----------|--------|---------|
| no_preamble         | —      | 100%      | —    | 0          | 0      | ↔        |
| no_horizontal_rules | —      | 100%      | —    | 0          | 0      | ↔        |

### Fixture × Eval Matrix
| fixture                     | no_fabrication | contact_preserved | format_compliance | bullet_quality | no_preamble | no_horizontal_rules |
| ---                         | ---            | ---               | ---               | ---            | ---         | ---                 |
| experienced-swe__senior-swe | 3/3            | 3/3               | 3/3               | 3/3            | 3/3         | 3/3                 |
| recent-grad__data-scientist | 3/3            | 3/3               | 3/3               | 2/3            | 3/3         | 3/3                 |
| marketing-manager__pm       | 3/3            | 3/3               | 3/3               | 2/3            | 3/3         | 3/3                 |

### Failure Details

**bullet_quality**
- `recent-grad__data-scientist` run 2: Bullets omit available quantification from source
- `marketing-manager__pm` run 1: "Responsible for managing" — filler phrase present
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
✓ config valid — 1 use case, 6 evals, 8 fixtures
```

On error:

```
Error: eval 'no_fabrication' (type: llm) missing required field: pass_criteria
```

---

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

```
⚠ partial results: recent-grad__data-scientist run 2, recent-grad__data-scientist run 3 not found — excluded from rates
scoring tailor_resume: 2 fixtures × 3 runs (PARTIAL — 2 outputs missing, skipped)
✓ results written to evals/results/2026-03-24T14-30-00-a3f9
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
  no_fabrication                 marketing-manager__pm        run 1  ⚠ error
  ...
```

When a judge is erroring (API failure, malformed response), `--concurrency 1` shows you exactly which fixture and run is triggering it. With parallel execution the errors surface only in the final report, mixed with everything else.

---

### `fieldtest history`

List all past runs, newest first, with tag-level failure rates.

```bash
fieldtest history
```

```
RUN ID                      TIMESTAMP           SET           FIXTURES    RIGHT     GOOD      SAFE
2026-03-24T14-30-00-a3f9    2026-03-24 14:30    full          11          0%        9%        0%
2026-03-24T11-31-00-da96    2026-03-24 11:31    full          11          0%        18%       0%
2026-03-23T18-52-00-79fb    2026-03-23 18:52    smoke         6           0%        12%       0%
```

The rates shown are average failure rates across all evals with that tag. Use this to spot when a change improved or hurt a whole category. Open the `-report.md` or run `fieldtest view [run-id]` for the specific run to see which evals moved.

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
  bullet_quality: 0.180 → 0.090 (+0.090)

Decreased:
  education_placement: 0.240 → 0.180 (-0.060)

Unchanged: no_fabrication, contact_preserved, format_compliance, no_preamble, no_horizontal_rules
```

Deltas use neutral language — "increased" means the failure rate went up, "decreased" means it went down. You decide if a change is a regression. A decrease in `education_placement` failure rate after a prompt fix is expected. An increase in `no_fabrication` is always worth investigating.

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

Interactive mode:

```
Would remove:
  outputs/: 33 run files
  results/: 8 old result sets (keeping 20)
Proceed? [y/N]:
```

Only what's listed in the prompt gets removed. If only results need pruning, outputs are untouched.

`--keep` defaults to 20. Each result set is 5 files (`-data.json`, `-data.csv`, `-report.md`, `-report.csv`, `-report.html`); all five are removed together when pruning.

---

### `fieldtest init`

Scaffold the eval directory structure in your project. Safe to run in an existing project — won't overwrite files unless you pass `--force`.

```bash
fieldtest init                          # creates evals/ in current directory
fieldtest init --template email         # pre-filled email support template
fieldtest init --template rag           # pre-filled RAG / Q&A template
fieldtest init --template extraction    # pre-filled structured extraction template
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
| `llm` | semantic judgment that requires reading the output | fabrication, quality, keyword alignment |
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

CI gating: `fieldtest score` exits 0 on success, 1 on error. It does not exit non-zero on high failure rates — the tool measures; you judge. To gate CI on specific failure rates, parse the `-data.json`:

```bash
python3 -c "
import json, glob, sys
f = sorted(glob.glob('evals/results/*-data.json'))[-1]
rows = json.load(open(f))['rows']
failures = [r for r in rows if r['eval_id'] == 'no_fabrication' and r.get('passed') is False]
if failures:
    print(f'no_fabrication failed on {len(failures)} runs')
    sys.exit(1)
"
```

---

## Examples and patterns

- `examples/generate_anthropic.py` — complete generator calling Claude directly
- `examples/generate_openai.py` — complete generator calling OpenAI
- `examples/generate_subprocess.py` — complete generator calling any CLI tool
- `examples/generate-patterns.md` — sets, CI integration, scheduling, multiple generators, production traffic sampling
- `examples/eval-patterns.md` — eval design cookbook: refusals, format compliance, forbidden content, conditional behavior, classification, and more

---

## Why upfront reasoning

Engineering teams will say: *we figure out what "good" means as we go.* This sounds like pragmatism. It isn't.

[Read the full argument →](docs/philosophy.md)

---

*The practice is the point. The tool makes the practice tractable.*
