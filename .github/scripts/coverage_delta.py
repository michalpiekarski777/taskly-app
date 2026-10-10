"""Compare coverage before and after the agent's patch.

Reads coverage.py JSON reports (backend) and Vitest `json-summary` reports
(frontend) and reports line and branch coverage per component and per file.

Usage:
    coverage_delta.py [--backend-before b.json --backend-after a.json]
                      [--frontend-before b.json --frontend-after a.json
                       --frontend-root <dir the reports were produced in>]
                      [--out coverage-delta.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

METRICS = ("lines", "branches")


def _metric(covered: int, total: int) -> dict:
    pct = round(covered / total * 100, 2) if total else 100.0
    return {"covered": covered, "total": total, "pct": pct}


def read_pytest(path: Path, prefix: str = "backend") -> tuple[dict, dict[str, dict]]:
    """(totals, per-file) metrics from a coverage.py JSON report."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def metrics(summary: dict) -> dict:
        return {
            "lines": _metric(summary["covered_lines"], summary["num_statements"]),
            "branches": _metric(summary.get("covered_branches", 0), summary.get("num_branches", 0)),
        }

    files = {f"{prefix}/{name}": metrics(entry["summary"]) for name, entry in data["files"].items()}
    return metrics(data["totals"]), files


def read_vitest(path: Path, root: str, prefix: str = "frontend") -> tuple[dict, dict[str, dict]]:
    """(totals, per-file) metrics from a Vitest/istanbul json-summary report."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def metrics(entry: dict) -> dict:
        return {m: _metric(entry[m]["covered"], entry[m]["total"]) for m in METRICS}

    files = {}
    for name, entry in data.items():
        if name == "total":
            continue
        relative = os.path.relpath(name, root) if os.path.isabs(name) else name
        files[f"{prefix}/{Path(relative).as_posix()}"] = metrics(entry)
    return metrics(data["total"]), files


def compare(before: tuple[dict, dict], after: tuple[dict, dict]) -> dict:
    (totals_before, files_before), (totals_after, files_after) = before, after
    changed_files = []
    for path in sorted(files_before.keys() | files_after.keys()):
        old, new = files_before.get(path), files_after.get(path)
        old_pct = old["lines"]["pct"] if old else None
        new_pct = new["lines"]["pct"] if new else None
        if old_pct != new_pct:
            changed_files.append(
                {
                    "path": path,
                    "lines_before": old_pct,
                    "lines_after": new_pct,
                    "delta": round((new_pct or 0) - (old_pct or 0), 2),
                }
            )
    return {
        "before": totals_before,
        "after": totals_after,
        "delta": {m: round(totals_after[m]["pct"] - totals_before[m]["pct"], 2) for m in METRICS},
        "files": changed_files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend-before", type=Path)
    parser.add_argument("--backend-after", type=Path)
    parser.add_argument("--frontend-before", type=Path)
    parser.add_argument("--frontend-after", type=Path)
    parser.add_argument("--frontend-root", default=os.getcwd())
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    components = {}
    if args.backend_before and args.backend_after:
        components["backend"] = compare(read_pytest(args.backend_before), read_pytest(args.backend_after))
    if args.frontend_before and args.frontend_after:
        components["frontend"] = compare(
            read_vitest(args.frontend_before, args.frontend_root),
            read_vitest(args.frontend_after, args.frontend_root),
        )
    if not components:
        parser.error("Provide before and after reports for at least one component")

    text = json.dumps({"components": components}, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
