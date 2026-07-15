"""Reputation Graph: cold-start prior → history-backed multi-dimensional scoring."""

from core.reputation import record_delivery, record_failure, score_agent
from core.store import get_store


def test_cold_start_uses_marketplace_and_trust_prior():
    s = score_agent("brand_new_agent", marketplace_score=90.0, trust="high")
    assert s.samples == 0
    assert s.basis == "cold-start"
    assert s.overall > 60  # a high-reputation, high-trust agent still ranks well with no history
    assert set(s.dimensions) >= {"quality", "reliability", "speed", "cost_efficiency", "reuse"}


def test_history_sharpens_and_rewards_reliable_cheap_fast_agents():
    store = get_store()
    agent = "reliable_agent_1"
    for _ in range(12):
        record_delivery(agent, capability="research", latency_ms=800, cost_usd=2.0, store=store)
    s = score_agent(agent, marketplace_score=50.0, trust="medium", store=store)
    assert s.samples == 12
    assert s.basis == "history"
    # fast + cheap + always delivered → strong dimensions
    assert s.dimensions["quality"] > 0.9
    assert s.dimensions["speed"] > 0.7
    assert s.dimensions["cost_efficiency"] > 0.8


def test_failures_and_recovery_are_reflected():
    store = get_store()
    agent = "flaky_agent_1"
    for _ in range(6):
        record_delivery(agent, latency_ms=5000, cost_usd=20.0, store=store)
    for _ in range(4):
        record_failure(agent, recovered=False, store=store)
    record_failure(agent, recovered=True, store=store)
    s = score_agent(agent, store=store)
    assert s.dimensions["quality"] < 0.85     # failures drag quality down
    assert 0.0 < s.dimensions["recovery"] <= 1.0
