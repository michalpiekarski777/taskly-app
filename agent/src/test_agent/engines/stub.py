"""Deterministic engine without an LLM, for exercising the workflow end to end.

For each changed source file without a test file it writes an import smoke
test. TEST_AGENT_STUB_SCENARIO selects other outcomes, so every path of the
workflow (nothing to add, failing tests, rejected patch) can be demonstrated:

    smoke      (default) create smoke tests
    none       decide that no tests are needed
    failing    create tests that fail
    forbidden  also modify a source file, which validation must reject
"""

from __future__ import annotations

import os
from pathlib import Path

from test_agent.report import Model, Report, Target
from test_agent.workspace import conventional_test_path, python_module, resolve_inside, write_file

SCENARIOS = ("smoke", "none", "failing", "forbidden")

BACKEND_TEMPLATE = '''\
"""Smoke test written by the test agent's stub engine (no LLM involved)."""

import {module} as module


def test_module_imports():
    assert module.__name__ == "{module}"{failure}
'''

FRONTEND_TEMPLATE = """\
// Smoke test written by the test agent's stub engine (no LLM involved).
import {{ expect, it }} from 'vitest'
import * as module from './{name}'

it('exports its public API', () => {{
  expect(Object.keys(module).length).toBeGreaterThan({minimum})
}})
"""


def run(repo: Path, changes: dict) -> Report:
    scenario = os.environ.get("TEST_AGENT_STUB_SCENARIO", "smoke")
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown TEST_AGENT_STUB_SCENARIO {scenario!r}; expected one of {SCENARIOS}")
    report = Report(engine="stub", model=Model(provider="none", name=f"stub:{scenario}"))

    for component, data in changes.get("components", {}).items():
        for source in data.get("changed_sources", []):
            report.targets.append(_plan_target(repo, component, source, scenario))

    if scenario == "forbidden":
        sources = [t.source for t in report.targets]
        if sources:
            # Simulates a misbehaving agent; the workflow must refuse this patch.
            with resolve_inside(repo, sources[0]).open("a", encoding="utf-8") as file:
                file.write("\n# modified by the stub engine's 'forbidden' scenario\n")
    return report


def _plan_target(repo: Path, component: str, source: str, scenario: str) -> Target:
    test_file = conventional_test_path(source)
    if scenario == "none":
        return Target(component, source, "skip", "Stub scenario 'none': no tests needed", test_file)
    if test_file is None:
        return Target(component, source, "skip", "No test file convention for this path")
    if resolve_inside(repo, test_file).exists():
        return Target(component, source, "skip", "Test file exists; the stub engine never edits tests", test_file)

    failing = scenario == "failing"
    if component == "backend":
        package = resolve_inside(repo, test_file).parent
        if not (package / "__init__.py").exists():
            # New test packages would need an __init__.py, which the patch policy does not allow.
            return Target(component, source, "skip", f"Test package {package.name}/ does not exist", test_file)
        failure = "\n    assert False, 'stub scenario: failing'" if failing else ""
        content = BACKEND_TEMPLATE.format(module=python_module(source), failure=failure)
    else:
        name = Path(source).stem
        content = FRONTEND_TEMPLATE.format(name=name, minimum=10_000 if failing else 0)

    write_file(repo, test_file, content)
    return Target(
        component,
        source,
        "create",
        "No test file yet; stub engine adds an import smoke test",
        test_file,
        skills=[],
        attempts=1,
        result="not-run",
    )
