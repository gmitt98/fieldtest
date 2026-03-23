"""
examples/runner_anthropic.py

Reference runner: calls Anthropic Claude directly for each fixture.
Conforms to the fieldtest runner contract (§14).

Usage:
  python examples/runner_anthropic.py [set_name]
  python examples/runner_anthropic.py smoke
  python examples/runner_anthropic.py full

Requires: ANTHROPIC_API_KEY in environment.
Replace the prompt in call_system() with your actual system prompt.
"""
import pathlib
import sys
import yaml
import anthropic

# ---------------------------------------------------------------------------
# Your system logic — replace with your actual implementation
# ---------------------------------------------------------------------------

client = anthropic.Anthropic()

def call_system(inputs: dict) -> str:
    """Call your LLM system with fixture inputs. Return output as plain text."""
    # Example: inputs might be {"resume": "...", "job": "..."}
    user_message = f"""
Resume:
{inputs.get("resume", "")}

Job Description:
{inputs.get("job", "")}

Please tailor this resume for the job description above.
""".strip()

    message = client.messages.create(
        model="claude-opus-4-5",  # YOUR system model — not the judge model
        max_tokens=2048,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Runner boilerplate — conforms to fieldtest runner contract
# ---------------------------------------------------------------------------

def resolve_set(set_name: str, fixtures_cfg: dict) -> list[str]:
    """Resolve fixture set name to list of IDs."""
    sets = fixtures_cfg.get("sets", {})
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
    config_path = pathlib.Path("evals/config.yaml")
    config      = yaml.safe_load(config_path.read_text())
    set_name    = sys.argv[1] if len(sys.argv) > 1 else "full"

    for use_case in config["use_cases"]:
        fixtures_cfg = use_case["fixtures"]
        runs         = fixtures_cfg.get("runs", config.get("defaults", {}).get("runs", 5))
        fixture_dir  = pathlib.Path(fixtures_cfg["directory"])
        fixture_ids  = resolve_set(set_name, fixtures_cfg)

        print(f"use_case: {use_case['id']} | set: {set_name} | {len(fixture_ids)} fixtures × {runs} runs")

        for fixture_id in fixture_ids:
            fixture = yaml.safe_load((fixture_dir / f"{fixture_id}.yaml").read_text())
            out_dir = pathlib.Path(f"evals/outputs/{fixture_id}")
            out_dir.mkdir(parents=True, exist_ok=True)

            for run in range(1, runs + 1):
                output = call_system(fixture["inputs"])
                (out_dir / f"run-{run}.txt").write_text(output)
                print(f"  {fixture_id} run {run}/{runs} ✓")


if __name__ == "__main__":
    main()
