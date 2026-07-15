"""Shared data shapes — plain stdlib dataclasses so the core runtime has ZERO third-party deps.

Why dataclasses (not pydantic): the offline demo + tests must run with nothing installed. FastAPI
supports stdlib dataclasses directly as request/response models, so the production API layer still
uses these unchanged. One schema means the offline heuristic and a real model produce identical
structures, and downstream phases never care which planner ran.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _filtered(cls: type, data: dict) -> dict:
    """Keep only kwargs that are real fields of `cls` (tolerates noisy LLM JSON)."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class IntakeResult:
    """Verify-first restatement of the user's goal (§4.6)."""

    restated_goal: str
    assumptions: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "IntakeResult":
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlannedNode:
    """One sub-task in the goal's DAG. `depends_on` holds KEYS of prerequisite nodes."""

    key: str
    title: str
    description: str = ""
    success_criteria: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    capability: str = "general"
    needs_hire: bool = True
    rationale: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PlannedNode":
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    nodes: list[PlannedNode] = field(default_factory=list)


@dataclass
class JobView:
    """What the API/CLI return for a job."""

    id: str
    goal: str
    status: str
    planner: str
    restated_goal: str
    assumptions: list[str]
    success_criteria: list[str]
    open_questions: list[str]
    nodes: list[PlannedNode]
    owner_id: str = "default-owner"
    reused_playbook: str | None = None    # goal_shape of a matched Playbook (§4.5 reuse-on-intake)
    path_mode: str = ""                   # conductor path: direct_mcp | single_asp | orchestrate
    path_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
