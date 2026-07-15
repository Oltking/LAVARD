"""Optimization Engine: preference-weighted candidate selection with explainable breakdown."""

from dataclasses import dataclass

from core.reputation import assess, choose_best, preferences, rank, record_delivery
from core.store import get_store


@dataclass
class FakeRep:
    score: float


@dataclass
class FakeListing:
    agent_id: str
    name: str
    price_usd: float
    reputation: FakeRep
    mode: str = "a2a"


@dataclass
class FakeVerdict:
    trust: str


def _candidate(agent_id, name, market_score, trust="high"):
    return (FakeListing(agent_id, name, 5.0, FakeRep(market_score)), FakeVerdict(trust))


def test_preferences_exist():
    assert set(preferences()) == {"cheapest", "fastest", "smartest", "balanced"}


def test_cheapest_prefers_cheaper_history_agent():
    store = get_store()
    # cheap+fast agent vs expensive+slow agent, both delivered many times
    for _ in range(12):
        record_delivery("opt_cheap", latency_ms=500, cost_usd=1.0, store=store)
        record_delivery("opt_pricey", latency_ms=6000, cost_usd=30.0, store=store)
    cands = [_candidate("opt_cheap", "Cheap", 70.0), _candidate("opt_pricey", "Pricey", 95.0)]

    best_cheap = choose_best(cands, preference="cheapest", store=store)
    assert best_cheap.agent_id == "opt_cheap"

    # smartest leans on quality/reputation → the higher-reputation agent can win
    ranked_smart = rank(cands, preference="smartest", store=store)
    assert ranked_smart[0].breakdown  # explainable per-dimension contribution present


def test_low_trust_is_excluded_from_best():
    cands = [_candidate("low1", "LowTrust", 90.0, trust="low")]
    assert choose_best(cands, preference="balanced") is None
    a = assess(*cands[0], preference="balanced")
    assert a.excluded and "low trust" in a.exclude_reason
