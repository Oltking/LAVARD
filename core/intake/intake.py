"""Verify-first intake: restate the goal, list assumptions, define success criteria (§4.6).

Never spend before the goal is verified. Uses the model when configured; otherwise a
deterministic heuristic so the demo runs offline. Both return an `IntakeResult`.
"""

from __future__ import annotations

import re

from core.llm.client import ModelClient
from core.schemas import IntakeResult

_INTAKE_SYSTEM = (
    "You are LAVARD's intake controller. Restate the user's goal precisely, surface the "
    "assumptions you are making, define concrete measurable success criteria, and list any "
    "open questions whose answers would change the plan. Do NOT invent scope. "
    'Reply as JSON: {"restated_goal": str, "assumptions": [str], "success_criteria": [str], '
    '"open_questions": [str]}.'
)

# Cheap signals for the offline heuristic.
_DEADLINE_HINT = re.compile(r"\b(by|before|within|deadline|due)\b", re.I)
_BUDGET_HINT = re.compile(r"\b(budget|under|\$\d|cost|cheap|spend)\b", re.I)


def verify_goal(goal: str, model: ModelClient | None = None) -> IntakeResult:
    goal = goal.strip()
    if not goal:
        raise ValueError("Goal is empty.")
    model = model or ModelClient()
    if model.is_configured:
        try:
            data = model.complete_json(_INTAKE_SYSTEM, f"Goal: {goal}", tier="complex")
            return IntakeResult(**{**_heuristic(goal).to_dict(), **_clean(data)})
        except Exception:
            # Fall back rather than fail the job if the model misbehaves.
            return _heuristic(goal)
    return _heuristic(goal)


def _clean(data: dict) -> dict:
    """Keep only known keys with the right types."""
    out: dict = {}
    for k in ("restated_goal",):
        if isinstance(data.get(k), str):
            out[k] = data[k]
    for k in ("assumptions", "success_criteria", "open_questions"):
        v = data.get(k)
        if isinstance(v, list):
            out[k] = [str(x) for x in v if str(x).strip()]
    return out


def _heuristic(goal: str) -> IntakeResult:
    """Deterministic offline intake — conservative, verifiable, no invented scope."""
    restated = goal if goal[-1] in ".!?" else goal + "."
    restated = "Deliver on: " + restated

    assumptions = [
        "The goal as stated is complete; no hidden sub-goals beyond the words given.",
        "Standard quality and correctness expectations apply to each deliverable.",
    ]
    open_questions: list[str] = []
    if not _DEADLINE_HINT.search(goal):
        open_questions.append("No deadline was stated — is there a target completion time?")
    if not _BUDGET_HINT.search(goal):
        open_questions.append(
            "No budget was stated — confirm the spending cap (interim default applies)."
        )

    success_criteria = [
        f"Every explicit deliverable implied by '{goal.rstrip('.')}' is produced and reviewed.",
        "Each sub-task's own success criteria are met before the job is signed off.",
        "Total spend stays within the approved budget cap.",
    ]
    return IntakeResult(
        restated_goal=restated,
        assumptions=assumptions,
        success_criteria=success_criteria,
        open_questions=open_questions,
    )
