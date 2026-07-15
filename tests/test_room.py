from core.foreman import hire_for_job
from core.room import run_room
from core.room.blackboard import Blackboard
from core.room.knowledge import PortableMemory
from core.room.models import ANSWERED_FROM_MEMORY, HIRED_NEW, POLLED_ROOM, question
from core.room.referee import Referee, RefereeStop
from core.room.agents import MockRoomAgent
from core.room.controller import FirstResponder
from core.room.models import RoomTranscript
from core.service import submit_goal
from core.store import get_store


def _staffed_job():
    view = submit_goal(
        "Research rollups, then write a blog post, then design an infographic, then audit security")
    hire_for_job(view.id)
    return view.id


def test_first_responder_three_branches():
    job_id = _staffed_job()
    mem = PortableMemory({"topicM": "known answer"})
    bb = Blackboard()
    peer = MockRoomAgent("peer::X", "content", [], expertise={"topicP"})
    asker = MockRoomAgent("asker::Y", "research", [])
    participants = {"peer::X": peer, "asker::Y": asker}
    t = RoomTranscript(job_id)
    fr = FirstResponder(job_id, mem, bb, participants, t)

    _, m1, _ = fr.resolve("asker::Y", question("q", "topicM", "research"))
    assert m1 == ANSWERED_FROM_MEMORY
    _, m2, _ = fr.resolve("asker::Y", question("q", "topicP", "content"))
    assert m2 == POLLED_ROOM
    _, m3, cost = fr.resolve("asker::Y", question("q", "topicGap", "legal"))
    assert m3 == HIRED_NEW and cost > 0
    assert t.hired_in_room  # a new specialist was hired into the room


def test_demo_room_exercises_all_branches_and_completes():
    job_id = _staffed_job()
    t = run_room(job_id, demo=True)
    assert t.status == "completed"
    for method in (ANSWERED_FROM_MEMORY, POLLED_ROOM, HIRED_NEW):
        assert t.resolutions.get(method, 0) >= 1, f"branch {method} not exercised"


def test_kill_switch_freezes_room():
    job_id = _staffed_job()
    t = run_room(job_id, demo=True, freeze_before_turn=2)
    assert t.status == "frozen"
    assert any("HALTED" in turn.action for turn in t.turns)


def test_prefrozen_room_stops_immediately():
    job_id = _staffed_job()
    get_store().freeze_room(job_id)
    t = run_room(job_id, demo=True, clear_frozen=False)
    assert t.status == "frozen"


def test_budget_ceiling_never_overshot():
    # A tiny budget must NOT be blown: the pre-charge affordability guard (MED-1) refuses an
    # unaffordable mid-room hire rather than committing it and overshooting the ceiling.
    job_id = _staffed_job()
    t = run_room(job_id, demo=True, budget_usd=0.25)
    assert t.spend_usd <= 0.25 + 1e-9
    # graceful degradation: no multi-dollar helper was hired into the room past the ceiling
    assert all(h["amount_usd"] <= 0.25 for h in t.hired_in_room)


def test_budget_hard_stops_when_already_over():
    # If the meter is already at/over the ceiling (e.g. carried in from a prior run), the room
    # refuses to start new work at all.
    from core.room.referee import Referee, RefereeStop
    ref = Referee("j", budget_usd=0.25)
    ref.spend = 0.25
    try:
        ref.check_budget()
        assert False, "should have raised"
    except RefereeStop as s:
        assert s.reason == "budget_exceeded"


def test_referee_turn_and_duplicate_limits():
    ref = Referee("j", budget_usd=100, room_turn_limit=100, agent_turn_limit=2)
    ref.turn("a")
    ref.turn("a")
    try:
        ref.turn("a")
        assert False, "should have raised"
    except RefereeStop as s:
        assert s.reason == "turn_limit"
    assert ref.is_duplicate_question("b", "t") is False
    assert ref.is_duplicate_question("b", "t") is True
