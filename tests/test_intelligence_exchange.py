"""Intelligence Exchange: many callers on one public query → one upstream call, rest shared.
Personalized/mutating work is never shared (isolated by design)."""

from core.router.exchange import IntelligenceExchange, is_shareable


def test_shareable_guard():
    assert is_shareable("current BTC price")
    assert is_shareable("today's ETH news")
    assert is_shareable("what is EIP-3009")
    # personalized / mutating → never shareable
    assert not is_shareable("audit my Solidity contract")
    assert not is_shareable("deploy the landing page")
    assert not is_shareable("what is my wallet balance")


def test_many_callers_one_upstream_call():
    ex = IntelligenceExchange(ttl_s=100)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return "BTC = $65,000"

    answers = []
    for i in range(300):
        ans, shared = ex.fetch("price_ai", "current BTC price", compute, cost=0.01,
                               caller_id=f"c{i}", now=1000.0)
        answers.append((ans, shared))

    assert calls["n"] == 1                        # only ONE upstream call left
    assert all(a == "BTC = $65,000" for a, _ in answers)
    assert ex.stats.upstream_calls == 1
    assert ex.stats.shared_hits == 299            # everyone else shared
    assert ex.stats.calls_saved == 299
    assert abs(ex.stats.cost_saved - 299 * 0.01) < 1e-9


def test_ttl_expiry_refetches():
    ex = IntelligenceExchange(ttl_s=30)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return f"answer {calls['n']}"

    ex.fetch("news_ai", "latest ETH news", compute, 0.02, "a", now=0.0)
    ex.fetch("news_ai", "latest ETH news", compute, 0.02, "b", now=10.0)   # shared
    ex.fetch("news_ai", "latest ETH news", compute, 0.02, "c", now=100.0)  # stale → refetch
    assert calls["n"] == 2


def test_unshareable_always_isolated():
    ex = IntelligenceExchange()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return f"private-{calls['n']}"

    a1, s1 = ex.fetch("audit_ai", "audit my contract", compute, 5.0, "u1")
    a2, s2 = ex.fetch("audit_ai", "audit my contract", compute, 5.0, "u2")
    assert not s1 and not s2          # never shared
    assert a1 != a2                   # each isolated
    assert calls["n"] == 2
    assert ex.stats.not_shareable == 2
