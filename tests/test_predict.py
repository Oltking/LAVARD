"""Predictive next-task suggestions: domain prior on day one, personalized by history, and it
NEVER spends (pure memory read, prepared suggestions only)."""

from core.memory import distill_job, get_memory
from core.predict import predict_for_job, predict_next
from core.service import submit_goal
from core.store import get_store


def test_domain_prior_suggests_audit_after_build_day_one():
    # brand-new owner, no history → domain prior still gives useful follow-ons
    sugg = predict_next("fresh_owner", ["engineering", "finance"])
    caps = {s.capability for s in sugg}
    assert "security" in caps        # build/defi → audit
    assert all(s.prepared for s in sugg)
    assert all(s.capability not in ("engineering", "finance") for s in sugg)  # not what we did


def test_history_personalizes_and_preselects_crew():
    owner = "pred_owner"
    view = submit_goal("build a backend and audit the contracts", owner)
    store = get_store()
    for cap, agent, name in [("engineering", "eng1", "EngCo"), ("security", "sec1", "SecCo")]:
        store.create_hire(view.id, node_key=f"n_{cap}", agent_id=agent, agent_name=name,
                          in_room_id=f"{cap}::{name}", capability=cap, amount_usd=10.0,
                          trust="high", confidence=0.9, escrow_id="e", payee="p", status="released")
    distill_job(view.id)

    sugg = predict_next(owner, ["engineering"], mem=get_memory())
    sec = next((s for s in sugg if s.capability == "security"), None)
    assert sec is not None
    # crew pre-selected from memory (no marketplace / no spend)
    assert sec.preselected and sec.preselected["agent_id"] == "sec1"


def test_predict_for_job_returns_prepared_no_spend():
    view = submit_goal("design a landing page", "pred_owner3")
    sugg = predict_for_job(view.id)
    assert all(s.prepared and s.confidence >= 0 for s in sugg)
