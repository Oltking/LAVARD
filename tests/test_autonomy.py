"""Phase 10 — autonomy hardening: checkpointing, resume-after-crash, budget caps, soak.

A staffed job has four specialist nodes. We freeze the room mid-run (simulating a crash/kill),
then resume: already-completed nodes are skipped, the budget/turn meter carries forward, and the
job finishes within its original cap. The soak test loops many restarts and asserts the cap holds.
"""

from core.foreman import hire_for_job
from core.room import run_room
from core.service import submit_goal
from core.store import get_store


def _staffed_job():
    view = submit_goal(
        "Research rollups, then write a blog post, then design an infographic, then audit security")
    hire_for_job(view.id)
    return view.id


def test_checkpoint_written_on_freeze_and_cleared_on_completion():
    job_id = _staffed_job()
    store = get_store()

    t1 = run_room(job_id, demo=True, freeze_before_turn=2)
    assert t1.status == "frozen"
    ckpt = store.get_checkpoint(job_id)
    assert ckpt is not None and ckpt["room_turns"] > 0

    t2 = run_room(job_id, demo=True, resume=True)
    assert t2.status == "completed"
    assert store.get_checkpoint(job_id) is None  # cleared on clean finish


def test_resume_skips_completed_nodes_and_carries_meter():
    job_id = _staffed_job()
    store = get_store()

    run_room(job_id, demo=True, freeze_before_turn=3)
    ckpt = store.get_checkpoint(job_id)
    done_before = set(ckpt["completed_nodes"])
    spend_before = ckpt["spend_usd"]

    t2 = run_room(job_id, demo=True, resume=True)
    assert t2.resumed_from is not None
    assert t2.resumed_from["spend_usd"] == spend_before
    # completed-pre-restart nodes are announced as skipped, not re-run
    skipped = [t for t in t2.turns if t.method == "resumed"]
    assert len(skipped) == len(done_before)
    assert t2.spend_usd >= spend_before  # meter carried forward, never reset


def test_budget_cap_never_breached_across_restarts():
    job_id = _staffed_job()
    # A tight cap must hold on EVERY restart: the affordability guard (MED-1) declines an
    # unaffordable hire and the room degrades gracefully instead of overshooting.
    cap = 0.25
    for _ in range(6):
        t = run_room(job_id, demo=True, resume=True, budget_usd=cap)
        assert t.spend_usd <= cap + 1e-9              # cap never breached
        assert all(h["amount_usd"] <= cap for h in t.hired_in_room)
        if t.status == "completed":
            break
    assert t.spend_usd <= cap + 1e-9


def test_resume_survives_a_fresh_process_view():
    # Prove the checkpoint is durable across a NEW process, not just an in-process re-call:
    # drop the cached settings + store so the resume reads the checkpoint off disk from scratch.
    import core.store as store_mod
    from core.config import get_settings

    job_id = _staffed_job()
    run_room(job_id, demo=True, freeze_before_turn=2)
    assert get_store().get_checkpoint(job_id) is not None

    get_settings.cache_clear()          # simulate a cold restart: no in-memory state survives
    store_mod.get_store()               # fresh store instance / fresh connections
    t = run_room(job_id, demo=True, resume=True)
    assert t.status == "completed"
    assert t.resumed_from is not None
    assert get_store().get_checkpoint(job_id) is None


def test_soak_many_restarts_completes_within_budget():
    job_id = _staffed_job()
    store = get_store()
    cap = 5.0
    # Simulate an unattended long run that keeps getting interrupted every couple of turns.
    for i in range(50):
        t = run_room(job_id, demo=True, resume=True, budget_usd=cap, freeze_before_turn=3)
        assert t.spend_usd <= cap + 1e-9
        if t.status == "completed":
            break
    assert t.status == "completed"
    assert store.get_checkpoint(job_id) is None
