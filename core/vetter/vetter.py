"""The Vetter — confidence-scored risk verdict on an agent's provenance (§4.1, Phase 3).

Pulls the agent's onchain profile (identity, platform reputation, contracts touched, funder
trace) and combines it into a transparent, additive trust score with an evidence chain. It
*layers on top of* the platform's native reputation/dispute signals — it does not reinvent them.

Honest limits are first-class (spec §4.1): funder tracing hits walls at fresh wallets, mixers,
and TEE-custodied keys. When origin is opaque the verdict says so, lowers CONFIDENCE (not
necessarily trust), and surfaces it — an opaque origin is itself a useful signal, never hidden.

Deterministic and offline: reads the mock OnchainDataClient by default (Q-API-1).
"""

from __future__ import annotations

from core.vetter.schemas import Evidence, VetterVerdict
from onchain import get_onchain_data
from onchain.identity import OnchainDataClient, OnchainProfile


def vet_agent(agent_id: str, client: OnchainDataClient | None = None) -> VetterVerdict:
    client = client or get_onchain_data()
    profile = client.get_profile(agent_id)
    return _score(agent_id, profile)


def _score(agent_id: str, p: OnchainProfile) -> VetterVerdict:
    rep = p.reputation
    evidence: list[Evidence] = []
    limits: list[str] = []

    # Base: platform-native reputation (we layer on top, don't reinvent).
    score = float(rep.score)
    evidence.append(Evidence("platform_reputation", f"platform score {rep.score:.0f}/100", rep.score))

    # Track record.
    if rep.jobs_completed < 10:
        evidence.append(Evidence("thin_track_record", f"{rep.jobs_completed} jobs completed", -8))
        score -= 8
    else:
        evidence.append(Evidence("track_record", f"{rep.jobs_completed} jobs completed", 0))

    # Dispute history.
    dr = rep.dispute_rate
    if dr > 0:
        pen = -min(dr * 100.0, 40.0)
        evidence.append(Evidence("dispute_history", f"dispute rate {dr*100:.1f}%", pen))
        score += pen

    # Account age / freshness.
    if rep.first_seen_days < 30:
        evidence.append(Evidence("fresh_account", f"first seen {rep.first_seen_days}d ago", -10))
        score -= 10
        limits.append("Account is <30 days old — limited history to reason over.")
    elif rep.first_seen_days < 90:
        evidence.append(Evidence("young_account", f"first seen {rep.first_seen_days}d ago", -5))
        score -= 5

    # Contract risk.
    if p.risky_contracts:
        pen = -25.0 * len(p.risky_contracts)
        evidence.append(
            Evidence("risky_contract", f"interacted with {p.risky_contracts}", pen)
        )
        score += pen

    # Funder provenance — the honest-limits core.
    opaque = any(edge.opaque for edge in p.funder_trace)
    confidence = 0.9
    if opaque:
        evidence.append(
            Evidence("opaque_funder", "funder origin is a mixer / fresh wallet — untraceable", -5)
        )
        score -= 5
        confidence -= 0.4
        limits.append(
            "Funder trace terminates at an opaque origin (mixer/privacy tool/fresh wallet); "
            "provenance is unverifiable beyond one hop. Treat with caution."
        )
    else:
        evidence.append(Evidence("clear_funder", "funder trace resolves to a non-opaque origin", 5))
        score += 5
    if rep.jobs_completed < 10:
        confidence -= 0.15
    if rep.first_seen_days < 30:
        confidence -= 0.15

    score = max(0.0, min(100.0, score))
    confidence = max(0.1, min(0.99, confidence))

    # Trust band with hard overrides.
    if p.risky_contracts or score < 45 or dr > 0.20:
        trust = "low"
    elif score >= 70 and dr < 0.05 and not opaque:
        trust = "high"
    else:
        trust = "medium"

    limits.append(
        "Verdict is a confidence-scored signal, not a guarantee. TEE-custodied keys and cheap "
        "fresh sub-wallets (EVM+Solana) cap how far any origin can be traced."
    )

    recommendation = {
        "high": "Safe to hire per the Vetter; still apply the necessity test before spending.",
        "medium": "Hire allowed with monitoring and a capped first milestone; watch disputes.",
        "low": "Do not hire without explicit user approval; surface the risk signals above.",
    }[trust]

    return VetterVerdict(
        agent_id=agent_id,
        trust=trust,
        confidence=confidence,
        score=score,
        evidence=evidence,
        limits=limits,
        recommendation=recommendation,
    )
