"""Router decision log — proves the economics (§4.4): every decision records est_cost,
alternative_cost, and saved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RouterDecision:
    query: str
    kind: str            # "cache_hit" | "cache_miss" | "dedup_collapse" | "route"
    tier: str            # trivial | routine | complex | critical
    model: str
    est_cost: float      # what we actually spent on this decision
    alternative_cost: float   # what it would have cost without the router optimization
    saved: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouterLog:
    decisions: list[RouterDecision] = field(default_factory=list)

    def add(self, d: RouterDecision) -> None:
        self.decisions.append(d)

    @property
    def total_saved(self) -> float:
        return round(sum(d.saved for d in self.decisions), 4)

    @property
    def total_spent(self) -> float:
        return round(sum(d.est_cost for d in self.decisions), 4)

    def count(self, kind: str) -> int:
        return sum(1 for d in self.decisions if d.kind == kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [d.to_dict() for d in self.decisions],
            "total_spent": self.total_spent,
            "total_saved": self.total_saved,
            "cache_hits": self.count("cache_hit"),
            "dedup_collapses": self.count("dedup_collapse"),
        }
