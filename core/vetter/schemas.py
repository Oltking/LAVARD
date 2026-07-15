"""Vetter output shapes. A verdict is NEVER a binary guarantee (§4.1): it is a trust band plus a
confidence value plus the evidence chain that produced it plus explicit honest limits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    """One factor in the verdict, with its effect on the score (transparent additive model)."""

    signal: str          # e.g. "platform_reputation", "risky_contract", "opaque_funder"
    detail: str
    effect: float        # contribution to the trust score (+/-)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VetterVerdict:
    agent_id: str
    trust: str                       # "high" | "medium" | "low"
    confidence: float                # 0..1 — how much we trust the trust score itself
    score: float                     # 0..100 composite trust score
    evidence: list[Evidence] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)   # honest limits of this analysis
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "trust": self.trust,
            "confidence": round(self.confidence, 3),
            "score": round(self.score, 1),
            "evidence": [e.to_dict() for e in self.evidence],
            "limits": self.limits,
            "recommendation": self.recommendation,
        }
