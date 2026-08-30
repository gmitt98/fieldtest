# Walkthrough — write your first eval

Fifteen minutes, no API key, no system of your own to test.

You will install fieldtest, look at a set of artifacts it ships, run the evals
that come with them, then write one yourself and watch it catch a defect the
others miss.

Every command and every block of output below is real. Nothing is illustrative.

---

## 1. Install

```bash
pip install fieldtest
```

That is the whole install. fieldtest does not run your system, so there is no
runtime to configure and no key to set yet.

## 2. Take a dataset

```bash
fieldtest dataset list
```

```
Bundled datasets:
  expense-report — A dataset to write evals against.
  support-agent — A dataset of agent traces to write evals against.

Copy one into this project with:  fieldtest dataset use <name>
```

```bash
fieldtest dataset use expense-report
```

```
Copied 'expense-report' to evals/
  evals/README.md   what is in it and what to write
  evals/config.yaml your evals — three are TODO

Run it now (no API key needed):  fieldtest score --set full
```

It copies rather than links, because you are going to edit it.

## 3. Look at what you got

```
evals/
  README.md                      what is in it, and what is left to write
  PROMPT.md                      the prompt that produced the outputs
  sources/travel-policy.md       what the assistant was told the rules are
  sources/receipts-october.csv   the data it was given
  sources/receipts-march.csv
  sources/receipts-june.csv
  fixtures/october-trip.yaml     one test case: inputs, and your labels
  fixtures/march-trip.yaml
  fixtures/june-trip.yaml
  outputs/october-trip/run-1.txt three outputs per fixture, as though a
  outputs/october-trip/run-2.txt   generator had just written them
  outputs/october-trip/run-3.txt
  ...
  config.yaml                    your evals
  rules.py                       your Python evals
  reference-evals.yaml           the answer key, for afterwards
```

**A fixture** names the inputs for one case:

```yaml
id: october-trip
description: Reimbursement summary for Dana Okafor from receipts-october.csv

inputs:
  prompt:   "file:PROMPT.md"
  policy:   "file:sources/travel-policy.md"
  receipts: "file:sources/receipts-october.csv"
  employee: "Dana Okafor"
```

`file:` reads the file and passes its contents. Without the prefix the value is
a literal string — which matters, because an LLM judge shown
`sources/travel-policy.md` is being asked about a filename.

The prompt is an input like any other. That is what makes *did it do what was
asked* something you can write an eval for.

**An output** is whatever your generator wrote. Here, plain text:

```
## Reimbursement summary — Dana Okafor

| Receipt | Date | Category | Claimed | Reimbursable |
| R-1041 | 2026-10-03 | airfare | $412.60 | $412.60 |
| R-1042 | 2026-10-03 | ground | $38.20 | $38.20 |
| R-1043 | 2026-10-03 | lodging | $268.00 | $250.00 |
| R-1044 | 2026-10-04 | meals | $52.75 | $52.75 |
| R-1045 | 2026-10-04 | meals | $91.40 | $75.00 |
| R-1046 | 2026-10-05 | ground | $41.15 | $41.15 |
| R-1049 | 2026-10-05 | meals | $28.00 | $28.00 |
```

fieldtest never generated these. It only ever reads
`outputs/<fixture>/run-N.txt`, which is why you can start here instead of
building a runner first.

Six of the nine outputs carry a deliberate fault. Read a few before you
continue — you are about to decide what "wrong" means for them.

## 4. Run the evals that came with it

```bash
fieldtest score --set full
```

No API key needed: the four evals in the shipped config are `rule`, `regex` and
`reference`, all of which run locally.

```
### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 75% | 9 / 12 |
| GOOD | 89% | 8 / 9 |
| SAFE | 89% | 8 / 9 |

### Failure Details

**excluded_categories_not_reimbursed**
- `june-trip` run 2: R-1190 (alcohol) reimbursed $47.00

**golden_summary**
- `march-trip` run 2: missing: Total reimbursable: $98.30

**no_unfilled_placeholders**
- `march-trip` run 2: pattern '\$?\[[A-Z_]+\]' found

**total_matches_line_items**
- `march-trip` run 2: no 'Total reimbursable: $N.NN' line
- `october-trip` run 3: line items sum to $897.70, output states $912.70
```

Four evals, five failures, across three of the nine outputs. The other three
faulty outputs went unflagged — nothing that ships catches them.

## 5. Find the gap

Open `evals/config.yaml`. Under RIGHT:

```yaml
      # TODO one of the outputs cites a receipt that exists in no source file.
      #      rules.py has the sketch. Uncomment and finish:
```

Look back at `outputs/october-trip/run-3.txt`. It lists `R-1049`. Now
`sources/receipts-october.csv`:

```
R-1041,R-1042,R-1043,R-1044,R-1045,R-1046
```

There is no R-1049. The assistant invented a receipt and reimbursed $28.00 for
it.

`total_matches_line_items` did flag that output — but for the wrong reason,
because the total also fails to add up. If the model had invented a receipt
*and* added correctly, nothing would have caught it.

## 6. Write the eval

This one needs no judge. The receipts are right there in the fixture inputs, so
it is a `rule` — plain Python, no API call, no cost.

Add to `evals/rules.py`:

```python
@rule("no_invented_receipts")
def no_invented_receipts(output: str, inputs: dict) -> dict:
    """Every receipt cited must exist in the source CSV."""
    import csv
    import io

    known = {r["receipt_id"] for r in csv.DictReader(io.StringIO(inputs["receipts"]))}
    cited = {rid for rid, _ in ROW.findall(output)}
    invented = sorted(cited - known)
    return {
        "passed": not invented,
        "detail": (
            f"cites {', '.join(invented)}, which is in no source receipt"
            if invented else f"all {len(cited)} receipts exist in the source"
        ),
    }
```

`inputs["receipts"]` is the CSV text, not its path, because the fixture used
`file:`. `ROW` is already defined at the top of `rules.py`.

Write a `detail` that names the offending receipt. The report shows `detail` and
little else, and "failed" tells a reader nothing.

Then declare it in `config.yaml`, replacing the TODO:

```yaml
      - id: no_invented_receipts
        tag: right
        type: rule
        description: every receipt cited exists in the source file
```

`tag: right` because an invented receipt is a correctness fault. If you tagged
it `safe` — reimbursing money against a receipt that does not exist — that is a
defensible reading, and the tag decides which section of the report it lands in
and how loudly a failure reads.

## 7. Run it again

```bash
fieldtest score --set full
```

```
### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 81% | 17 / 21 |
| GOOD | 89% | 8 / 9 |
| SAFE | 89% | 8 / 9 |

### RIGHT
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| no_invented_receipts | — | 89% [56–98%] | 9 | — | 0 | 0 | — |
| total_matches_line_items | — | 78% [45–94%] | 9 | — | 0 | 0 | ↔ |
| golden_summary | — | 67% [21–94%] | 3 | — | 0 | 0 | ↔ |

**no_invented_receipts**
- `october-trip` run 3: cites R-1049, which is in no source receipt
```

Your eval caught it, and named the receipt.

Two things in that table worth noticing. `89% [56–98%]` is a Wilson interval:
eight passes out of nine supports a true pass rate anywhere from 56% to 98%, so
do not read 89% as precision you do not have. And `vs prior` shows `↔` for the
evals that did not move and `—` for the one that has no history — fieldtest
compared this run against the last automatically.

## 8. Score your own judgment

You decided run 3 was wrong. Record it, in `fixtures/october-trip.yaml`, under
the existing `labels:` key:

```yaml
  no_invented_receipts:
    1: pass
    2: pass
    3: fail
```

Run again:

```
### Judge vs Human Labels
| eval | labeled runs | agreement | errors |
|------|--------------|-----------|--------|
| golden_summary | 3 | 100.0% | 0 false pass, 0 false fail |
| total_matches_line_items | 9 | 100.0% | 0 false pass, 0 false fail |
| no_invented_receipts | 3 | 100.0% | 0 false pass, 0 false fail |
| no_unfilled_placeholders | 9 | 100.0% | 0 false pass, 0 false fail |
| excluded_categories_not_reimbursed | 9 | 100.0% | 0 false pass, 0 false fail |
```

100% agreement is the only honest answer for a rule that counts strings.
Labels earn their keep on LLM evals, where the judge can be confidently wrong.
False passes are counted separately from false fails, because on a `safe` eval
those are not the same mistake.

## 9. What is left

Two TODOs remain in `config.yaml`. Between them they cover the three unflagged
outputs, and both need an LLM judge:

- `october-trip/run-2` reimburses a $91.40 meal against a $75 cap. Its
  arithmetic is internally consistent, so no sum-checking rule will catch it.
- `march-trip/run-3` and `june-trip/run-3` give correct numbers and omit the
  section the prompt asked for by name.

Write those and you will need a key:

```bash
export ANTHROPIC_API_KEY=...
fieldtest score --set full
```

Then compare against the answer key:

```bash
fieldtest score --config reference-evals.yaml --set full
```

A `calibration.panel` is commented out at the bottom of that file. Uncomment it
and you can run two judges over the same outputs and see where they disagree:

```bash
fieldtest calibrate --config reference-evals.yaml --set smoke --dry-run
fieldtest calibrate --config reference-evals.yaml --set smoke
```

```
Most contested evals:
  follows_requested_structure — 50.0% disagreement
  caps_applied — 33.3% disagreement
  explanation_clarity — 0.0% disagreement
```

The evals at the top of that list are the ones whose `pass_criteria` are
ambiguous. Two models reading the same words reached different verdicts, which
is a fact about the words.

Read the answer key for its failures as much as its passes. Its `caps_applied` eval states
in its own `fail_criteria` that it should judge two daily caps and ignore
everything else, and the judge still fails outputs for unrelated defects —
66.7% agreement with a human against 100% for every rule eval. That gap is what
`fieldtest calibrate` and human labels exist to measure.

## Then your own project

```bash
fieldtest init
```

The scaffold is the same shape you have been editing. Point `outputs/` at
whatever your generator writes, and the loop is identical.

`support-agent` is the other bundled dataset: nine JSON agent traces, tool calls
and results included, where most of what matters is deterministic.
