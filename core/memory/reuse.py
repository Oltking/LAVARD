"""REUSE on intake (§4.5): match an incoming goal to a known-good Playbook, hand relevant facts
to the Foreman, and — before any spend — let memory answer a node so the hire is skipped entirely.
The cheapest agent is the one you never hire.
"""

from __future__ import annotations

import time

from core.memory.schemas import Fact, Playbook
from core.memory.store import get_memory

FRESH_MAX_AGE_S = 30 * 24 * 3600.0   # facts older than 30d are treated as stale for reuse
MIN_CONF = 0.7


def match_playbook(owner_id: str, goal: str) -> tuple[Playbook, float] | None:
    return get_memory().match_playbook(owner_id, goal)


def blueprint_for_goal(owner_id: str, goal: str) -> Playbook | None:
    """The reusable workflow blueprint (skeleton + DAG + ideal crew) for a goal, if one exists.
    Reused on intake to skip cold planning/search and pre-select known-good specialists."""
    match = get_memory().match_playbook(owner_id, goal)
    return match[0] if match else None


def preferred_crew_for_goal(owner_id: str, goal: str) -> dict[str, str]:
    """capability → agent_id map from the best-matching blueprint's crew (empty if none)."""
    pb = blueprint_for_goal(owner_id, goal)
    if pb is None:
        return {}
    return {c["capability"]: c["agent_id"] for c in pb.crew if c.get("agent_id")}


def seed_facts_for_goal(owner_id: str, goal: str, top_k: int = 5) -> list[tuple[Fact, float]]:
    return get_memory().search_facts(owner_id, goal, min_conf=MIN_CONF,
                                     max_age_s=FRESH_MAX_AGE_S, top_k=top_k, threshold=0.55)


def memory_answer_for_node(owner_id: str, node: dict, now: float | None = None):
    """Return (Fact, sim) if Portable Memory already answers this node well enough to skip a hire."""
    now = now if now is not None else time.time()
    query = f"{node['title']} {node['capability']}"
    hits = get_memory().search_facts(
        owner_id, query, min_conf=MIN_CONF, max_age_s=FRESH_MAX_AGE_S,
        top_k=1, threshold=0.55, now=now,
    )
    return hits[0] if hits else None
