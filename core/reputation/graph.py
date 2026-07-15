"""The Reputation Graph (§ vision: multi-dimensional agent scoring).

Rate agents by what actually happened, not by stars. Every completed hire/room delivery records
an execution outcome (delivered/failed/recovered, latency, cost, whether its output was later
reused). The graph aggregates that history into normalized [0,1] sub-scores across several
dimensions, which the Optimization Engine then weights per the user's preference.

Honest cold-start: with no execution history yet, the score falls back to the platform-native
marketplace signal (feedbackRate/soldCount) and the Vetter trust band — so a brand-new deployment
still ranks sensibly and gets sharper as it accumulates its own evidence. `samples` is always
surfaced so a caller (and the demo) can see how much real evidence backs a score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.store import get_store

# Latency/cost are normalized against these soft references (a call at the reference value scores
# ~0.5). They are deliberately generous so the curve is smooth, not a cliff.
_LATENCY_REF_MS = 4000.0
_COST_REF_USD = 15.0


@dataclass
class ReputationScore:
    agent_id: str
    samples: int
    dimensions: dict[str, float] = field(default_factory=dict)  # each in [0,1]
    overall: float = 0.0                                         # 0..100 blended
    basis: str = "cold-start"                                    # "history" | "cold-start" | "blend"

    def to_dict(self) -> dict:
        return {"agent_id": self.agent_id, "samples": self.samples,
                "dimensions": {k: round(v, 4) for k, v in self.dimensions.items()},
                "overall": round(self.overall, 2), "basis": self.basis}


def _norm_inverse(value: float, ref: float) -> float:
    """Map a lower-is-better metric to [0,1] with ref → 0.5 (smooth, bounded)."""
    if value <= 0:
        return 1.0
    return max(0.0, min(1.0, ref / (ref + value)))


def score_agent(agent_id: str, *, marketplace_score: float | None = None,
                trust: str | None = None, store=None) -> ReputationScore:
    """Compute the multi-dimensional reputation score for an agent.

    `marketplace_score` (0..100) and `trust` provide the cold-start prior before the agent has
    its own execution history in this deployment."""
    store = store or get_store()
    stats = store.get_agent_stats(agent_id)
    n = stats["samples"]

    # Cold-start prior from platform signals (feedbackRate 0..100 → 0..1).
    prior = (marketplace_score / 100.0) if marketplace_score is not None else 0.6
    trust_prior = {"high": 0.85, "medium": 0.6, "low": 0.2}.get(trust or "", prior)
    prior = (prior + trust_prior) / 2.0

    if n == 0:
        dims = {"quality": prior, "reliability": prior, "speed": 0.5,
                "cost_efficiency": 0.5, "reuse": 0.0, "recovery": prior}
        return ReputationScore(agent_id, 0, dims, round(prior * 100.0, 2), "cold-start")

    delivered, failed, recovered = stats["delivered"], stats["failed"], stats["recovered"]
    attempts = max(1, delivered + failed + recovered)
    completion_rate = (delivered + recovered) / attempts
    recovery = recovered / max(1, failed + recovered)
    speed = _norm_inverse(stats["avg_latency_ms"], _LATENCY_REF_MS)
    cost_eff = _norm_inverse(stats["avg_cost_usd"], _COST_REF_USD)
    reuse = min(1.0, stats["reused"] / max(1, delivered))

    # Blend measured quality with the prior, weighted by how much evidence we have (more samples
    # → trust the history; few samples → lean on the prior). Saturates around 10 samples.
    evidence_w = min(1.0, n / 10.0)
    quality = evidence_w * completion_rate + (1 - evidence_w) * prior
    reliability = evidence_w * completion_rate + (1 - evidence_w) * trust_prior

    dims = {"quality": quality, "reliability": reliability, "speed": speed,
            "cost_efficiency": cost_eff, "reuse": reuse, "recovery": recovery}
    overall = round(100.0 * (0.35 * quality + 0.2 * reliability + 0.15 * speed
                             + 0.15 * cost_eff + 0.1 * reuse + 0.05 * recovery), 2)
    basis = "history" if evidence_w >= 1.0 else "blend"
    return ReputationScore(agent_id, n, dims, overall, basis)


def record_delivery(agent_id: str, *, job_id: str = "", capability: str = "",
                    latency_ms: int = 0, cost_usd: float = 0.0, reused: bool = False,
                    store=None) -> None:
    (store or get_store()).record_execution(
        agent_id, job_id=job_id, capability=capability, status="delivered",
        latency_ms=latency_ms, cost_usd=cost_usd, reused=reused)


def record_failure(agent_id: str, *, job_id: str = "", capability: str = "",
                   recovered: bool = False, store=None) -> None:
    (store or get_store()).record_execution(
        agent_id, job_id=job_id, capability=capability,
        status="recovered" if recovered else "failed")
