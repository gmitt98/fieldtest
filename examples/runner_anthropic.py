"""
examples/runner_anthropic.py

Reference runner: calls Anthropic Claude directly for each fixture.
Conforms to the fieldtest runner contract.

Usage:
  python examples/runner_anthropic.py [set_name]
  python examples/runner_anthropic.py smoke
  python examples/runner_anthropic.py full

Requires:
  pip install anthropic pyyaml
  export ANTHROPIC_API_KEY=sk-ant-...

Note: this is YOUR system's model — completely separate from the judge model
that fieldtest uses internally. Change the model here freely; it has no effect
on how your outputs are scored. The judge model is set in evals/config.yaml
under defaults.model.
"""
import os
import pathlib
import sys
import yaml
import anthropic


# ---------------------------------------------------------------------------
# Your system logic — replace with your actual implementation
# ---------------------------------------------------------------------------

client = anthropic.Anthropic()

# YOUR system prompt — replace with your actual prompt
SYSTEM_PROMPT = """You are a helpful assistant."""

def call_system(inputs: dict) -> str:
    """
    Call your LLM system with fixture inputs. Return output as plain text.

    inputs is the 'inputs' block from your fixture YAML — whatever fields
    you defined there are available here.
    """
    # Build your user message from fixture inputs.
    # Replace this with your actual message construction.
    user_message = str(inputs)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",   # your system model — change freely
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Runner boilerplate — conforms to fieldtest runner contract
# ---------------------------------------------------------------------------

def resolve_set(set_name: str, fixtures_cfg: dict) -> list[str]:
    """Resolve fixture set name to list of IDs."""
    sets  = fixtures_cfg.get("sets", {})
    value = sets.get(set_name, sets.get("full", []))
    if value == "all":
        fixture_dir = pathlib.Path(fixtures_cfg["directory"])
        return [p.stem for p in sorted(fixture_dir.rglob("*.yaml"))]
    if isinstance(value, str) and value.endswith("/*"):
        sub = value[:-2]
        fixture_dir = pathlib.Path(fixtures_cfg["directory"]) / sub
        return [p.stem for p in sorted(fixture_dir.glob("*.yaml"))]
    return value if isinstance(value, list) else []


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    config_path = pathlib.Path("evals/config.yaml")
    if not config_path.exists():
        print(f"Error: config not found at {config_path}. Run from your project root.", file=sys.stderr)
        sys.exit(1)

    config   = yaml.safe_load(config_path.read_text())
    set_name = sys.argv[1] if len(sys.argv) > 1 else "full"

    for use_case in config["use_cases"]:
        fixtures_cfg = use_case["fixtures"]
        runs         = fixtures_cfg.get("runs", config.get("defaults", {}).get("runs", 5))
        fixture_dir  = pathlib.Path("evals") / fixtures_cfg["directory"]
        fixture_ids  = resolve_set(set_name, fixtures_cfg)

        print(f"use_case: {use_case['id']} | set: {set_name} | {len(fixture_ids)} fixtures × {runs} runs")

        for fixture_id in fixture_ids:
            fixture_path = fixture_dir / f"{fixture_id}.yaml"
            try:
                fixture = yaml.safe_load(fixture_path.read_text())
            except Exception as e:
                print(f"Error: could not load fixture {fixture_path}: {e}", file=sys.stderr)
                sys.exit(1)

            out_dir = pathlib.Path(f"evals/outputs/{fixture_id}")
            out_dir.mkdir(parents=True, exist_ok=True)

            for run in range(1, runs + 1):
                print(f"  {fixture_id} run {run}/{runs}...", end=" ", flush=True)
                output = call_system(fixture["inputs"])
                (out_dir / f"run-{run}.txt").write_text(output)
                print("✓")


if __name__ == "__main__":
    main()
