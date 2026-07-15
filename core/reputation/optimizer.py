"""The Optimization Engine (§ vision: score cost/speed/quality/reliability/reputation, pick the
best combination for the user's preference).

Before hiring, LAVARD scores every vetted candidate across normalized dimensions and solves a
single weighted objective set by the user's preference:

    cheapest  → weight cost hard
    fastest   → weight speed hard
    smartest  → weight quality/reliability/reputation hard
    balanced  → an even blend (default)

Inputs per candidate: the marketplace listing (price, platform reputation), the Vetter trust band,
and the multi-dimensional Reputation Graph score (execution history). Output: a ranked list of
`Assessment`s with a transparent per-dimension breakdown, so the choice is explainable — never a
black box. Low-trust candidates are hard-excluded (they still route to user approval upstream).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.reputation.graph import ReputationScore, score_agent

# The quoted price feeds cost_efficiency immediately (before any execution history exists), so
# "cheapest" is meaningful on day one. A listing at this price scores ~0.5.
_PRICE_REF_USD = 15.0


def _price_efficiency(price_usd: float) -> float:
    if price_usd <= 0:
        return 1.0
    return max(0.0, min(1.0, _PRICE_REF_USD / (_PRICE_REF_USD + price_usd)))

# Preference → dimension weights (they need not sum to 1; the score is a weighted average).
_PROFILES: dict[str, dict[str, float]] = {
    "cheapest":  {"cost_efficiency": 0.60, "quality": 0.15, "reliability": 0.10, "speed": 0.10, "reuse": 0.05},
    "fastest":   {"speed": 0.55, "reliability": 0.15, "quality": 0.15, "cost_efficiency": 0.10, "reuse": 0.05},
    "smartest":  {"quality": 0.45, "reliability": 0.30, "reuse": 0.10, "speed": 0.08, "cost_efficiency": 0.07},
    "balanced":  {"quality": 0.28, "reliability": 0.22, "cost_efficiency": 0.20, "speed": 0.20, "reuse": 0.10},
}
DEFAULT_PREFERENCE = "balanced"


def preferences() -> list[str]:
    return list(_PROFILES)


@dataclass
class Assessment:
    agent_id: str
    name: str
    listing: Any                        # the AgentListing
    reputation: ReputationScore
    trust: str
    preference: str
    score: float                        # weighted objective, 0..1
    breakdown: dict[str, float] = field(default_factory=dict)
    excluded: bool = False
    exclude_reason: str = ""

    def to_dict(self) -> dict:
        return {"agent_id": self.agent_id, "name": self.name,
                "preference": self.preference, "score": round(self.score, 4),
                "trust": self.trust, "reputation": self.reputation.to_dict(),
                "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
                "excluded": self.excluded, "exclude_reason": self.exclude_reason}


def _weights(preference: str) -> dict[str, float]:
    return _PROFILES.get(preference, _PROFILES[DEFAULT_PREFERENCE])


def assess(listing: Any, verdict: Any, preference: str = DEFAULT_PREFERENCE,
           store=None) -> Assessment:
    """Score one candidate under a preference. `verdict` is a Vetter verdict (trust/score)."""
    rep = score_agent(listing.agent_id, marketplace_score=float(listing.reputation.score),
                      trust=verdict.trust, store=store)
    # Fold the live quoted price into cost_efficiency: use it outright at cold-start, blend it
    # with measured cost once the agent has execution history. This is what makes "cheapest"
    # actually pick the cheaper listing from day one.
    dims = dict(rep.dimensions)
    price_eff = _price_efficiency(float(getattr(listing, "price_usd", 0.0)))
    dims["cost_efficiency"] = (price_eff if rep.samples == 0
                               else 0.5 * price_eff + 0.5 * dims.get("cost_efficiency", 0.5))
    weights = _weights(preference)
    breakdown = {dim: weights[dim] * dims.get(dim, 0.0) for dim in weights}
    score = sum(breakdown.values())
    a = Assessment(agent_id=listing.agent_id, name=listing.name, listing=listing,
                   reputation=rep, trust=verdict.trust, preference=preference,
                   score=score, breakdown=breakdown)
    if verdict.trust == "low":
        a.excluded = True
        a.exclude_reason = "low trust — requires explicit user approval"
    return a


def rank(candidates: list[tuple[Any, Any]], preference: str = DEFAULT_PREFERENCE,
         store=None) -> list[Assessment]:
    """Rank (listing, verdict) pairs best-first under the preference. Excluded ones sort last."""
    assessments = [assess(listing, verdict, preference, store) for listing, verdict in candidates]
    assessments.sort(key=lambda a: (not a.excluded, a.score), reverse=True)
    return assessments


def choose_best(candidates: list[tuple[Any, Any]], preference: str = DEFAULT_PREFERENCE,
                store=None) -> Assessment | None:
    ranked = rank(candidates, preference, store)
    if not ranked:
        return None
    top = ranked[0]
    return None if top.excluded else top
