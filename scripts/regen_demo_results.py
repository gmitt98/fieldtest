#!/usr/bin/env python3
"""
Re-render the bundled demo results from their committed rows.

The demos ship pre-scored results so `fieldtest demo --offline` works without a
key. Those artifacts were rendered by an older writer and aggregated by an older
summary, so they drift every time either changes — and a user comparing what the
tool prints against what the demo shipped sees two different reports.

No judging happens here and no key is needed: the verdicts in -data.json are
reused verbatim. Only the aggregation and the rendering are recomputed, which is
exactly what went stale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fieldtest.config import ResultRow, parse_and_validate          # noqa: E402
from fieldtest.results.aggregator import build_summary               # noqa: E402
from fieldtest.results.writer import write_results                   # noqa: E402


def regen(demo_dir: Path) -> str:
    results = demo_dir / "results"
    data_files = sorted(results.glob("*-data.json"))
    if not data_files:
        return f"{demo_dir.name}: no -data.json, skipped"

    config = parse_and_validate(demo_dir / "config.yaml")
    out = []
    for data_path in data_files:
        data = json.loads(data_path.read_text(encoding="utf-8"))
        rows = [ResultRow.model_validate(r) for r in data["rows"]]

        # The shipped artifacts are named for the file, not for the run inside
        # it: `demo --offline` reads demo-offline-report.html. Writing under the
        # run_id would leave the shipped files stale and add files nothing reads.
        file_stem = data_path.name.removesuffix("-data.json")

        # The report header stamps render time. Left alone, every regen rewrites
        # the date and the committed artifacts churn on a no-op run. The demo
        # results describe a run that happened on a particular day; preserve it.
        md_path = results / f"{file_stem}-report.md"
        original_stamp = None
        if md_path.exists():
            for line in md_path.read_text(encoding="utf-8").splitlines():
                if "| set:" in line:
                    original_stamp = line.split(" | ")[0]
                    break

        write_results(
            rows=rows,
            summary=build_summary(rows, config),
            delta=data.get("delta", {}),
            config=config,
            run_id=file_stem,
            output_dir=results,
            set_name=data.get("set", "full"),
            partial=data.get("partial", False),
            partial_details=data.get("partial_details") or None,
        )
        # write_results stamps run_id into the JSON; restore the real one, which
        # is what the report header and any delta refer to.
        if original_stamp:
            for suffix in ("-report.md", "-report.html"):
                f = results / f"{file_stem}{suffix}"
                if not f.exists():
                    continue
                text = f.read_text(encoding="utf-8")
                lines = text.splitlines(keepends=True)
                for i, line in enumerate(lines):
                    if "| set:" in line or "Time:" in line:
                        head, sep, tail = line.partition(" | set:")
                        if sep:
                            lines[i] = original_stamp + sep + tail
                            break
                f.write_text("".join(lines), encoding="utf-8")

        out_json = results / f"{file_stem}-data.json"
        restored = json.loads(out_json.read_text(encoding="utf-8"))
        restored["run_id"] = data["run_id"]
        out_json.write_text(json.dumps(restored, indent=2, default=str), encoding="utf-8")
        out.append(f"{demo_dir.name}/{file_stem}")
    return ", ".join(out)


def main() -> int:
    for demo in sorted((REPO / "fieldtest" / "demo").iterdir()):
        if demo.is_dir() and (demo / "config.yaml").exists():
            print(f"  {regen(demo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
