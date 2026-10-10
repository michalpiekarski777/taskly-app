"""The agent's report: the contract between the agent and the workflow.

The workflow renders it into the pull request comment
(.github/scripts/render_report.py), so field names and values must stay in
sync with that script.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

SCHEMA_VERSION = 1

Decision = Literal["create", "update", "skip"]
Result = Literal["passed", "failed", "skipped", "not-run"]


@dataclass
class Target:
    component: str
    source: str
    decision: Decision
    reason: str
    test_file: str | None = None
    skills: list[str] = field(default_factory=list)
    attempts: int = 0
    result: Result = "not-run"


@dataclass
class Model:
    provider: str
    name: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0


@dataclass
class Report:
    engine: str
    model: Model
    targets: list[Target] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
