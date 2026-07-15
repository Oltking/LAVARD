import time

from core.router import Router, classify_step
from core.router.cache import SemanticCache
from core.router.demo import run_router_demo


def test_classifier_tiers():
    assert classify_step("Look up the ticker for OKB") == "trivial"
    assert classify_step("Summarize this note") == "routine"
    assert classify_step("Plan and decompose the goal") == "complex"
    assert classify_step("Release the escrow and pay the agent") == "critical"


def test_classifier_meets_eval_floor():
    from evals.run import main

    assert main() == 0


def test_semantic_cache_serves_near_duplicate():
    r = Router()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return "answer"

    r.ask("Analyze and compare the throughput of three rollups", compute, agent_id="a")
    r.ask("Compare the throughput of three rollups", compute, agent_id="a")  # near-dup
    assert calls["n"] == 1, "near-duplicate must be served from cache"
    assert r.log.count("cache_hit") == 1
    assert r.log.total_saved > 0


def test_cross_agent_dedup_collapses_paid_call():
    log = run_router_demo()
    assert log.count("cache_hit") >= 1
    assert log.count("dedup_collapse") >= 1
    assert log.total_saved > 0


def test_cache_freshness_expires_stale_entry():
    cache = SemanticCache(max_age_s=10)
    t0 = 1000.0
    cache.put("price of okb", "old", cost=0.001, now=t0)
    assert cache.get("price of okb", now=t0 + 5) is not None       # fresh
    assert cache.get("price of okb", now=t0 + 999) is None          # stale
