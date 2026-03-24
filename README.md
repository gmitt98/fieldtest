# fieldtest

The eval landscape is crowded at the execution layer and nearly empty at the practice layer.

Most eval tools assume you already know what to evaluate. You install a framework, run some metrics, see numbers. The numbers feel like quality. They're not. They're measurements without meaning — because nobody defined what the measurements are supposed to catch before running them.

**fieldtest is a tool for the layer that's missing: the reasoning that produces the evals.**

---

## The problem most teams skip

Before you can measure anything, you have to answer three questions for every use case your system handles:

- **What does correct look like?** Is the output grounded? Does it contain the required information? Does it refuse when it should refuse?
- **What does well-made look like?** Is it structured correctly? The right tone? Does it follow the format rules you set?
- **What must never happen?** PII exposure? Guardrail violations? Responses outside the system's scope?

These are not the same question. They have different answers, different diagnostic paths, and different fixes. A single quality score hides all of that.

Most teams skip this step. They reach for a framework, pick metrics that are easy to run, and end up measuring what's easy rather than what matters. Production keeps surprising them. They add more evals to a broken model and get a broken model with more evals.

---

## The approach

**The config structure is the practice. You can't fill it out without doing the reasoning.**

fieldtest's config asks you — in order — to name your use cases, define what right, good, and safe means for each, and specify how you'll test them. That sequence is the thing. You cannot skip to measurement without first doing the definitional work. The structure enforces it.

```yaml
schema_version: 1

system:
  name: Resume tailoring assistant
  domain: English-language resumes tailored to job descriptions

use_cases:
  - id: tailor_resume
    description: User wants resume tailored to a specific job

    evals:

      - id: no_fabrication
        tag: right          # correctness — grounding problem if this fails
        type: llm
        description: Output does not invent credentials not in the source
        pass_criteria: Every fact in the output can be traced to the source material
        fail_criteria: Output contains a credential, metric, or date not in the source

      - id: no_preamble
        tag: safe           # guardrail — architectural fix if this fails
        type: regex
        description: Output starts with content, not commentary
        pattern: "^(Here is|Sure|I've|I have|Below)"
        match: false

      - id: bullet_quality
        tag: good           # quality — prompt iteration if this fails
        type: llm
        description: Bullets are specific, quantified, and free of filler language
        pass_criteria: Bullets begin with action verbs, are specific, include quantified results
        fail_criteria: Bullets are vague, use filler phrases, or omit available data

    fixtures:
      directory: fixtures/
      sets:
        smoke:      [experienced-swe__senior-swe]
        regression: golden/*
        full:       all
      runs: 3

defaults:
  provider: anthropic
  model: claude-haiku-3-5-20251001
  runs: 3
```

---

## Right / Good / Safe

Every eval requires a tag. The tag is the diagnostic path.

| tag | what it means | failure → |
|-----|--------------|-----------|
| `right` | correctness — did the system do the correct thing? | grounding, retrieval, or reasoning fix |
| `good` | quality — did the system do it well? | prompt engineering or format fix |
| `safe` | guardrails — did the system violate a hard constraint? | architectural fix, not prompt iteration |

When something fails, the tag tells you where to look. A single quality score tells you nothing.

---

## How it works

```
config.yaml + fixtures/  →  YOUR RUNNER  →  outputs/  →  fieldtest score  →  results/
```

Three roles, clean separation:

1. **Config** — defines use cases, evals (tagged right/good/safe, typed), fixtures, run count
2. **Runner** — your code, ~30 lines. Calls your system. Writes `outputs/[fixture-id]/run-N.txt`. That's all.
3. **fieldtest score** — reads outputs, dispatches judges, aggregates distributions, writes results

The runner and eval tool share nothing except the outputs directory. You write the runner in whatever language calls your system. fieldtest handles the rest.

**Two LLMs, two purposes, no shared infrastructure:**

```
YOUR SYSTEM (runner)              JUDGE (fieldtest score)
──────────────────────────────    ──────────────────────────────────
calls your model or pipeline      calls a judge LLM to score outputs
configured by: your runner code   configured by: defaults.model
auth: your credentials            auth: ANTHROPIC_API_KEY
```

---

## Install

```bash
pip install fieldtest
```

## Quick start

```bash
# scaffold the directory structure and a starter config
fieldtest init

# write evals/config.yaml, add fixtures, write your runner
# run your system against the fixtures:
python3 evals/runner.py

# score the outputs:
fieldtest score
fieldtest score --set smoke       # fast subset
fieldtest score --set regression  # golden fixtures only (CI)
```

---

## Results

Four files per run in `results/`:

| file | what it is |
|------|-----------|
| `[run-id]-data.json` | Full result data — rows, summary, delta. Machine-readable, CI-parseable. |
| `[run-id]-data.csv` | Flat rows, one per fixture × eval × run. Analyst-ready. |
| `[run-id]-report.md` | Human report — tag health, per-eval tables, fixture matrix, failure details. |
| `[run-id]-report.csv` | Spreadsheet report — tag health, matrix, and failures in three labeled sections. |

**The tool reports distributions. You decide what's a regression.** No thresholds, no pass/fail gates in the tool. A delta on `no_fabrication` is always worth investigating. A delta on `bullet_quality` after a prompt change is expected. You know the difference; the tool doesn't.

---

## Key principles

**One eval per failure mode.** Each eval targets exactly one named thing that can fail. Narrow scope means interpretable failures. Bundled evals produce scores with no diagnostic value.

**N runs per fixture.** LLM outputs are stochastic. Run each fixture multiple times. 0% failure rate is clean. 33–66% is intermittent — stochastic problem or borderline case. 100% is systematic — the instruction is being ignored.

**Re-score without re-running.** When you change an eval, you don't need to re-run your system. The outputs on disk don't change; only the judging does. Runner re-runs cost money. Score re-runs are cheap.

**Delta vs prior run.** Every report shows movement against the previous run. You decide if a change is a regression. The tool uses neutral language — "increased"/"decreased" — because it doesn't know your risk tolerance.

**Files, not infrastructure.** No server, no dashboard, no database. Works on a personal project and at enterprise scale. The difference is where the files go.

---

## Eval types

| type | when to use |
|------|-------------|
| `rule` | deterministic Python logic; register with `@rule("id")` in `evals/rules.py` |
| `regex` | pattern matching; `match: true` = must match, `match: false` = must not match |
| `llm` | LLM-as-judge; binary pass/fail or scored (1–5 scale) |
| `reference` | compare against expected output in fixture `expected` block |

## CLI

```bash
fieldtest validate                    # check config is valid
fieldtest score                       # score all fixtures (full set)
fieldtest score --set smoke           # fast subset
fieldtest score --set regression      # golden fixtures only
fieldtest score --allow-partial       # skip missing outputs instead of failing
fieldtest score --concurrency 1       # sequential, for debugging
fieldtest history                     # list past runs
fieldtest diff                        # compare most recent vs prior
fieldtest clean --results --keep 10   # prune old results
fieldtest init                        # scaffold new project
```

---

## Examples and patterns

- `examples/runner_anthropic.py` — runner calling Claude directly
- `examples/runner_openai.py` — runner calling OpenAI
- `examples/runner_subprocess.py` — runner calling any CLI tool
- `examples/runner-patterns.md` — sets, CI integration, cron/on-demand triggers, multiple runners, production traffic sampling
- `examples/eval-patterns.md` — eval design cookbook: refusals, format compliance, forbidden content, conditional behavior, classification, and more

---

*The practice is the point. The tool makes the practice tractable.*
