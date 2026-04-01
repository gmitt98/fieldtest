# Recipe: CI Integration

**Goal:** Run evals in CI (GitHub Actions, GitLab CI, etc.) and optionally gate on results.

## Key design decision

fieldtest exits 0 on success and 1 on error. It does **not** exit non-zero for "too many failures" — that's a human judgment, not a tool decision. If you want CI gates, parse the JSON.

## Basic CI: run and report

```yaml
# .github/workflows/evals.yml
name: Evals
on: [push]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install fieldtest
      - run: python evals/runner.py regression
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: fieldtest score --set regression
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: evals/results/
```

## CI with a gate

Parse the JSON to enforce a threshold. The tool reports; your CI decides:

```bash
# gate.sh — exit 1 if any RIGHT eval has >20% failure rate
fieldtest score --set regression

LATEST=$(ls -t evals/results/*-data.json | head -1)
python3 -c "
import json, sys
data = json.load(open('$LATEST'))
for uc_id, tags in data['summary'].items():
    for eval_id, stats in tags.get('right', {}).items():
        fr = stats.get('failure_rate')
        if fr is not None and fr > 0.20:
            print(f'GATE FAIL: {eval_id} failure_rate={fr:.1%}')
            sys.exit(1)
print('All RIGHT evals within threshold.')
"
```

## Cost control

- Use `--set regression` in CI — deterministic evals only (rule, regex, reference). Zero LLM cost.
- Reserve `--set full` (with LLM judges) for nightly or pre-release runs.
- Use `--concurrency 1` if you hit rate limits in CI.

## What the report shows

CI runs produce the same four output files as local runs. Upload the `results/` directory as a build artifact for review. The HTML report is self-contained — reviewers can open it directly from the artifact.
