from core.foreman import hire_for_job, sign_off
from core.governance import Action, PermissionPolicy, build_report, review_action
from core.governance.review import APPROVE, APPROVE_WITH_EDITS, DENY, ESCALATE
from core.service import submit_goal
from core.store import get_store


def test_read_action_is_approved():
    v = review_action(Action("read", "Read the marketplace listings"))
    assert v.verdict == APPROVE and v.will_execute


def test_hire_within_ceiling_is_ask_once_approved():
    v = review_action(Action("spend", "Pay a small hire", amount_usd=10, target="X"),
                      PermissionPolicy(auto_spend_ceiling_usd=50))
    assert v.verdict == APPROVE_WITH_EDITS and v.will_execute


def test_large_spend_escalates_not_executed():
    v = review_action(Action("spend", "Pay a big invoice", amount_usd=500, target="X"),
                      PermissionPolicy(auto_spend_ceiling_usd=50))
    assert v.verdict == ESCALATE and not v.will_execute


def test_destructive_action_always_escalates():
    v = review_action(Action("grant_scope", "delete all onchain funds", target="wallet"))
    assert v.verdict == ESCALATE and not v.will_execute


def test_unnecessary_action_denied():
    v = review_action(Action("hire", "Hire a redundant agent", required=False))
    assert v.verdict == DENY and not v.will_execute


def test_audit_log_is_appended_and_tamper_evident():
    view = submit_goal("Research rollups then write a post")
    hire_for_job(view.id)
    sign_off(view.id)
    store = get_store()
    log = store.get_audit(view.id)
    kinds = {e["kind"] for e in log}
    assert "job_created" in kinds and "hire" in kinds and "payment_released" in kinds
    assert store.verify_audit(view.id) is True

    # tamper with one entry -> chain verification fails
    with store._connect() as c:
        c.execute("UPDATE audit_log SET detail='HACKED' WHERE job_id=? AND seq=1", (view.id,))
    assert store.verify_audit(view.id) is False


def test_report_renders_with_hires_and_savings():
    view = submit_goal("Audit a contract then write documentation")
    hire_for_job(view.id)
    r = build_report(view.id)
    assert r["hires"]
    assert r["hired_cost_usd"] > 0
    assert "audit_log" in r and r["audit_verified"] is True
    assert "estimated_savings_usd" in r
