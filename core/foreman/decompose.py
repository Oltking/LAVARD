"""Goal -> task graph (DAG) decomposition (§4.2, Phase 1).

Produces a list of `PlannedNode`s with per-node success criteria and dependencies. Uses the
model when configured, else a deterministic heuristic. Either way the result is validated as a
real DAG (unique keys, resolvable deps, acyclic) before it leaves this module — an invalid LLM
plan falls back to the heuristic rather than propagating a broken graph.

The necessity test (`needs_hire`) is seeded here and refined by the Vetter/Foreman in Phase 4:
coordination/verification nodes LAVARD does itself; specialist work is a candidate for hiring.
"""

from __future__ import annotations

import re

from core.llm.client import ModelClient
from core.schemas import IntakeResult, Plan, PlannedNode

# --- capability keyword map (fit -> canonical capability the marketplace query will use) ---
_CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research": ("research", "investigate", "find", "gather", "study", "compare", "benchmark",
                 "analyz", "survey"),
    "data": ("data", "dataset", "scrape", "etl", "pipeline", "clean ", "dataframe", "sql"),
    "content": ("write", "blog", "article", "copy", "content", "documentation", "docs",
                "draft", "translate", "summar"),
    "design": ("design", " ui", " ux", "mockup", "logo", "brand", "graphic", "wireframe"),
    "engineering": ("build", "code", "implement", "develop", " api", "backend", "frontend",
                    "app", "website", "script", "integrate", "software"),
    "security": ("audit", "security", "vulnerab", "smart contract", "pentest", "exploit"),
    "devops": ("deploy", " ci", " cd", "infrastructure", "docker", "kubernetes", "host",
               "provision", "serverless"),
    "marketing": ("market", "campaign", " seo", " ads", "social", "launch", "promote",
                  "outreach"),
    "finance": ("trade", "swap", "invest", "portfolio", "onchain", "defi", "token", "price",
                "wallet", "crypto"),
    "qa": ("test", " qa", "validate", "review"),
}

# LAVARD does these itself — no external hire (necessity test seed).
_INTERNAL_HINTS = ("plan", "organiz", "coordinat", "decide", "summar", "review", "verify",
                   "integrate", "prioriti")

# sequential split markers -> each stage depends on the previous
_SPLIT_RE = re.compile(
    r"\bthen\b|\bafter that\b|\bafterwards\b|\bafter\b|\bonce\b|\bnext\b|\bfinally\b|;|(?:,\s*and\b)|\band then\b",
    re.I,
)

_DECOMPOSE_SYSTEM = (
    "You are LAVARD's Foreman. Decompose the verified goal into the SMALLEST set of sub-tasks "
    "that actually accomplishes it (necessity test — never pad). For each node give a stable "
    "key (n1,n2,...), title, description, 1-3 measurable success_criteria, depends_on (keys of "
    "prerequisites), capability (one of: research,data,content,design,engineering,security,"
    "devops,marketing,finance,qa,coordination), needs_hire (false for coordination/verification "
    "LAVARD does itself), and a one-line rationale. Include a final coordination node that "
    'verifies all deliverables against the success criteria. Reply as JSON: {"nodes": [...]}.'
)


def decompose(
    goal: str, intake: IntakeResult | None = None, model: ModelClient | None = None
) -> Plan:
    model = model or ModelClient()
    if model.is_configured:
        try:
            crit = "\n".join(intake.success_criteria) if intake else ""
            data = model.complete_json(
                _DECOMPOSE_SYSTEM,
                f"Goal: {goal}\nSuccess criteria:\n{crit}",
                tier="complex",
            )
            plan = Plan(nodes=[PlannedNode.from_dict(n) for n in data.get("nodes", [])])
            validate_dag(plan.nodes)  # raises if malformed
            if plan.nodes:
                return plan
        except Exception:
            pass  # fall through to heuristic
    return _heuristic(goal, intake)


def _capability_for(text: str) -> str:
    low = " " + text.lower() + " "
    for cap, kws in _CAPABILITY_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return cap
    return "general"


def _heuristic(goal: str, intake: IntakeResult | None) -> Plan:
    goal = goal.strip().rstrip(".")
    fragments = [f.strip(" ,.").strip() for f in _SPLIT_RE.split(goal)]
    fragments = [f for f in fragments if len(f) > 2]
    if not fragments:
        fragments = [goal]

    nodes: list[PlannedNode] = []
    prev_key: str | None = None
    for i, frag in enumerate(fragments, start=1):
        key = f"n{i}"
        cap = _capability_for(frag)
        internal = cap == "general" and any(h in frag.lower() for h in _INTERNAL_HINTS)
        nodes.append(
            PlannedNode(
                key=key,
                title=frag[:1].upper() + frag[1:],
                description=f"Sub-task derived from the goal: {frag}.",
                success_criteria=[f"'{frag}' is completed and its output reviewed."],
                depends_on=[prev_key] if prev_key else [],
                capability=cap if not internal else "coordination",
                needs_hire=not internal,
                rationale=(
                    "Coordination step handled by LAVARD."
                    if internal
                    else f"Requires '{cap}' capability the goal genuinely needs."
                ),
            )
        )
        prev_key = key

    # final verification / sign-off node — LAVARD's own, depends on everything before it.
    verify_key = f"n{len(nodes) + 1}"
    crit = intake.success_criteria if intake else [f"Goal '{goal}' is fully satisfied."]
    nodes.append(
        PlannedNode(
            key=verify_key,
            title="Integrate deliverables and verify against success criteria",
            description="Assemble sub-task outputs, check them against the job's success "
            "criteria, and prepare sign-off.",
            success_criteria=crit,
            depends_on=[n.key for n in nodes],
            capability="coordination",
            needs_hire=False,
            rationale="Final quality gate and sign-off — the controller's own responsibility.",
        )
    )
    plan = Plan(nodes=nodes)
    validate_dag(plan.nodes)
    return plan


def validate_dag(nodes: list[PlannedNode]) -> None:
    """Raise ValueError unless nodes form a valid DAG: unique keys, resolvable deps, acyclic."""
    keys = [n.key for n in nodes]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate node keys in plan.")
    keyset = set(keys)
    for n in nodes:
        for dep in n.depends_on:
            if dep not in keyset:
                raise ValueError(f"Node {n.key} depends on unknown node {dep}.")
            if dep == n.key:
                raise ValueError(f"Node {n.key} depends on itself.")
    # cycle check via Kahn's algorithm
    incoming = {k: 0 for k in keys}
    adj: dict[str, list[str]] = {k: [] for k in keys}
    for n in nodes:
        for dep in n.depends_on:
            adj[dep].append(n.key)
            incoming[n.key] += 1
    queue = [k for k, c in incoming.items() if c == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                queue.append(nxt)
    if seen != len(keys):
        raise ValueError("Task graph contains a cycle.")
