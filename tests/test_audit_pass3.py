"""Third deep-audit pass: operation idempotency + input validation + numeric defense."""

import pytest

from core.foreman import hire_for_job
from core.room import run_room
from core.service import MAX_GOAL_CHARS, submit_goal
from core.store import get_store


def test_repeated_hire_does_not_double_escrow():
    v = submit_goal("research the market then build a backend", "p3_owner")
    o1 = hire_for_job(v.id)
    o2 = hire_for_job(v.id)                                   # retried / duplicate call
    hired1 = sum(1 for o in o1 if o.decision == "hired")
    assert hired1 > 0
    assert all(o.decision != "hired" for o in o2)            # nothing hired again
    assert all(o.decision == "skipped_already_hired"
               for o in o2 if o.node_key in {x.node_key for x in o1 if x.decision == "hired"})
    # store holds exactly the first round of hires — no duplicate escrow
    assert len(get_store().get_hires(v.id)) == hired1


def test_repeated_room_is_idempotent_no_op():
    v = submit_goal("research the market then build a backend", "p3_room")
    hire_for_job(v.id)
    t1 = run_room(v.id, demo=True)
    hires_after = len(get_store().get_hires(v.id))
    t2 = run_room(v.id, demo=True)                            # retried room run
    assert t2.spend_usd == 0.0                                # no re-charge
    assert len(get_store().get_hires(v.id)) == hires_after    # no duplicate helper escrow


def test_giant_goal_is_rejected():
    with pytest.raises(ValueError, match="too long"):
        submit_goal("build " + "x" * (MAX_GOAL_CHARS + 1), "dos_owner")


def test_non_positive_price_never_charges_negative():
    from thehouse.core.models import ASPEntry, ASPMode
    from thehouse.core.pricing import ceiling_price, settled_price
    for p in (0.0, -5.0):
        e = ASPEntry(asp_id="a", tool_name="a.go", mode=ASPMode.A_LLM,
                     original_price_per_call=p, thehouse_price=p * 0.8)
        assert ceiling_price(e) == 0.0
        assert settled_price(e, 1) == 0.0 and settled_price(e, 2) == 0.0
