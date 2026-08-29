# Spec 14 — Sample datasets

**Tier** 3 · **Depends on** 13 · **Touches** `datasets/`, `judges/llm.py`, `config.py`, `cli_project.py`, README · **Status** draft

## §1 Problem

Everything fieldtest ships already has the answers filled in.

The three demos are closed loops: fixtures, outputs, six evals, rules, committed results. A reader
watches one run and learns what the output looks like. None of them leaves anything to write, and
writing the eval is the practice the tool exists to force. `philosophy.md` argues the twenty-minute
right/good/safe conversation is the product; there is currently nowhere to have it.

A user who wants to try the mechanics has to first invent a system, write a generator, produce
outputs, and only then reach the part fieldtest is about. That is a long way to walk before
learning whether `pass_criteria` is hard to write.

There is also a defect in the way. Fixture inputs reach the judge through `str(value)`, so a
fixture written the way README §3 demonstrates —

```yaml
inputs:
  resume: fixtures/resumes/experienced-swe.txt
```

— sends the judge the twenty-five character path, not the resume. Every grounding or faithfulness
eval written against it judges blind. This is the defect spec 13 fixed, reintroduced through the
pattern the README teaches in its main example, and no demo caught it because all three inline
their data.

## §2 Requirements

1. A dataset is **artifacts, not a finished suite**: the task prompt, the inputs, and outputs as
   though a generator had just written them. The user supplies the evals.
2. **Every judge type is represented.** `rule`, `regex`, `llm` binary, `llm` scored, and
   `reference`. A user should finish having written one of each, because the mechanics differ per
   type and that is what they came to learn.
3. **Failures must be reachable without an API key.** At least one planted defect must be findable
   by `rule` and one by `regex` — a total that does not match its line items, a value that appears
   nowhere in the inputs. A dataset whose only failures need an LLM judge cannot be explored
   offline and makes the cheap judge types look decorative.
4. **The prompt is an input, not a footnote.** It goes in the fixture's `inputs` so the judge
   receives it, which is what makes `does this do what was asked` expressible at all.
5. Outputs are prebundled and committed. fieldtest never runs the system under test, so a dataset
   without outputs cannot be scored, and requiring a key to get started defeats the purpose.
6. **Scaffold, plus an answer key.** The shipped config carries one worked eval per tag and a
   `TODO` for the rest naming the question to answer. A separate `reference-evals.yaml` holds a
   complete set to compare against afterwards.
7. Bundled in the wheel. The current wheel is 172K; these are text.
8. `file:`-prefixed inputs are resolved and read. Without this, requirement 1 forces every source
   document to be inlined into YAML.

## §3 Contract

### `file:` inputs

```yaml
inputs:
  prompt:   "file:prompts/expense-summary.md"
  policy:   "file:sources/travel-policy.md"
  receipts: "file:sources/receipts-oct.csv"
  employee: "Dana Okafor"          # literal, unchanged
```

Resolved relative to the config's directory. A prefix rather than a heuristic: `question: "see
notes/faq.md"` is a legitimate literal, and silently replacing it with file contents would be a
worse failure than today's. A `file:` input that does not exist is a `ConfigError` from
`fieldtest validate`, not an error at the first judge call.

`fieldtest validate` reports how many inputs resolved, so a reader can confirm the judge is
handed the document rather than the path. On the row rather than in validate was the first
sketch; validate gives the same guarantee where a user already checks their config, without
threading a flag through the dispatcher.

### Layout

```
fieldtest/datasets/expense-report/
  README.md                 what the system is, what is planted, what to write
  PROMPT.md                 the prompt that produced the outputs
  config.yaml               scaffold: one worked eval per tag, the rest TODO
  reference-evals.yaml      the answer key
  rules.py                  one worked rule; the rest TODO
  sources/                  policy, receipts — referenced with file:
  fixtures/                 inputs, and `expected` blocks for reference evals
  outputs/<fixture>/run-N.txt
```

### `fieldtest dataset`

```bash
fieldtest dataset list
fieldtest dataset use expense-report        # copies into ./evals
```

Copies rather than references, because the point is to edit it.

## §4 The dataset: `expense-report`

An assistant that reads a travel policy and a receipt list and writes a reimbursement summary.
Chosen because it produces prose to judge *and* arithmetic to check, so the cheap judge types have
real work.

Planted defects, spread across runs rather than concentrated:

| Defect | Found by |
|---|---|
| stated total does not equal the sum of line items | `rule` |
| a receipt ID that appears in no source file | `regex` / `rule` |
| an unfilled placeholder (`[amount]`, `TBD`) | `regex` |
| claims an item is reimbursable that policy caps | `llm` binary, or `rule` |
| omits the justification the prompt asked for | `llm` binary against `inputs.prompt` |
| correct but graceless phrasing | `llm` scored |
| golden fixture with known-correct output | `reference` |

Roughly a third of runs are clean, so a user sees passes as well as failures and the rates are not
all zero.

## §5 Acceptance

- `fieldtest dataset use expense-report && fieldtest score --set full` runs with **no API key** and
  reports failures, because the rule and regex evals are in the shipped scaffold.
- The shipped scaffold validates: `fieldtest validate` is clean, and reports the `TODO` evals as
  the unwritten work rather than as errors.
- `reference-evals.yaml` scores the same outputs and every planted defect is caught by at least one
  eval in it.
- A `file:` input reaches the judge as document text. Asserted by the prompt the judge receives,
  not by the loader returning a string.
- A `file:` input naming a missing file fails `fieldtest validate`.
- Every one of the five judge types appears in `reference-evals.yaml`.

## §6 Out of scope

Generating the outputs at install time, or shipping a generator. Outputs are committed artifacts;
regenerating them is a maintenance task, not a user-facing feature.

Datasets large enough to need downloading. If one is ever wanted, it argues for a fetch command,
and this spec's decision to bundle should be revisited rather than stretched.

A second dataset. Agent traces are the obvious next one — a JSON trace in `run-N.txt` is scored by
a rule eval today with no code change, verified — but one complete dataset that exercises every
judge type is worth more than two that each exercise half.
