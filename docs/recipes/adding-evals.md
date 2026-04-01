# Recipe: Adding Evals to an Existing Suite

**Goal:** Extend your eval coverage without breaking existing results or losing history.

## The pattern

Add new evals to your config, re-score existing outputs, and see the new eval alongside existing ones. You don't need to re-run your system.

## Step by step

### 1. Add the eval to config.yaml

```yaml
evals:
  # ... existing evals ...

  # New eval — added to catch a failure mode you discovered
  - id: no-pii-in-response
    tag: safe
    labels: [privacy]
    type: regex
    pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"    # SSN pattern
    match: false
    description: Response does not contain Social Security numbers
```

### 2. Re-score without re-running

Your existing outputs in `evals/outputs/` are still valid. Just re-score:

```bash
fieldtest score --set full
```

The new eval runs against the same outputs. The report now includes the new row.

### 3. Check the delta

The delta section will show `no-pii-in-response` as a **new eval** (no baseline to compare against). All other evals will show their delta normally.

## When to re-run vs. re-score

| Situation | Action |
|-----------|--------|
| Added a new eval | Re-score only (`fieldtest score`) |
| Changed eval criteria | Re-score only |
| Changed the system prompt | Re-run + re-score |
| Changed fixture inputs | Re-run + re-score |
| Improved a rule in rules.py | Re-score only |

This is why the runner and scorer are decoupled — re-scoring is free (or near-free for rule/regex/reference evals).

## Tips

- Add one eval at a time so you can see its isolated impact
- If the new eval fails on most fixtures, that's signal — either the failure mode is real and widespread, or your eval criteria are too strict
- Use `labels:` to group related evals for filtering in the HTML report
- Commit your updated config alongside the new results so the history is traceable
