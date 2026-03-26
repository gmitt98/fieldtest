"""
examples/generate_openai.py

Reference generator: calls OpenAI directly for each fixture and
writes outputs to evals/outputs/ for fieldtest to score.

Usage:
  python examples/generate_openai.py [set_name]

Requires: OPENAI_API_KEY in environment.
Replace the prompt in call_system() with your actual system prompt.
"""
import pathlib
import sys
import yaml
import openai

client = openai.OpenAI()


def call_system(inputs: dict) -> str:
    """Call your OpenAI system with fixture inputs. Return output as plain text."""
    user_message = f"Input: {inputs}"  # Replace with your actual prompt construction

    response = client.chat.completions.create(
        model="gpt-4o",  # YOUR system model — not the judge model
        messages=[{"role": "user", "content": user_message}],
    )
    return response.choices[0].message.content


def resolve_set(set_name: str, fixtures_cfg: dict) -> list[str]:
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
