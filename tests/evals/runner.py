"""
tests/evals/runner.py

Dogfood runner — runs fieldtest score against controlled test projects and
captures the JSON result as the output for each fixture × run.

Each fixture defines a test_project/ with known config, known outputs, and
known expected results. The runner invokes `fieldtest score` and writes
the JSON result file content to outputs/{fixture_id}/run-{n}.txt.

Usage:
  python tests/evals/runner.py [set_name]
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import shutil
import yaml


def resolve_set(set_name: str, fixtures_cfg: dict, fixture_dir: pathlib.Path) -> list[str]:
    sets  = fixtures_cfg.get("sets", {})
    value = sets.get(set_name, sets.get("full", []))
    if value == "all":
        return [p.stem for p in sorted(fixture_dir.rglob("*.yaml"))]
    if isinstance(value, str) and value.endswith("/*"):
        sub = value[:-2]
        return [p.stem for p in sorted((fixture_dir / sub).glob("*.yaml"))]
    return value if isinstance(value, list) else []


def run_fieldtest_score(config_path: str, set_name: str, results_dir: str) -> str:
    """
    Invoke fieldtest score against a test project.
    Returns the JSON content of the result file, or an error dict as JSON.
    """
    results_path = pathlib.Path(results_dir)
    results_path.mkdir(parents=True, exist_ok=True)

    # Clear existing results so we capture only this run
    for old in results_path.glob("*.json"):
        old.unlink()

    proc = subprocess.run(
        [sys.executable, "-m", "fieldtest.cli", "score",
         "--config", config_path, "--set", set_name],
        capture_output=True, text=True,
    )

    # Find the result JSON written
    results = sorted(results_path.glob("*.json"), reverse=True)
    if results:
        return results[0].read_text()
    else:
        return json.dumps({
            "error": "no result file written",
            "exit_code": proc.returncode,
            "stderr": proc.stderr,
        })


def main():
    config_path  = pathlib.Path("tests/evals/config.yaml")
    config       = yaml.safe_load(config_path.read_text())
    set_name     = sys.argv[1] if len(sys.argv) > 1 else "full"
    outputs_base = pathlib.Path("tests/evals/outputs")

    for use_case in config["use_cases"]:
        fixtures_cfg = use_case["fixtures"]
        runs         = fixtures_cfg.get("runs", config.get("defaults", {}).get("runs", 3))
        fixture_dir  = pathlib.Path("tests/evals") / fixtures_cfg["directory"]
        fixture_ids  = resolve_set(set_name, fixtures_cfg, fixture_dir)

        print(f"[dogfood] use_case: {use_case['id']} | {len(fixture_ids)} fixtures × {runs} runs")

        for fixture_id in fixture_ids:
            fixture = yaml.safe_load((fixture_dir / f"{fixture_id}.yaml").read_text())
            inputs  = fixture.get("inputs", {})

            out_dir = outputs_base / fixture_id
            out_dir.mkdir(parents=True, exist_ok=True)

            for run in range(1, runs + 1):
                output = run_fieldtest_score(
                    config_path=inputs.get("config", ""),
                    set_name=inputs.get("set", "full"),
                    results_dir=inputs.get("results_dir", ""),
                )
                (out_dir / f"run-{run}.txt").write_text(output)
                print(f"  {fixture_id} run {run}/{runs} ✓")


if __name__ == "__main__":
    main()
