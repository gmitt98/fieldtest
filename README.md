# fieldtest

Structured AI eval practice for any project.

You drop it into a project, write one config file, and point it at fixture data. The config asks — in order — what your system does, what right/good/safe means for each use case, and how to test it. That sequence is the practice. You can't skip to measurement without doing the thinking first.

## Install

```bash
pip install fieldtest
```

## Quick start

```bash
# scaffold the structure
fieldtest init

# fill out evals/config.yaml, add fixtures, run your system
# then score:
fieldtest score
```

## How it works

```
config.yaml + fixtures/  →  YOUR RUNNER  →  outputs/  →  fieldtest score  →  results/
```

**Three roles, clean separation:**

1. **Config** (`evals/config.yaml`) — defines use cases, evals, fixtures, run count
2. **Runner** (`evals/runner.py`) — your code. Calls your system, writes `outputs/[fixture-id]/run-N.txt`
3. **Eval tool** (`fieldtest score`) — reads outputs, dispatches judges, writes results

The runner and eval tool share nothing except the `outputs/` directory. You write the runner (~30 lines). `fieldtest` handles the rest.

## Config structure

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
        tag: right           # right | good | safe
        type: reference      # rule | regex | llm | reference
        description: Output does not contain invented credentials

      - id: no_added_pii
        tag: safe
        type: regex
        pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
        match: false
        description: Output must not contain SSN patterns

      - id: requirement_coverage
        tag: good
        type: llm
        description: Output addresses key requirements from job description
        pass_criteria: The tailored resume addresses the key requirements listed in the job description
        fail_criteria: The tailored resume ignores or misses major requirements from the job description

    fixtures:
      directory: fixtures/
      sets:
        smoke:      [experienced-swe__senior-swe]
        regression: golden/*
        full:       all
      runs: 5

defaults:
  provider: anthropic
  model: claude-haiku-3-5-20251001
  runs: 5
```

## Right / Good / Safe

Every eval has a required `tag`:

- **right** — correctness. Failure → grounding, retrieval, or reasoning problem.
- **good** — quality. Failure → prompt engineering or format problem.
- **safe** — guardrails. Failure → architectural problem, not a prompt problem.

Different tags → different diagnostic paths → different fixes.

## Eval types

| type | when to use |
|------|-------------|
| `rule` | deterministic Python logic; register with `@rule("id")` in `evals/rules.py` |
| `regex` | pattern matching; `match: true` = must match, `match: false` = must not match |
| `llm` | LLM-as-judge; binary (pass/fail) or scored (1-5 scale) |
| `reference` | compare against expected output in fixture file |

## CLI

```bash
fieldtest validate                    # check config
fieldtest score                       # score all fixtures
fieldtest score --set smoke           # fast subset
fieldtest score --set regression      # golden fixtures only
fieldtest score --allow-partial       # skip missing outputs instead of failing
fieldtest score --concurrency 1       # sequential (for debugging)
fieldtest history                     # list past runs
fieldtest diff                        # compare most recent vs prior
fieldtest clean --results --keep 10   # prune old results
fieldtest init                        # scaffold new project
```

## Results

Four files per run in `results/`:

| file | what it is |
|------|-----------|
| `[run-id]-data.json` | Full result data — rows, summary, delta. Machine-readable, CI-parseable. |
| `[run-id]-data.csv` | Flat rows, one per fixture × eval × run. Analyst-ready. |
| `[run-id]-report.md` | Human report — tag health, per-eval tables, fixture matrix, failure details. |
| `[run-id]-report.csv` | Spreadsheet report — same three views as the MD, designed to open in Excel/Numbers. |

The tool does **not** pass or fail your system. It measures distributions. You judge.

## Key principles

- **One eval per failure mode** — narrow scope = interpretable failures
- **N runs per fixture** — captures variance; distributions not single scores
- **Delta vs prior run** — regression detection without declared thresholds
- **Files, not infrastructure** — no server, no dashboard, no DB
- **Tool measures; human judges** — no thresholds in the tool

## Two LLMs, two purposes

Your runner calls **your system**. `fieldtest score` calls its own **judge LLM**. They are completely separate. `defaults.model` in config is the judge model, not your system's model.

```
YOUR SYSTEM (runner)          JUDGE (fieldtest)
────────────────────────────  ────────────────────────────────
calls your LLM/pipeline       calls a judge LLM to score outputs
configured by: your code      configured by: defaults.provider + defaults.model
auth: your credentials        auth: ANTHROPIC_API_KEY
```

## Writing rules

```python
# evals/rules.py
from fieldtest import rule

@rule("no_fabrication")
def check_no_fabrication(output: str, inputs: dict) -> dict:
    forbidden = ["invented company", "fabricated certification"]
    for f in forbidden:
        if f.lower() in output.lower():
            return {"passed": False, "detail": f"found: '{f}'"}
    return {"passed": True, "detail": "no fabrication detected"}
```

## Reference runners and patterns

- `examples/runner_anthropic.py` — calls Claude directly
- `examples/runner_openai.py` — calls OpenAI
- `examples/runner_subprocess.py` — calls any CLI tool
- `examples/eval-patterns.md` — cookbook: refusals, format compliance, forbidden content, conditional behavior, and more

## Environment

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # for judge LLM calls
```

v1 ships Anthropic only. Provider abstraction is in place for v2 (OpenAI, Azure, local models).
