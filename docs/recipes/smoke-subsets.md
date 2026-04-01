# Recipe: Smoke Subsets

**Goal:** Get fast eval signal with a small fixture set before committing to a full run.

## The pattern

Define a `smoke` set in your config with 2-4 representative fixtures. Run it after every prompt change. Use `full` for pre-release and nightly runs.

## Config snippet

```yaml
fixtures:
  directory: fixtures/
  sets:
    smoke: [vacation-policy, off-topic-sports]    # 2 fixtures, fast
    regression: golden/*                           # all golden fixtures
    full: all                                      # everything
  runs: 3
```

## Workflow

```bash
# Quick check after a prompt edit (< 30 seconds)
python evals/runner.py smoke
fieldtest score --set smoke

# Looks good — run the full suite
python evals/runner.py full
fieldtest score --set full
```

## Choosing smoke fixtures

Pick fixtures that cover:

1. **One happy-path case** — should pass all evals consistently
2. **One known-hard case** — the fixture most likely to regress
3. **One edge case** — out-of-scope, adversarial, or boundary input

If your smoke set passes but full fails, your smoke set isn't representative enough — swap in the failing fixture.

## Cost control

- Smoke with `runs: 1` for instant signal (override in config or use a separate set config)
- Use deterministic evals only (rule, regex, reference) for zero-cost smoke: no LLM judge calls
- Reserve LLM judges for `full` runs

## What the report shows

Smoke reports are small — 2-4 rows in the fixture matrix. The delta section still works: it compares against the last smoke run (same-set baseline), so you can track iteration within the smoke scope.

**Important:** fieldtest only compares runs from the same set. A smoke run won't delta against a full run — this prevents misleading cross-set comparisons.
