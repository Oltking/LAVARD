"""Reusable Workflow Blueprints: distill captures DAG + ideal crew; reuse pre-selects crew;
the anonymized export carries the shape without owner/data."""

from core.memory import blueprint_for_goal, distill_job, get_memory, preferred_crew_for_goal
from core.service import submit_goal
from core.store import get_store


def _seed_completed_job(owner="bp_owner"):
    view = submit_goal("research competitors then design a logo and build a landing page", owner)
    job_id = view.id
    store = get_store()
    # simulate hires that delivered, so distill has a crew
    for cap, agent, name in [("research", "agent_r", "ResearchCo"),
                             ("design", "agent_d", "DesignCo"),
                             ("engineering", "agent_e", "EngCo")]:
        store.create_hire(job_id, node_key=f"n_{cap}", agent_id=agent, agent_name=name,
                          in_room_id=f"{cap}::{name}", capability=cap, amount_usd=10.0,
                          trust="high", confidence=0.9, escrow_id="e", payee="p", status="released")
    return job_id, owner


def test_distill_captures_dag_and_crew():
    job_id, owner = _seed_completed_job()
    distill_job(job_id)
    pb = blueprint_for_goal(owner, "research competitors then design a logo and build a landing page")
    assert pb is not None
    assert pb.crew and {c["agent_id"] for c in pb.crew} >= {"agent_r", "agent_d", "agent_e"}
    # crew sorted by reputation score, each entry explainable
    assert all("capability" in c and "score" in c for c in pb.crew)


def test_preferred_crew_map_and_reuse():
    job_id, owner = _seed_completed_job("bp_owner2")
    distill_job(job_id)
    crew = preferred_crew_for_goal(owner, "research competitors then design a logo and build a landing page")
    assert crew.get("research") == "agent_r"
    assert crew.get("design") == "agent_d"


def test_anonymized_export_is_capability_shape_only():
    job_id, owner = _seed_completed_job("bp_owner3")
    distill_job(job_id)
    pb = blueprint_for_goal(owner, "research competitors then design a logo and build a landing page")
    anon = pb.anonymized()
    # only non-identifying capability shape — no owner, goal, project titles, or crew
    assert set(anon) == {"roles", "dag_edges"}
    assert "owner_id" not in anon and "goal_shape" not in anon
    assert "node_titles" not in anon and "crew" not in anon
