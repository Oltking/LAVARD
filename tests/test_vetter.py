from core.vetter import vet_agent
from onchain.identity import FunderEdge, MockOnchainData, OnchainProfile
from onchain.schemas import AgentIdentity, ReputationSignals, WalletRef


def _profile(*, score, jobs, disputes, age_days, risky, opaque):
    ident = AgentIdentity("a", "A", wallets=[WalletRef("x-layer", "0xabc")])
    rep = ReputationSignals(
        score=score, jobs_completed=jobs, disputes=disputes, stake_okb=200, first_seen_days=age_days
    )
    return OnchainProfile(
        identity=ident,
        reputation=rep,
        contracts_touched=["0x1"],
        risky_contracts=(["0xde1e" * 10] if risky else []),
        funder_trace=[FunderEdge("0xsrc", "0xabc", 100.0, opaque=opaque)],
        tx_count=jobs,
    )


class _Fixed(MockOnchainData):
    def __init__(self, profile):
        self._p = profile

    def get_profile(self, agent_id):
        return self._p


def test_verdict_shape_and_never_binary():
    v = vet_agent("asp_sec_00001")
    assert v.trust in {"high", "medium", "low"}
    assert 0.0 <= v.confidence <= 1.0
    assert v.evidence, "must return an evidence chain"
    assert v.limits, "must always surface honest limits"
    assert v.recommendation


def test_risky_contract_forces_low_trust():
    p = _profile(score=95, jobs=300, disputes=0, age_days=400, risky=True, opaque=False)
    v = vet_agent("x", client=_Fixed(p))
    assert v.trust == "low"
    assert any(e.signal == "risky_contract" for e in v.evidence)


def test_opaque_origin_lowers_confidence_and_is_surfaced():
    clear = vet_agent("x", client=_Fixed(
        _profile(score=90, jobs=300, disputes=0, age_days=400, risky=False, opaque=False)))
    opaque = vet_agent("x", client=_Fixed(
        _profile(score=90, jobs=300, disputes=0, age_days=400, risky=False, opaque=True)))
    assert opaque.confidence < clear.confidence
    assert any("opaque" in lim.lower() for lim in opaque.limits)
    assert opaque.trust != "high"  # opacity blocks a high rating


def test_strong_clean_agent_gets_high_trust():
    p = _profile(score=92, jobs=400, disputes=0, age_days=500, risky=False, opaque=False)
    v = vet_agent("x", client=_Fixed(p))
    assert v.trust == "high"
    assert v.confidence > 0.8


def test_high_dispute_rate_forces_low():
    p = _profile(score=80, jobs=50, disputes=40, age_days=400, risky=False, opaque=False)
    v = vet_agent("x", client=_Fixed(p))
    assert v.trust == "low"
