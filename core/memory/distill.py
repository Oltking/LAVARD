"""DISTILL on room close (§4.5): redact -> store durable facts (confidence + freshness) + a
reusable Playbook (goal shape -> roles hired -> pitfalls -> decomposition skeleton).

Everything is redacted at capture (core/memory/redact.py). Owner-scoped to the job's owner.
"""

from __future__ import annotations

from typing import Any

from core.memory.redact import redact
from core.memory.store import get_memory
from core.store import get_store


def distill_job(job_id: str, transcript: Any | None = None) -> dict:
    store = get_store()
    mem = get_memory()
    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Unknown job {job_id}")
    owner = job["owner_id"]
    nodes = job["nodes"]
    hires = store.get_hires(job_id)

    specialist_nodes = [n for n in nodes
                        if n["needs_hire"] and n["capability"] != "coordination"]
    roles = sorted({h["capability"] for h in hires} | {n["capability"] for n in specialist_nodes})

    pitfalls: list[str] = []
    if transcript is not None:
        methods = getattr(transcript, "resolutions", {}) or {}
        if methods.get("hired_new"):
            pitfalls.append("Expect a capability gap needing an extra specialist hired mid-room.")
        if methods.get("polled_room"):
            pitfalls.append("Some sub-tasks depend on a peer's output — sequence them.")
        if any("blocked" in nc for nc in getattr(transcript, "nodes_completed", [])):
            pitfalls.append("A node blocked on a duplicate question — pre-seed its context.")
    if not pitfalls:
        pitfalls = ["Watch for early stalls needing domain grounding before work starts."]

    # DAG skeleton: capability→capability edges from the plan's dependency structure, so a reused
    # blueprint carries the *shape* of the workflow, not just a flat list of steps.
    cap_by_key = {n["key"]: n["capability"] for n in nodes}
    dag_edges: list[list[str]] = []
    for n in specialist_nodes:
        for dep in n.get("depends_on", []):
            dep_cap = cap_by_key.get(dep)
            if dep_cap and dep_cap != "coordination":
                edge = [dep_cap, n["capability"]]
                if edge not in dag_edges:
                    dag_edges.append(edge)

    # Ideal crew: the agents that actually delivered, ranked by their reputation score, so a reused
    # blueprint pre-selects known-good specialists instead of re-searching cold.
    from core.reputation import score_agent

    crew: list[dict] = []
    seen_agents: set[str] = set()
    for h in hires:
        if h["agent_id"] in seen_agents or h["status"] not in ("hired", "released"):
            continue
        seen_agents.add(h["agent_id"])
        rep = score_agent(h["agent_id"], trust=h.get("trust"))
        crew.append({"capability": h["capability"], "agent_id": h["agent_id"],
                     "name": h["agent_name"], "score": rep.overall})
    crew.sort(key=lambda c: c["score"], reverse=True)

    goal_clean, _ = redact(job["goal"])
    # Redact node titles too — they are goal fragments and can carry secrets/PII. (Previously stored
    # raw, contradicting this module's "everything is redacted at capture" contract.)
    clean_titles = [redact(n["title"])[0] for n in specialist_nodes]
    pb = mem.add_playbook(
        owner, goal_shape=goal_clean, roles=roles, pitfalls=pitfalls,
        node_titles=clean_titles, dag_edges=dag_edges, crew=crew,
    )

    facts_stored = 0
    redactions: list[str] = []
    for n in specialist_nodes:
        text, kinds = redact(
            f"Reusable '{n['capability']}' deliverable on file: {n['title']}."
        )
        redactions += kinds
        # topic = the node title so a similar future node matches; embed on title+capability.
        mem.add_fact(owner, topic=n["title"], text=text, domain=n["capability"],
                     confidence=0.85, embed_text=f"{n['title']} {n['capability']}")
        facts_stored += 1

    # Tier-2 aggregate learning: contribute ONLY the anonymized capability shape (no owner, no
    # goal, no deliverables) to the global co-occurrence model. The blueprint itself stays private.
    from core.insights import record_workflow

    record_workflow(roles)

    from core.governance import audit

    audit(job_id, "memory_distilled", "LAVARD",
          f"Distilled 1 playbook + {facts_stored} facts", {"playbook_id": pb.id, "roles": roles})
    return {
        "playbook_id": pb.id,
        "owner_id": owner,
        "roles": roles,
        "facts_stored": facts_stored,
        "pitfalls": pitfalls,
        "redactions": sorted(set(redactions)),
    }
