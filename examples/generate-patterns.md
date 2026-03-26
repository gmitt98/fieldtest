# Generator Patterns

The generator is a script you own. fieldtest never calls it — it only reads the outputs
the generator writes. This separation is intentional: you control when the system runs,
on what fixtures, and how it's triggered. `fieldtest score` is always independent.

```
YOUR GENERATOR  →  outputs/[fixture-id]/run-N.txt  →  fieldtest score  →  results/
```

---

## Sets are the lever

Define sets in `config.yaml` for different scopes and costs:

```yaml
fixtures:
  sets:
    smoke:      [fixture-a, fixture-b, fixture-c]   # fast subset
    regression: golden/*                             # golden fixtures only
    full:       all                                  # everything
```

Then the generator and scorer both take a `--set` argument:

```bash
python3 evals/generate.py smoke
fieldtest score --set smoke
```

**Smoke** — fast signal. A few representative fixtures covering each eval type.
Run after every prompt change before committing to a full run.

**Regression** — golden fixtures only. Deterministic reference + rule + regex evals.
No LLM judge cost. Run in CI on every PR.

**Full** — everything. Run before releases and after production incidents.

---

## Common trigger patterns

| trigger | generate set | score | purpose |
|---------|-------------|-------|---------|
| Every PR (CI) | `regression` | yes | catch regressions cheaply — no LLM judge cost |
| After a prompt change | `smoke` → review → `full` | yes both | fast feedback before full regeneration |
| Nightly cron | `full` | yes | capture model drift and long-tail failures |
| Post-deploy | `smoke` | yes | quick signal that the live system is behaving |
| Production incident | `full` | yes | measure blast radius; then add fixture reproducing it |

---

## Re-scoring without re-generating

When you change an eval (update judge criteria, fix a rule, add a new eval), you do
not need to re-run your system. The outputs on disk are unchanged — just re-dispatch
the judges:

```bash
# edit evals/config.yaml or evals/rules.py
fieldtest score --set full    # no regeneration needed
```

This is the most important operational pattern. Re-generating costs money and
time. Re-scoring is cheap — only judge API calls.

**Regenerate outputs** when the system changes: prompt update, new fixture, new source files.
**Re-run only `fieldtest score`** when an eval changes: new eval, updated criteria, fixed rule.

---

## Multiple generators for different scenarios

Nothing stops you from having more than one generator script. Each writes to the same
`outputs/` directory. `fieldtest score` reads whatever is there.

```
evals/
  generate.py           # standard: calls your production system
  generate_shadow.py    # shadow: calls a candidate model or new prompt
  generate_local.py     # local: calls a local model for cheap iteration
```

Run whichever is appropriate:

```bash
python3 evals/generate_shadow.py smoke   # test a new prompt variant on the smoke set
fieldtest score --set smoke              # score the outputs it wrote
```

---

## CI integration (GitHub Actions example)

```yaml
# .github/workflows/eval-regression.yml
name: Eval regression

on:
  pull_request:
  push:
    branches: [main]

jobs:
  regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install fieldtest
      - run: fieldtest score --set regression --config evals/config.yaml
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: evals/results/
```

The `regression` set runs only golden fixtures with rule/regex/reference evals — no LLM
judge calls, no API cost beyond what your system uses. `fieldtest score` exits 0 on
success and 1 on error. It does not exit non-zero on high failure rates — the tool
measures, humans judge. If you want CI to gate on specific failure rates, parse the
`-data.json` output:

```bash
python3 -c "
import json, sys, glob
f = sorted(glob.glob('evals/results/*-data.json'))[-1]
data = json.load(open(f))
# example: fail CI if contact_preserved failure rate > 0
for row in data['rows']:
    if row['eval_id'] == 'contact_preserved' and row['passed'] is False:
        print('contact_preserved failed', row['fixture_id'], 'run', row['run'])
        sys.exit(1)
"
```

---

## Production traffic sampling

Static fixtures test known cases. For coverage of the actual distribution of inputs
your system receives in production, write a generator that pulls real traffic:

```python
# evals/generate_production.py
# Pulls recent production requests, runs them through the system,
# writes outputs — fieldtest score handles the rest.

import pathlib
from your_system import call_system
from your_logging import fetch_recent_requests

OUTPUT_DIR = pathlib.Path("evals/outputs")

requests = fetch_recent_requests(limit=50)
for req in requests:
    fixture_id = f"prod-{req['id']}"
    out_dir = OUTPUT_DIR / fixture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output = call_system(req["input"])
    (out_dir / "run-1.txt").write_text(output)
```

`fieldtest score` doesn't know these came from production vs static fixtures. The
same evals run. The same report is written. You get failure rate distributions over
your actual traffic, not just your handcrafted test cases.

Add production-sampled fixtures to the `full` set; keep static fixtures in `regression`
for the deterministic CI gate.
