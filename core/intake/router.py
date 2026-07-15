"""Intake path-classifier — pick the cheapest *sufficient* execution mode for a goal.

LAVARD's ethos is "the cheapest agent is the one you never hire." This extends it to the job
level: "the cheapest orchestration is the one you never run." A low-need user who just wants one
MCP tool call should not pay the latency and coordination cost of a full room.

Three modes, chosen from the *decomposed plan* (so the signal is the work graph, not raw prose):

  direct_mcp   — exactly one unit of specialist work AND it reads like a single deterministic
                 tool call (fetch/price/convert/scan…). Route it straight through the executor
                 (pay-per-call, via TheHouse for the discount). No room, no sign-off.
  single_asp   — exactly one unit of specialist work that produces a deliverable needing review.
                 One A2A hire with escrow + sign-off, but no multi-agent room.
  orchestrate  — anything else: multiple work units, cross-node dependencies, or ambiguity.
                 The full pipeline (today's path).

CONSERVATIVE BY CONSTRUCTION: mis-routing a real multi-step job to direct_mcp gives a wrong/
partial answer; mis-routing a trivial call to orchestrate merely wastes a little money. So every
uncertain case falls to `orchestrate` — we only short-circuit when it is *clearly* a single call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.schemas import PlannedNode

# Single-shot tool verbs: a deterministic MCP call returns an answer directly, with nothing to
# review or sign off. Kept deliberately narrow.
_MCP_VERBS = (
    "fetch", "get ", "look up", "lookup", "price", "quote", "convert", "scan", "check ",
    "balance", "resolve", "translate", "lookup", "read ", "search ", "current ",
)

# If the goal carries sequencing language it is not a single call, full stop.
_SEQUENCE_RE = re.compile(
    r"\bthen\b|\bafter\b|\bonce\b|\bnext\b|\bfinally\b|;|\band then\b", re.I
)


@dataclass
class PathDecision:
    mode: str            # "direct_mcp" | "single_asp" | "orchestrate"
    capability: str      # the single work capability (or "coordination" when none)
    reason: str
    work_nodes: int = 0

    @property
    def short_circuits(self) -> bool:
        return self.mode in ("direct_mcp", "single_asp")


def _work_nodes(plan_nodes: list[PlannedNode]) -> list[PlannedNode]:
    return [n for n in plan_nodes if n.needs_hire]


def classify_path(goal: str, plan_nodes: list[PlannedNode]) -> PathDecision:
    """Decide the cheapest sufficient mode. Pure — no I/O, no side effects."""
    goal = (goal or "").strip()
    work = _work_nodes(plan_nodes)

    # More than one unit of specialist work → genuine coordination. Orchestrate.
    if len(work) != 1:
        return PathDecision(
            mode="orchestrate",
            capability="coordination",
            reason=f"{len(work)} specialist work units — needs the full pipeline.",
            work_nodes=len(work),
        )

    node = work[0]

    # A single work node that other nodes depend on (beyond the final verify) still implies
    # coordination; and any sequencing language in the goal means it is not one call.
    if _SEQUENCE_RE.search(goal):
        return PathDecision(
            mode="orchestrate",
            capability=node.capability,
            reason="Goal contains sequencing language — treat as multi-step.",
            work_nodes=1,
        )

    low = " " + goal.lower() + " "
    looks_like_tool_call = any(v in low for v in _MCP_VERBS)

    if looks_like_tool_call:
        return PathDecision(
            mode="direct_mcp",
            capability=node.capability,
            reason="Single deterministic tool call — pay-per-call via the executor (agent-to-MCP).",
            work_nodes=1,
        )

    return PathDecision(
        mode="single_asp",
        capability=node.capability,
        reason="One specialist deliverable — a single A2A hire with sign-off (agent-to-ASP).",
        work_nodes=1,
    )
