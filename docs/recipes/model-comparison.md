# Recipe: Model Comparison

**Goal:** Compare two (or more) models on the same fixtures and evals, then diff the results.

## How it works

fieldtest doesn't pick the model your system uses — your runner does. To compare models, run the same fixtures through different models and score each set of outputs.

## Runner pattern

Your runner takes a model name as an argument and writes outputs to the standard directory:

```python
# evals/runner.py
import sys
from pathlib import Path

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-4o"

for fixture_path in Path("evals/fixtures/golden").glob("*.yaml"):
    # ... load fixture, call your system with MODEL ...
    for n in range(1, 4):
        output = call_your_system(fixture["inputs"], model=MODEL)
        Path(f"evals/outputs/{fixture['id']}/run-{n}.txt").write_text(output)
```

## Workflow

```bash
# Run with model A
python evals/runner.py gpt-4o
fieldtest score --set full
# Results saved as run 2026-04-01T10-00-00-a1b2

# Run with model B
python evals/runner.py claude-sonnet-5
fieldtest score --set full
# Results saved as run 2026-04-01T10-05-00-c3d4
# Delta section automatically compares to the previous run

# Compare directly
fieldtest diff 2026-04-01T10-05-00-c3d4 --baseline 2026-04-01T10-00-00-a1b2
```

## What the report shows

The second `fieldtest score` automatically detects the first run as a baseline (same set name) and produces a delta section:

- **Increased/decreased** failure rates per eval
- **Unchanged** evals (within 0.1% epsilon)
- For scored evals: delta in mean score

## Tips

- Use the same fixture set for both runs — cross-set deltas are misleading
- Name your runs or tag them in your notes; fieldtest identifies runs by timestamp
- If you're comparing judge models (not your system's model), use `fieldtest calibrate` — it runs a
  panel over one output set and reports pairwise agreement and kappa, rather than making you diff
  two runs and eyeball the difference. The per-eval `model:` override still works for pinning one
  eval to a stronger judge once you know which one it needs.
- Comparing your system's models is unaffected by any of that, but note that fieldtest now refuses
  to auto-compare runs scored by different judges. Keep the judge fixed while you vary the system,
  or the diff tells you about the instrument instead of the model.
