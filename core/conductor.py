"""The conductor — one entrypoint that runs a job from goal to deliverables, honoring the
cheapest-sufficient path decision (core/intake/router.py).

Before this, a caller had to orchestrate the flow by hand: POST /jobs, then /hire, then /room,
then /signoff. `run_job` is the single driver that does the whole arc and *adapts to the path*:

  direct_mcp   — a low-need goal that is one deterministic tool call: service it pay-per-call
                 through the executor and return the answer. No room. Terminal (already paid).
  single_asp / orchestrate — hire the specialist(s), run the controller-mediated Room to produce
                 the deliverables, then stop at `awaiting_signoff` (releasing escrow is a user
                 action — spending is always-ask, per governance).

The path is a *hint*, not a straitjacket: if a "direct_mcp" goal's best candidate turns out to be
an A2A agent (not a pay-per-call MCP), the hire loop opens escrow and the conductor falls through
to the room path — "however it can logically work." Money is never auto-released.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.foreman import hire_for_job, sign_off
from core.governance import audit
from core.service import get_job, process_job, submit_goal
from core.store import get_store


@dataclass
class JobRun:
    job_id: str
    mode: str                          # the classifier path that was chosen
    status: str                        # completed | awaiting_signoff | escalated | no_op
    reason: str = ""
    answer: str | None = None          # direct_mcp terminal answer
    outcomes: list[dict] = field(default_factory=list)
    room: dict | None = None
    # Total money exposure = committed escrow (hires) + room coordination + pay-per-call MCP.
    # Broken out so nothing is hidden (audit HIGH-3: the headline must not understate real cost).
    spend_usd: float = 0.0
    committed_escrow_usd: float = 0.0  # in escrow for hires, released on sign-off
    coordination_usd: float = 0.0      # room model/routing spend
    mcp_usd: float = 0.0               # Agent-to-MCP pay-per-call
    saved_usd: float = 0.0
    next_action: str | None = None     # what the caller does next (e.g. sign-off)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_job(goal: str, owner_id: str = "default-owner", *, executor=None,
            demo: bool = False, resume: bool = False, auto_signoff: bool = False,
            preference: str = "balanced") -> JobRun:
    """Run a goal end-to-end. `executor` routes paid Agent-to-MCP calls (TheHouse) and real Room
    deliverables; `auto_signoff` releases escrow automatically (off by default — sign-off is the
    user's call); `preference` (cheapest|fastest|smartest|balanced) drives agent selection."""
    view = submit_goal(goal, owner_id)
    job_id = view.id
    # submit_goal processes inline by default; if a queue deferred it, force processing now so the
    # path decision exists before we branch.
    if not view.path_mode:
        process_job(job_id)
        view = get_job(job_id)
    mode = (view.path_mode or "orchestrate") if view else "orchestrate"
    # Reuse a known-good workflow blueprint's crew (memory network effect): pre-select proven
    # specialists instead of cold-searching. Empty when this owner has no matching blueprint yet.
    from core.memory import preferred_crew_for_goal

    preferred_crew = preferred_crew_for_goal(owner_id, goal)
    audit(job_id, "conductor_start", "conductor", f"path={mode}",
          {"mode": mode, "goal": goal, "preference": preference,
           "blueprint_crew": bool(preferred_crew)})

    outcomes = hire_for_job(job_id, executor=executor, preference=preference,
                            preferred_crew=preferred_crew)
    serviced = [o for o in outcomes if o.decision == "serviced_mcp"]
    hired = [o for o in outcomes if o.decision == "hired"]
    escalated = [o for o in outcomes if o.decision.startswith("escalated")]

    mcp_spend = sum(o.amount_usd or 0.0 for o in serviced)
    mcp_saved = sum(o.saved_usd or 0.0 for o in serviced)

    # direct_mcp that actually serviced as pay-per-call → terminal answer, no room, no escrow.
    if mode == "direct_mcp" and serviced and not hired:
        answer = serviced[0].result
        audit(job_id, "conductor_done", "conductor", "direct_mcp answered pay-per-call",
              {"charged": round(mcp_spend, 4), "saved": round(mcp_saved, 4)})
        return JobRun(
            job_id=job_id, mode=mode, status="completed",
            reason="Single deterministic tool call serviced pay-per-call (agent-to-MCP).",
            answer=answer, outcomes=[o.to_dict() for o in outcomes],
            spend_usd=round(mcp_spend, 4), mcp_usd=round(mcp_spend, 4),
            saved_usd=round(mcp_saved, 4), next_action=None)

    # Otherwise produce deliverables in the Room for any escrow hires.
    room_dict = None
    room_spend = 0.0
    room_saved = 0.0
    room_hired_escrow = 0.0
    if hired:
        from core.room import run_room

        transcript = run_room(job_id, demo=demo, resume=resume, executor=executor,
                              preference=preference)
        room_dict = transcript.to_dict()
        room_spend = transcript.spend_usd
        room_saved = getattr(transcript, "router_saved_usd", 0.0)
        # specialists hired MID-ROOM (first-responder) also open escrow — count them too
        room_hired_escrow = sum(h.get("amount_usd", 0.0)
                                for h in getattr(transcript, "hired_in_room", []))

    if hired:
        status = "awaiting_signoff"
        reason = f"{len(hired)} specialist(s) hired; deliverables produced. Awaiting user sign-off."
        next_action = f"POST /jobs/{job_id}/signoff"
        if auto_signoff:
            result = sign_off(job_id)
            status = "completed"
            reason = f"{len(hired)} specialist(s) hired and paid on auto sign-off."
            next_action = None
            audit(job_id, "conductor_signoff", "conductor",
                  f"auto-released ${result.total_released_usd:.2f}", {})
    elif serviced:
        status = "completed"
        reason = "Serviced pay-per-call (agent-to-MCP); nothing to sign off."
        next_action = None
    elif escalated:
        status = "escalated"
        reason = "; ".join(o.reason for o in escalated)
        next_action = "resolve escalation (approve spend / review low-trust candidate)"
    else:
        status = "no_op"
        reason = "No external work was needed (self-serviced or answered from memory)."
        next_action = None

    # Total exposure includes the escrow COMMITTED for hires (released on sign-off) — the largest
    # cost, previously omitted from the headline number (audit HIGH-3).
    committed_escrow = round(sum(o.amount_usd or 0.0 for o in hired) + room_hired_escrow, 4)
    total_spend = round(mcp_spend + room_spend + committed_escrow, 4)
    audit(job_id, "conductor_done", "conductor", f"status={status}",
          {"mode": mode, "total_usd": total_spend, "committed_escrow_usd": committed_escrow,
           "coordination_usd": round(room_spend, 4), "mcp_usd": round(mcp_spend, 4)})
    return JobRun(
        job_id=job_id, mode=mode, status=status, reason=reason,
        outcomes=[o.to_dict() for o in outcomes], room=room_dict,
        spend_usd=total_spend, committed_escrow_usd=committed_escrow,
        coordination_usd=round(room_spend, 4), mcp_usd=round(mcp_spend, 4),
        saved_usd=round(mcp_saved + room_saved, 4),
        next_action=next_action)
