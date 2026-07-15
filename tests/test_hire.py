from core.foreman import hire_for_job, necessity_test, sign_off
from core.service import submit_goal
from core.store import get_store
from onchain.payments import MockPayments, OPEN, RELEASED


def test_necessity_test_skips_coordination():
    needed, _ = necessity_test({"capability": "coordination", "needs_hire": False})
    assert needed is False
    needed, _ = necessity_test({"capability": "research", "needs_hire": True})
    assert needed is True


def test_hire_opens_escrow_and_assigns_room_ids():
    view = submit_goal("Research 3 rollups then write a comparison post")
    outcomes = hire_for_job(view.id)
    hired = [o for o in outcomes if o.decision == "hired"]
    assert hired, "at least one specialist node should be hired"
    for o in hired:
        assert o.escrow_id and o.escrow_id.startswith("esc_")
        assert o.in_room_id and "::" in o.in_room_id
        assert o.trust in {"high", "medium"}  # low-trust never auto-hired
        assert o.amount_usd is not None
    # the final coordination node is LAVARD's own -> skipped
    assert any(o.decision == "skipped_not_needed" for o in outcomes)


def test_hires_persist_and_signoff_releases_escrow():
    view = submit_goal("Audit a smart contract then write documentation")
    hire_for_job(view.id)
    hires = get_store().get_hires(view.id)
    assert hires and all(h["status"] == "hired" for h in hires)

    result = sign_off(view.id)
    assert result.total_released_usd > 0
    after = get_store().get_hires(view.id)
    assert all(h["status"] == "released" for h in after)


def test_escrow_state_machine_rejects_double_release():
    import pytest

    pay = MockPayments()
    esc = pay.open_escrow("payer", "payee", 10.0, "memo")
    assert esc.status == OPEN
    released = pay.release(esc)
    assert released.status == RELEASED
    with pytest.raises(ValueError):
        pay.release(released)  # cannot release twice
