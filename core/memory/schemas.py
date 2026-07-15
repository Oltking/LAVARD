"""Portable Memory shapes: durable Facts (confidence + freshness) and reusable Playbooks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Fact:
    id: str
    owner_id: str
    topic: str
    text: str
    domain: str
    confidence: float          # 0..1
    freshness_ts: float        # unix seconds; onchain/price facts go stale and are weighted
    redacted_kinds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Playbook:
    """A reusable workflow blueprint distilled from a completed job: the goal shape, the
    decomposition skeleton, the dependency DAG, and the ideal crew (agents that delivered well).
    Reused on intake to skip planning AND pre-select the crew."""

    id: str
    owner_id: str
    goal_shape: str            # canonical (redacted) description of the goal this solves
    roles: list[str]           # capabilities that were hired
    pitfalls: list[str]        # stalls/escalations seen, so next time we watch for them
    node_titles: list[str]     # the decomposition skeleton to seed the Foreman
    dag_edges: list[list[str]] = field(default_factory=list)  # [[from_cap, to_cap], ...]
    crew: list[dict] = field(default_factory=list)            # [{capability, agent_id, name, score}]
    uses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def anonymized(self) -> dict[str, Any]:
        """Non-identifying capability SHAPE only — the sole part safe to contribute to the global
        aggregate-learning model (privacy Tier 2). Deliberately drops owner, goal_shape,
        node_titles, and crew: those can carry project specifics or reveal who a user hired, and
        the blueprint itself is NEVER shared with another user (see docs/privacy.md)."""
        return {"roles": self.roles, "dag_edges": self.dag_edges}
