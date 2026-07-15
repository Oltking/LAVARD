"""Per-job report (§4.6): what was done, who was hired, cost, memory reused, savings — plus the
immutable audit log with an integrity check. Doubles as the trust surface and the demo narrative.
"""

from __future__ import annotations

from typing import Any

from core.config import get_settings
from core.store import get_store


def build_report(job_id: str) -> dict[str, Any]:
    store = get_store()
    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Unknown job {job_id}")
    hires = [h for h in store.get_hires(job_id) if not h["node_key"].startswith("room-helper")]
    room_hires = [h for h in store.get_hires(job_id) if h["node_key"].startswith("room-helper")]
    audit = store.get_audit(job_id)

    hired_cost = sum(h["amount_usd"] for h in hires + room_hires)
    released = sum(h["amount_usd"] for h in hires + room_hires if h["status"] == "released")

    memory_skips = [a for a in audit if a["kind"] == "hire_skipped_memory"]
    # savings estimate: avoided hires priced at the average of actual hires (fallback $12).
    avg_price = (hired_cost / len(hires + room_hires)) if (hires + room_hires) else 12.0
    est_savings = round(len(memory_skips) * avg_price, 2)
    # realized (not estimated) Router cache/dedup savings recorded on room close.
    router_saved = round(sum(a["data"].get("router_saved_usd", 0.0)
                             for a in audit if a["kind"].startswith("room_")), 4)
    # realized TheHouse aggregation savings on Agent-to-MCP pay-per-call executions.
    mcp_execs = [a for a in audit if a["kind"] == "mcp_executed"]
    thehouse_saved = round(sum(a["data"].get("saved", 0.0) for a in mcp_execs), 4)

    audit_ok = store.verify_audit(job_id)
    if not audit_ok:
        from core.observability import alert

        alert("audit_verification_failed", severity="critical", job_id=job_id)

    return {
        "job_id": job_id,
        "owner_id": job["owner_id"],
        "goal": job["goal"],
        "restated_goal": job["restated_goal"],
        "status": job["status"],
        "success_criteria": job["success_criteria"],
        "path_mode": job.get("path_mode", ""),
        "path_reason": job.get("path_reason", ""),
        "task_nodes": len(job["nodes"]),
        "hires": [
            {"in_room_id": h["in_room_id"], "agent": h["agent_name"], "capability": h["capability"],
             "amount_usd": h["amount_usd"], "trust": h["trust"], "status": h["status"]}
            for h in hires + room_hires
        ],
        "hired_cost_usd": round(hired_cost, 2),
        "released_usd": round(released, 2),
        "memory_reused_count": len(memory_skips),
        "estimated_savings_usd": est_savings,
        "savings_basis": "avoided hires × average hire price (heuristic estimate, not billed)",
        "router_saved_usd": router_saved,
        "mcp_calls": len(mcp_execs),
        "thehouse_saved_usd": thehouse_saved,
        "audit_log": audit,
        "audit_verified": audit_ok,
        "audit_seal": ("dev-key (set LAVARD_AUDIT_KEY in prod)"
                       if get_settings().audit_key_is_default else "sealed"),
    }
