"""Command line entry point used by the workflow.

    python -m test_agent run --repo <checkout> --changes changes.json --out <dir>

The agent edits test files in --repo and always writes <out>/report.json; the
workflow builds the patch from the working tree itself. Exit codes:
0 = finished (even if it decided to add nothing), 1 = failed, 2 = not configured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from test_agent.engines import stub
from test_agent.report import Model, Report

EXIT_OK, EXIT_FAILED, EXIT_NOT_CONFIGURED = 0, 1, 2

ENGINES: dict[str, Callable[[Path, dict], Report]] = {"stub": stub.run}


class NotConfigured(Exception):
    pass


def select_engine(name: str) -> Callable[[Path, dict], Report]:
    if name not in ENGINES:
        raise NotConfigured(f"Unknown engine {name!r}; available: {', '.join(sorted(ENGINES))}")
    return ENGINES[name]


def run(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "report.json"
    started = time.monotonic()
    try:
        engine = select_engine(args.engine)
        changes = json.loads(args.changes.read_text(encoding="utf-8"))
        report = engine(args.repo, changes)
        code = EXIT_OK
    except NotConfigured as error:
        report = Report(engine=args.engine, model=Model("none", "none"), errors=[str(error)])
        code = EXIT_NOT_CONFIGURED
    except Exception as error:  # noqa: BLE001 - the report must be written whatever happens
        traceback.print_exc()
        report = Report(engine=args.engine, model=Model("none", "none"), errors=[f"{type(error).__name__}: {error}"])
        code = EXIT_FAILED
    report.usage.duration_seconds = round(time.monotonic() - started, 1)
    report.write(report_path)
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_agent", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="write or update tests for the changed files")
    run_parser.add_argument("--repo", type=Path, required=True, help="checkout of the pull request head")
    run_parser.add_argument("--changes", type=Path, required=True, help="output of detect_changes.py")
    run_parser.add_argument("--out", type=Path, required=True, help="directory for report.json")
    run_parser.add_argument(
        "--engine", default=os.environ.get("TEST_AGENT_ENGINE") or "stub", help="engine (default: $TEST_AGENT_ENGINE or stub)"
    )
    return run(parser.parse_args(argv))
