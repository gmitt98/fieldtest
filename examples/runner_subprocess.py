"""
examples/runner_subprocess.py

Reference runner: calls any CLI tool, captures stdout as output.
Conforms to the fieldtest runner contract.

Usage:
  python examples/runner_subprocess.py [set_name]

Setup:
  1. Replace CMD with your actual command
  2. Your tool receives fixture inputs as JSON on stdin
  3. Your tool should write its output to stdout

Fixture inputs are passed as JSON via stdin.
"""
import json
import pathlib
import subprocess
import sys
import yaml


# ---------------------------------------------------------------------------
# Replace this with your actual command.
# Your tool will receive fixture inputs as JSON on stdin and should write
# its output to stdout.
# ---------------------------------------------------------------------------
CMD = ["your-cli-tool", "--flag"]


def call_system(inputs: dict) -> str:
    """Call a CLI tool with fixture inputs via stdin. Capture stdout."""
    if CMD == ["your-cli-tool", "--flag"]:
        print(
            "Error: CMD is still the placeholder value. "
            "Replace CMD at the top of this file with your actual command.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            CMD,
            input=json.dumps(inputs),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error: command failed (exit {e.returncode}): {' '.join(CMD)}", file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: command not found: {CMD[0]}", file=sys.stderr)
        sys.exit(1)


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
                output = call_system(fixture["inputs"])
                (out_dir / f"run-{run}.txt").write_text(output)
                print(f"  {fixture_id} run {run}/{runs} ✓")


if __name__ == "__main__":
    main()
