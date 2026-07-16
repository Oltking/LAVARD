import time

from core.foreman import hire_for_job
from core.memory import distill_job, get_memory
from core.memory.redact import redact
from core.service import submit_goal

OWNER = "owner-phase7"          # dedicated owner so memory stays isolated from other tests
GOAL = "Research the top 3 rollups, then write a comparison blog post, then audit security"


def test_redaction_strips_secrets():
    key = "sk-" + "ABCD1234EFGH5678IJKL"          # placeholder, assembled so no literal is scanned
    clean, kinds = redact("key " + key + " and 0x" + "a" * 64 + " email a@b.com")
    assert key not in clean
    assert "0x" + "a" * 64 not in clean
    assert "a@b.com" not in clean
    assert {"api_key", "private_key", "email"}.issubset(set(kinds))


def test_distill_stores_playbook_and_facts_redacted():
    view = submit_goal(GOAL + " with password: hunter2", owner_id=OWNER)
    hire_for_job(view.id)
    summary = distill_job(view.id)
    assert summary["facts_stored"] >= 1
    assert "password" in summary["redactions"]           # secret redacted at capture
    mem = get_memory()
    assert mem.list_playbooks(OWNER)
    assert mem.match_playbook(OWNER, GOAL) is not None


def test_second_similar_job_reuses_playbook_and_skips_a_hire():
    owner = "owner-reuse"
    # Job 1: run and distill so memory is populated for this owner.
    v1 = submit_goal(GOAL, owner_id=owner)
    hire_for_job(v1.id)
    distill_job(v1.id)

    # Job 2: a similar goal, same owner (same "then"-shape so nodes line up).
    v2 = submit_goal("Research 3 rollups, then write a comparison post, then audit security",
                     owner_id=owner)
    assert v2.reused_playbook is not None, "intake should reuse a known-good Playbook"

    outcomes = hire_for_job(v2.id)
    skipped = [o for o in outcomes if o.decision == "skipped_memory"]
    assert len(skipped) >= 1, "at least one hire should be skipped because memory answers it"


def test_memory_is_owner_scoped():
    submit_goal(GOAL, owner_id="owner-A")
    hire_for_job(submit_goal(GOAL, owner_id="owner-A").id)
    distill_job(submit_goal(GOAL, owner_id="owner-A").id)  # noqa: just populate owner-A
    mem = get_memory()
    # a different owner sees none of owner-A's facts
    assert mem.search_facts("owner-B-empty", GOAL) == []


def test_stale_facts_are_not_reused():
    mem = get_memory()
    old = time.time() - 60 * 24 * 3600      # 60 days old
    mem.add_fact("owner-stale", topic="research", text="stale research output",
                 domain="research", confidence=0.9, freshness_ts=old)
    from core.memory import memory_answer_for_node

    node = {"title": "Research the top 3 rollups", "capability": "research"}
    assert memory_answer_for_node("owner-stale", node) is None  # too old to reuse
