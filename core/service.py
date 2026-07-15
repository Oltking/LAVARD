"""Job orchestration seam for Phase 1: submit -> verify-first intake -> decompose -> persist.

Entry point the API and CLI both call. Persistence goes through core/store.py (stdlib sqlite by
default; SQLAlchemy/Postgres backend when configured). Later phases extend `process_job` with the
Foreman hiring loop, the Room, settlement, and distill-on-close.
"""

from __future__ import annotations

from core.foreman import decompose
from core.intake import verify_goal
from core.llm.client import ModelClient
from core.memory import get_memory, match_playbook
from core.queue import submit_job_processing
from core.schemas import JobView, PlannedNode
from core.store import get_store


# A goal is a plain-language instruction, not a document — cap it so a giant input can't DoS
# decomposition/embedding or run up LLM cost (audit: no length bound previously).
MAX_GOAL_CHARS = 8000


def submit_goal(goal: str, owner_id: str = "default-owner") -> JobView:
    """Create a job for a plain-language goal and run it through intake + decomposition."""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Goal must be a non-empty string.")
    if len(goal) > MAX_GOAL_CHARS:
        raise ValueError(f"Goal is too long ({len(goal)} chars); max {MAX_GOAL_CHARS}.")
    job_id = get_store().create_job(goal, owner_id=owner_id)
    from core.governance import audit

    audit(job_id, "job_created", "user", "Goal submitted", {"owner_id": owner_id})
    submit_job_processing(job_id)  # inline by default; Arq if configured
    view = get_job(job_id)
    assert view is not None
    # REUSE on intake: surface a matching known-good Playbook (§4.5) if this owner has one.
    match = match_playbook(owner_id, goal)
    if match is not None:
        view.reused_playbook = match[0].goal_shape
        get_memory().bump_use(match[0].id)
    return view


def process_job(job_id: str) -> None:
    """Verify the goal, decompose it, and persist the task graph. Idempotent per stage."""
    store = get_store()
    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Unknown job {job_id}")

    intake = verify_goal(job["goal"])
    store.save_intake(
        job_id,
        restated_goal=intake.restated_goal,
        assumptions=intake.assumptions,
        success_criteria=intake.success_criteria,
        open_questions=intake.open_questions,
        status="verified",
    )

    plan = decompose(job["goal"], intake)
    planner = "llm" if ModelClient().is_configured else "heuristic"
    store.save_plan(job_id, planner, plan.nodes)

    # Cheapest-sufficient path: a low-need goal (one deterministic tool call, or one specialist
    # deliverable) short-circuits the full room. Conservative — ambiguity → orchestrate.
    from core.governance import audit
    from core.intake.router import classify_path

    decision = classify_path(job["goal"], plan.nodes)
    store.save_path_decision(job_id, decision.mode, decision.reason)
    audit(job_id, "path_classified", "foreman", decision.reason,
          {"mode": decision.mode, "capability": decision.capability,
           "work_nodes": decision.work_nodes})


def get_job(job_id: str) -> JobView | None:
    data = get_store().get_job(job_id)
    if data is None:
        return None
    return JobView(
        id=data["id"],
        goal=data["goal"],
        status=data["status"],
        planner=data["planner"],
        restated_goal=data["restated_goal"],
        assumptions=data["assumptions"],
        success_criteria=data["success_criteria"],
        open_questions=data["open_questions"],
        owner_id=data.get("owner_id", "default-owner"),
        path_mode=data.get("path_mode", ""),
        path_reason=data.get("path_reason", ""),
        nodes=[
            PlannedNode(
                key=n["key"],
                title=n["title"],
                description=n["description"],
                success_criteria=n["success_criteria"],
                depends_on=n["depends_on"],
                capability=n["capability"],
                needs_hire=n["needs_hire"],
                rationale=n["rationale"],
            )
            for n in data["nodes"]
        ],
    )
