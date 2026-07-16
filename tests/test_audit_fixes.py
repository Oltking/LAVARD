"""Regression tests for the audit fixes (HIGH-1/2/3, MED-1/2/3, LOW-1/2)."""

from core.foreman import hire_for_job, sign_off
from core.memory.redact import redact
from core.memory.store import get_memory
from core.room.knowledge import MemoryBackedKnowledge
from core.room.referee import Referee
from core.router.cache import InMemoryVectorIndex, SemanticCache
from core.router.router import Router
from core.service import submit_goal
from core.store import get_store


# --- HIGH-3: sealed audit head detects tail truncation ---------------------------------------
def test_audit_tail_truncation_is_detected():
    view = submit_goal("Research rollups then write a post")
    hire_for_job(view.id)
    store = get_store()
    assert store.verify_audit(view.id) is True
    log = store.get_audit(view.id)
    last_seq = log[-1]["seq"]
    with store._connect() as c:
        c.execute("DELETE FROM audit_log WHERE job_id=? AND seq=?", (view.id, last_seq))
    assert store.verify_audit(view.id) is False  # truncation now caught by the sealed head


def test_audit_head_forgery_is_detected():
    view = submit_goal("Audit a contract then document it")
    hire_for_job(view.id)
    store = get_store()
    # Attacker truncates AND rewrites the head to match the shorter chain — still fails because
    # the HMAC signature can't be forged without the key.
    log = store.get_audit(view.id)
    with store._connect() as c:
        c.execute("DELETE FROM audit_log WHERE job_id=? AND seq=?", (view.id, log[-1]["seq"]))
        new_last = store.get_audit(view.id)[-1]
        c.execute("UPDATE audit_head SET length=?, head_hash=? WHERE job_id=?",
                  (len(log) - 1, new_last["hash"], view.id))
    assert store.verify_audit(view.id) is False


# --- HIGH-2: room first-responder reads the real persistent memory ----------------------------
def test_memory_backed_knowledge_reads_real_store():
    mem = get_memory()
    mem.add_fact("owner-h2", topic="rollup throughput",
                 text="Rollup X sustains ~4000 TPS on X Layer.", domain="research",
                 confidence=0.9, embed_text="rollup throughput research")
    k = MemoryBackedKnowledge("owner-h2")
    hit = k.lookup("rollup throughput")
    assert hit is not None and "from memory" in hit

    # owner-scoping: a different owner sees nothing (and no seed) -> None
    assert MemoryBackedKnowledge("someone-else").lookup("rollup throughput") is None


# --- HIGH-1: the Router actually mediates the room's spend ------------------------------------
def test_router_is_wired_into_room_and_saves_on_reuse():
    from core.room import run_room
    goal = "Research rollups, then write a post, then design an infographic"
    shared = Router()
    # Two SEPARATE jobs with the same work, through the SAME router: the second job's room hits the
    # first's semantic cache → realized savings. (Re-running one job's room is now an idempotent
    # no-op, so cross-job reuse is the correct scenario for the shared cache.)
    v1 = submit_goal(goal, "router_owner_a")
    hire_for_job(v1.id)
    t1 = run_room(v1.id, demo=True, router=shared)
    assert "router_saved_usd" in t1.to_dict()

    v2 = submit_goal(goal, "router_owner_b")
    hire_for_job(v2.id)
    t2 = run_room(v2.id, demo=True, router=shared)
    assert t2.router_saved_usd > 0


# --- MED-2: critical steps bypass cache/dedup -------------------------------------------------
def test_critical_step_bypasses_cache():
    r = Router()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return "verdict"

    r.ask("release escrow and pay the vendor", compute, agent_id="a")  # matches _CRITICAL
    r.ask("release escrow and pay the vendor", compute, agent_id="b")
    assert calls["n"] == 2  # never served from cache


def test_routine_step_is_cached():
    r = Router()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return "answer"

    r.ask("summarize the meeting notes", compute, tier="routine", agent_id="a")
    r.ask("summarize the meeting notes", compute, tier="routine", agent_id="b")
    assert calls["n"] == 1  # second is a dedup collapse


# --- MED-3: settlement routes to the funded payee, not the agent_id ---------------------------
def test_signoff_releases_to_stored_payee():
    view = submit_goal("Audit a contract then write documentation")
    outcomes = hire_for_job(view.id)
    assert any(o.decision == "hired" for o in outcomes)
    store = get_store()
    hires = [h for h in store.get_hires(view.id) if h["status"] == "hired"]
    assert all(h["payee"] for h in hires)  # a funded payee address was persisted
    res = sign_off(view.id)
    released_payees = {r["payee"] for r in res.released}
    # every released escrow paid the stored (funded) payee address, not the agent_id
    assert released_payees == {h["payee"] for h in hires}


# --- LOW-1: redaction precision ---------------------------------------------------------------
def test_redacts_valid_seed_phrase_but_not_prose():
    seed = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    clean, kinds = redact(seed)
    assert "seed_phrase" in kinds and "[REDACTED:seed_phrase]" in clean

    prose = ("we then met with the team and decided that the new plan would be good for "
             "us and them over the next few weeks as we ship the next set of small fixes")
    clean2, kinds2 = redact(prose)
    assert "seed_phrase" not in kinds2 and clean2 == prose


def test_redacts_real_secrets():
    key = "sk-" + "abcdefghijklmnop1234"          # placeholder, assembled so no literal is scanned
    txt = "key " + key + " and 0x" + "a" * 64
    clean, kinds = redact(txt)
    assert "api_key" in kinds and "private_key" in kinds
    assert key not in clean


# --- LOW-2: cache is bounded ------------------------------------------------------------------
def test_cache_index_evicts_when_full():
    idx = InMemoryVectorIndex(max_entries=10)
    cache = SemanticCache(index=idx)
    for i in range(25):
        cache.put(f"query number {i}", f"answer {i}", cost=0.005)
    assert len(idx.entries) == 10  # bounded; oldest evicted
