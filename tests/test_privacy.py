"""Privacy invariants (docs/privacy.md): private stays owner-scoped, aggregate learning carries no
user content, the public exchange never shares personalized work, suggestions never spend."""

from core.insights import global_followups, record_workflow
from core.memory import (
    blueprint_for_goal,
    distill_job,
    get_memory,
    memory_answer_for_node,
    preferred_crew_for_goal,
)
from core.predict import predict_for_job
from core.privacy import assert_aggregate_safe
from core.router.exchange import IntelligenceExchange
from core.service import submit_goal
from core.store import get_store


def _completed_job(owner, goal="build a backend then audit the contracts"):
    view = submit_goal(goal, owner)
    store = get_store()
    for cap, agent, name in [("engineering", "eng9", "EngCo"), ("security", "sec9", "SecCo")]:
        store.create_hire(view.id, node_key=f"n_{cap}", agent_id=agent, agent_name=name,
                          in_room_id=f"{cap}::{name}", capability=cap, amount_usd=10.0,
                          trust="high", confidence=0.9, escrow_id="e", payee="p", status="released")
    distill_job(view.id)
    return view.id


# ---- Tier 1: owner A's private memory never reaches owner B --------------------------
def test_blueprint_and_crew_are_owner_scoped():
    goal = "build a backend then audit the contracts"
    _completed_job("alice_priv", goal)
    # alice has a blueprint; bob (never ran it) gets nothing
    assert blueprint_for_goal("alice_priv", goal) is not None
    assert blueprint_for_goal("bob_priv", goal) is None
    assert preferred_crew_for_goal("bob_priv", goal) == {}


def test_facts_are_owner_scoped():
    _completed_job("carol_priv")
    node = {"title": "audit the contracts", "capability": "security"}
    assert memory_answer_for_node("carol_priv", node) is not None
    assert memory_answer_for_node("dave_priv", node) is None   # different owner → no leak


# ---- Tier 2: aggregate learning carries no user content -----------------------------
def test_aggregate_record_rejects_user_content():
    assert_aggregate_safe({"roles": ["engineering", "security"]})   # ok: capability shape
    for bad in ({"owner_id": "x"}, {"goal": "build X"}, {"crew": []}, {"node_titles": ["y"]}):
        try:
            assert_aggregate_safe(bad)
            assert False, f"should have rejected {bad}"
        except ValueError:
            pass


def test_insights_store_holds_only_capabilities():
    record_workflow(["engineering", "security", "qa"])
    fu = global_followups("engineering")
    assert "security" in fu and "qa" in fu
    # the anonymized blueprint shape is aggregate-safe
    _completed_job("erin_priv")
    pb = blueprint_for_goal("erin_priv", "build a backend then audit the contracts")
    assert_aggregate_safe(pb.anonymized())      # must not raise


# ---- Tier 3: the public exchange never shares personalized work ----------------------
def test_personalized_query_never_enters_shared_cache():
    ex = IntelligenceExchange(ttl_s=100)
    a1, s1 = ex.fetch("audit_ai", "audit my contract", lambda: "A", 5.0, "u1")
    a2, s2 = ex.fetch("audit_ai", "audit my contract", lambda: "B", 5.0, "u2")
    assert not s1 and not s2 and a1 != a2         # isolated, never shared
    assert ex.active_keys() == 0                  # nothing personalized cached for reuse


def test_shared_answer_does_not_leak_caller_identity():
    ex = IntelligenceExchange(ttl_s=100)
    ex.fetch("price_ai", "current BTC price", lambda: "$65k", 0.01, "secret_caller_1")
    ans, shared = ex.fetch("price_ai", "current BTC price", lambda: "x", 0.01, "caller_2")
    assert shared and ans == "$65k"
    assert "secret_caller_1" not in ans           # the answer carries no identity


# ---- #6: suggestions never spend ----------------------------------------------------
def test_suggestions_do_not_spend_or_hire():
    view = submit_goal("build a backend service", "frank_priv")
    store = get_store()
    before = len(store.get_hires(view.id))
    predict_for_job(view.id)
    after = len(store.get_hires(view.id))
    assert before == after                        # no hire created by predicting
