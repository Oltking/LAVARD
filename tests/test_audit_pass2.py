"""Second deep-audit pass fixes: audit-chain concurrency, resource growth, and redaction."""

import tempfile
import threading
import time

from api.security import RateLimiter
from core.router.exchange import IntelligenceExchange
from core.store import SqliteJobStore


def test_audit_chain_survives_concurrent_appends():
    path = tempfile.mkdtemp() + "/audit.db"
    store = SqliteJobStore(f"sqlite:///{path}")
    job_id = store.create_job("goal", "owner")

    def worker():
        for _ in range(25):
            store.append_audit(job_id, "evt", "actor", "d", {})

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = store.get_audit(job_id)
    seqs = [e["seq"] for e in entries]
    assert len(seqs) == len(set(seqs)) == 150      # no duplicate seqs under concurrency
    assert store.verify_audit(job_id) is True      # chain + sealed head intact


class _Req:
    def __init__(self, ip):
        self.headers = {}
        self.client = type("C", (), {"host": ip})()


def test_rate_limiter_prunes_stale_identities():
    rl = RateLimiter(per_minute=5)
    now = 1000.0
    for i in range(50):                            # 50 distinct callers, one hit each
        rl.allow(_Req(f"ip{i}"), now=now)
    assert len(rl._hits) == 50
    # a window later, a new call triggers a prune of everyone gone stale
    rl.allow(_Req("ipNEW"), now=now + 120)
    assert len(rl._hits) == 1                       # only the recent caller remains


def test_intelligence_exchange_evicts_expired():
    ex = IntelligenceExchange(ttl_s=30)
    for i in range(20):
        ex.fetch("price_ai", f"current price of TOKEN{i}", lambda: "x", 0.01, now=1000.0)
    assert ex.active_keys() == 20
    # well past TTL, a new fetch prunes the expired entries
    ex.fetch("price_ai", "current price of FRESH", lambda: "y", 0.01, now=1000.0 + 61)
    assert ex.active_keys() == 1


def test_distilled_node_titles_are_redacted():
    from core.memory import distill_job, get_memory
    from core.service import submit_goal
    from core.store import get_store

    # a goal whose decomposition title carries a secret-shaped token (assembled from parts so no
    # scannable secret literal lives in source — it's a format placeholder, not a real key)
    key = "sk-" + "ABCDEF0123456789ABCDEF"
    view = submit_goal("deploy the api with key " + key + " then audit it", "redact_owner")
    store = get_store()
    for cap in ("devops", "security"):
        store.create_hire(view.id, node_key=f"n_{cap}", agent_id=cap, agent_name=cap,
                          in_room_id=f"{cap}::x", capability=cap, amount_usd=1.0, trust="high",
                          confidence=0.9, escrow_id="e", payee="p", status="released")
    distill_job(view.id)
    pbs = get_memory().list_playbooks("redact_owner")
    joined = " ".join(t for pb in pbs for t in pb.node_titles)
    assert key not in joined     # the secret was redacted out of titles
