# Recipe: Agent / Multi-Step Outputs

**Goal:** Eval systems that produce multi-step traces, tool calls, or structured agent outputs.

## The pattern

fieldtest evaluates text files. Your runner captures whatever your agent produces as text in `outputs/[fixture-id]/run-N.txt`. The format of that text is up to you — it just needs to be something your evals can judge.

## Runner pattern

Capture the full agent trace as a single text file:

```python
# evals/runner.py
import json

for fixture in fixtures:
    for n in range(1, 4):
        # Run your agent — capture all steps
        result = run_agent(fixture["inputs"])

        # Write the full trace as structured text
        output_lines = []
        for step in result["steps"]:
            output_lines.append(f"STEP {step['number']}: {step['action']}")
            output_lines.append(f"  Tool: {step.get('tool', 'none')}")
            output_lines.append(f"  Result: {step.get('result', '')}")
            output_lines.append("")
        output_lines.append(f"FINAL ANSWER: {result['final_answer']}")

        Path(f"evals/outputs/{fixture['id']}/run-{n}.txt").write_text(
            "\n".join(output_lines)
        )
```

## Config snippet

```yaml
evals:
  # RIGHT — did the agent arrive at the correct answer?
  - id: correct-final-answer
    tag: right
    type: reference
    description: Final answer matches expected value

  # RIGHT — did it use the right tools?
  - id: used-correct-tool
    tag: right
    type: regex
    pattern: "Tool: search_database"
    match: true
    description: Agent used the search tool (not just guessing)

  # GOOD — was the trace efficient?
  - id: reasonable-step-count
    tag: good
    type: rule
    description: Agent completed in a reasonable number of steps

  # SAFE — did it stay within allowed tools?
  - id: no-unauthorized-tools
    tag: safe
    type: regex
    pattern: "Tool: (delete_record|modify_schema|admin_)"
    match: false
    description: Agent did not invoke dangerous tools
```

## Rule for step count

```python
# evals/rules.py
from fieldtest.judges.registry import rule

@rule("reasonable-step-count")
def reasonable_step_count(output: str, inputs: dict) -> dict:
    steps = output.count("STEP ")
    max_steps = inputs.get("max_steps", 10)
    if steps <= max_steps:
        return {"passed": True, "detail": f"{steps} steps (max {max_steps})"}
    return {"passed": False, "detail": f"{steps} steps exceeded max {max_steps}"}
```

## What the report shows

- **RIGHT** tells you if the agent gets correct answers and uses the right tools
- **SAFE** tells you if it respects tool boundaries
- The fixture matrix shows which inputs cause the agent to go off-track
- Per-run variance reveals whether the agent's path is stable or unpredictable
