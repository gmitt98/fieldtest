# expense-report

A dataset to write evals against.

The artifacts are done: a prompt, the source documents it was given, and nine
outputs as though a generator had just written them. Writing the evals is the
part left for you.

```bash
fieldtest score --set full          # works with no API key
```

Four evals are filled in, one per tag plus a reference eval, and all four are
deterministic — so the first run works before you have written anything or set
a key. The rest are `TODO` in `config.yaml` and `rules.py`.

## The system

An expense assistant. It is given `sources/travel-policy.md` and one receipt
CSV, and asked (in `PROMPT.md`) for a line-item table, a section naming every
reduction with its policy reason, and a total.

The prompt is a fixture input, not just documentation. That is what makes
"did it do what was asked" an eval you can write.

## What is in the outputs

Nine outputs across three trips. Three are clean. The rest each carry one fault:

| Output | Fault | Cheapest judge that finds it |
|---|---|---|
| `october-trip/run-2` | meals cap not applied; arithmetic self-consistent | `llm` |
| `october-trip/run-3` | invented receipt R-1049; total does not match its rows | `rule` |
| `march-trip/run-2` | `$[TOTAL]` placeholder left in | `regex` |
| `march-trip/run-3` | no reductions section, no heading | `llm` |
| `june-trip/run-2` | alcohol reimbursed, against policy 4.3 | `rule` |
| `june-trip/run-3` | correct numbers, no explanation of the exclusion | `llm` |

Two of those are catchable with no API call at all. Reach for an LLM judge when
the question needs judgment, not when it needs arithmetic.

## Your turn

`config.yaml` has three `TODO` evals with the question each one has to answer.
`rules.py` has a sketch for the invented-receipt rule.

The one worth doing first: `october-trip/run-2` reimburses a $91.40 meal
against a $75 cap, and its total adds up correctly. A rule that checks the
arithmetic passes it. Deciding what kind of eval catches that — and which tag
it belongs under — is the exercise.

## The answer key, and what it gets wrong

```bash
fieldtest score --config reference-evals.yaml --set full
```

Seven evals covering all five judge types. Compare it to yours after you have
written something, not before.

It is worth reading for its failures as much as its passes. `caps_applied` is
scoped in its own `fail_criteria` to judge the two daily caps and ignore
everything else. Run it and the judge still fails outputs for reimbursing
alcohol and for citing an invented receipt — both real defects, neither a cap.

The fixtures carry human labels recording what those verdicts should be, so the
report says so out loud:

```
| eval                               | labeled runs | agreement | errors                     |
| total_matches_line_items           | 3            | 100.0%    | 0 false pass, 0 false fail |
| excluded_categories_not_reimbursed | 3            | 100.0%    | 0 false pass, 0 false fail |
| caps_applied                       | 6            | 66.7%     | 0 false pass, 2 false fail |
```

The two rule evals agree with a human every time. The LLM eval, given explicit
written scope, does not. That is not a flaw in this dataset — it is the reason
`fieldtest calibrate` and human labels exist, reproducible on nine files you
can read in a minute.
