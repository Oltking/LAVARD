from core.foreman import find_candidates, rank_score
from onchain import get_marketplace, get_onchain_data
from onchain.identity import MockOnchainData
from onchain.marketplace import MockMarketplace


def test_default_backends_are_mocks_offline():
    assert isinstance(get_marketplace(), MockMarketplace)
    assert isinstance(get_onchain_data(), MockOnchainData)


def test_candidates_are_deterministic():
    a = get_marketplace().search_candidates("research", limit=5)
    b = get_marketplace().search_candidates("research", limit=5)
    assert [x.agent_id for x in a] == [x.agent_id for x in b]
    assert len(a) == 5


def test_candidates_carry_reputation_and_identity():
    c = get_marketplace().search_candidates("security", limit=3)[0]
    assert c.reputation.score >= 0
    assert 0.0 <= c.reputation.dispute_rate <= 1.0
    assert c.reputation.stake_okb >= 100  # evaluators/ASPs stake >=100 OKB
    # unified identity: EVM (x-layer) + Solana addresses
    chains = {w.chain for w in c.identity.wallets}
    assert "x-layer" in chains and "solana" in chains


def test_find_candidates_ranks_best_first():
    ranked = find_candidates("content", limit=5)
    scores = [rank_score(c) for c in ranked]
    assert scores == sorted(scores, reverse=True)


def test_onchain_profile_flags_opaque_origin_somewhere():
    data = get_onchain_data()
    # across many agents, the mock must produce at least one opaque/mixer-funded origin,
    # so the Vetter's honest-limits path (Phase 3) is exercisable.
    opaque_seen = any(
        any(edge.opaque for edge in data.get_profile(f"asp_x_{i}").funder_trace)
        for i in range(20)
    )
    assert opaque_seen
