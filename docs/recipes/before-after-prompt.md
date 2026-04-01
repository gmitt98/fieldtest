# Recipe: Before/After Prompt Change

**Goal:** Measure the impact of a prompt edit by comparing scored runs before and after.

## Workflow

```bash
# 1. Score the current state
python evals/runner.py
fieldtest score --set full
# Note the run ID: 2026-04-01T09-00-00-a1b2

# 2. Edit your prompt
# ... make changes to your system prompt ...

# 3. Re-generate outputs and score
python evals/runner.py
fieldtest score --set full
# Delta section automatically compares to the previous full-set run
```

## What the report shows

The delta section in the report shows:

```
Delta vs prior run (2026-04-01T09-00-00-a1b2)
| eval                | change  | before | after |
| addresses-the-ask   | +8.3%   | 75.0%  | 83.3% |
| no-hallucination    | -16.7%  | 100.0% | 83.3% |
| appropriate-tone    | ↔       | —      | —     |
```

This tells you: the prompt change improved correctness but introduced a hallucination regression. The tone eval was unchanged.

## Comparing specific runs

If auto-baseline picks the wrong run, specify it explicitly:

```bash
fieldtest score --set full --baseline evals/results/2026-04-01T09-00-00-a1b2-data.json
```

Or compare any two runs after the fact:

```bash
fieldtest diff 2026-04-01T09-00-00-a1b2 2026-04-01T10-00-00-c3d4
```

## Tips

- Always use the same fixture set for both runs
- Run N=3+ per fixture to distinguish real changes from variance
- If a SAFE eval regresses, investigate before shipping — even if RIGHT improved
- The HTML report delta section is the fastest way to see what changed
