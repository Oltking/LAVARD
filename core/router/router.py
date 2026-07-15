"""The Router (§4.4): cheapest-accurate model routing + semantic cache + cross-agent dedup, with
a decision log that proves the savings. "Never hire/compute what memory answers."

- classify each internal step (trivial|routine|complex|critical) and route to the cheapest model
  that clears that step's accuracy floor. `critical` steps (Vetter verdicts, anything spending
  money) favour the strongest model, cost secondary.
- before paying, check the semantic cache; a near-duplicate fresh answer is served for free.
- cross-agent dedup: if a *different* agent already paid for a near-duplicate query, collapse to
  that one paid effort and broadcast the result instead of paying twice.
"""

from __future__ import annotations

import re
from typing import Callable

from core.config import get_settings
from core.router.cache import SemanticCache
from core.router.log import RouterDecision, RouterLog

# cost per call by tier (illustrative; real numbers come from provider pricing).
TIER_COST = {"trivial": 0.001, "routine": 0.005, "complex": 0.02, "critical": 0.05}

_CRITICAL = re.compile(r"\b(spend|pay|escrow|hire|vet|verdict|release|budget|sign[- ]?off|settle)\b", re.I)
_COMPLEX = re.compile(r"\b(plan|decompose|design|architect|analy[sz]e|compare|strategy|evaluate|reason)\b", re.I)
_TRIVIAL = re.compile(r"\b(define|lookup|price|ticker|symbol|list|unit|convert|what is|who is)\b", re.I)


def classify_step(text: str) -> str:
    if _CRITICAL.search(text):
        return "critical"
    if _COMPLEX.search(text):
        return "complex"
    if _TRIVIAL.search(text):
        return "trivial"
    return "routine"


class Router:
    def __init__(self, cache: SemanticCache | None = None, log: RouterLog | None = None) -> None:
        self.cache = cache or SemanticCache()
        self.log = log or RouterLog()
        self.settings = get_settings()

    def model_for(self, tier: str) -> str:
        return {
            "trivial": self.settings.model_trivial,
            "routine": self.settings.model_routine,
            "complex": self.settings.model_complex,
            "critical": self.settings.model_critical,
        }[tier]

    def ask(self, query: str, compute: Callable[[], str], tier: str | None = None,
            agent_id: str = "", now: float | None = None) -> str:
        return self.ask_costed(query, compute, tier=tier, agent_id=agent_id, now=now)[0]

    def ask_costed(self, query: str, compute: Callable[[], str], tier: str | None = None,
                   agent_id: str = "", now: float | None = None) -> tuple[str, float]:
        """Like `ask` but also returns what this decision actually cost, so callers (the Room's
        meter) charge the true routed/cached price instead of a flat rate (audit finding HIGH-1)."""
        tier = tier or classify_step(query)
        cost = TIER_COST[tier]
        model = self.model_for(tier)

        # Critical steps (spending, vetting, sign-off) must never be served a stale/other-context
        # cached answer — freshness and the strongest model win over cost (audit finding MED-2).
        if tier == "critical":
            answer = compute()
            self.log.add(RouterDecision(query, "route", tier, model, est_cost=cost,
                                        alternative_cost=cost, saved=0.0,
                                        note="critical step — cache/dedup bypassed for freshness"))
            return answer, cost

        entry = self.cache.get(query, now=now)
        if entry is not None:
            dedup = bool(agent_id and entry.asker and entry.asker != agent_id)
            kind = "dedup_collapse" if dedup else "cache_hit"
            note = (f"collapsed onto {entry.asker}'s paid call" if dedup
                    else f"reused cached answer for '{entry.query}'")
            self.log.add(RouterDecision(query, kind, tier, model, est_cost=0.0,
                                        alternative_cost=cost, saved=cost, note=note))
            return entry.answer, 0.0

        answer = compute()
        self.cache.put(query, answer, cost, asker=agent_id, now=now)
        self.log.add(RouterDecision(query, "route", tier, model, est_cost=cost,
                                    alternative_cost=cost, saved=0.0,
                                    note="cheapest tier clearing the accuracy floor"))
        return answer, cost
