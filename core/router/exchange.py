"""The Intelligence Exchange — an "AI CDN" for idempotent public lookups (§ vision).

When many callers independently ask the same *public, read-only* question in a short window —
"current BTC price", "today's ETH news", "what is EIP-3009" — only ONE upstream paid call needs
to leave; every other caller is served the shared answer. Like a CDN edge cache, but for
intelligence.

The hard safety rule (this is what makes it sellable rather than dangerous): sharing is allowed
ONLY for queries that are idempotent and non-personalized. Anything that mutates state, is
personalized, or references private material is NEVER shared — each such caller gets their own
isolated call. This mirrors the batching-isolation guarantee already in TheHouse: an optimization
must never leak one caller's context into another's answer.

Metrics are first-class so the network effect is demonstrable: more callers on the same public
query ⇒ more upstream calls avoided ⇒ lower cost for everyone.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Queries safe to share: public, read-only, deterministic-ish lookups.
_SHAREABLE = re.compile(
    r"\b(price|ticker|quote|news|headline|what is|who is|define|definition|explain|"
    r"current|latest|rate|weather|block ?height|gas ?price|market ?cap|supply|docs?)\b", re.I)

# Never share: mutation, personalization, or anything referencing private/owned material.
_UNSHAREABLE = re.compile(
    r"\b(my|mine|our|deploy|sign|send|transfer|swap|buy|sell|mint|audit|review|write|generate|"
    r"build|create|delete|update|private|secret|wallet|key|seed|account|personal|for me)\b", re.I)


def is_shareable(query: str) -> bool:
    """True only for public, read-only, non-personalized lookups."""
    q = query or ""
    if _UNSHAREABLE.search(q):
        return False
    return bool(_SHAREABLE.search(q))


def _normalize(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


@dataclass
class _Shared:
    answer: str
    cost: float
    ts: float
    upstream_caller: str
    served: int = 0        # how many extra callers reused this


@dataclass
class ExchangeStats:
    upstream_calls: int = 0     # calls that actually left to the ASP
    shared_hits: int = 0        # callers served from a shared answer
    calls_saved: int = 0        # == shared_hits (one avoided upstream call each)
    cost_saved: float = 0.0
    not_shareable: int = 0      # calls that bypassed the exchange (isolated, by design)

    def to_dict(self) -> dict:
        served = self.upstream_calls + self.shared_hits
        return {"upstream_calls": self.upstream_calls, "shared_hits": self.shared_hits,
                "calls_saved": self.calls_saved, "cost_saved": round(self.cost_saved, 6),
                "not_shareable": self.not_shareable,
                "dedup_ratio": round(self.shared_hits / served, 4) if served else 0.0}


class IntelligenceExchange:
    """Cross-caller shared-answer cache for idempotent public queries, with a TTL window."""

    def __init__(self, ttl_s: float = 30.0) -> None:
        self.ttl_s = ttl_s
        self._shared: dict[str, _Shared] = {}
        self.stats = ExchangeStats()
        self._last_prune = 0.0

    def _key(self, namespace: str, query: str) -> str:
        return f"{namespace}::{_normalize(query)}"

    def fetch(self, namespace: str, query: str, compute, cost: float, caller_id: str = "",
              now: float | None = None) -> tuple[str, bool]:
        """Return (answer, shared). `compute()` is the real upstream call, invoked at most once
        per (namespace, query) TTL window. Non-shareable queries bypass the exchange entirely so
        they are always computed in isolation — never shared, never leaked."""
        now = now if now is not None else time.time()
        self._prune(now)
        if not is_shareable(query):
            self.stats.not_shareable += 1
            return compute(), False

        key = self._key(namespace, query)
        entry = self._shared.get(key)
        if entry is not None and (now - entry.ts) <= self.ttl_s:
            entry.served += 1
            self.stats.shared_hits += 1
            self.stats.calls_saved += 1
            self.stats.cost_saved += entry.cost
            return entry.answer, True

        answer = compute()
        self._shared[key] = _Shared(answer=answer, cost=cost, ts=now, upstream_caller=caller_id)
        self.stats.upstream_calls += 1
        return answer, False

    def _prune(self, now: float) -> None:
        """Evict expired shared answers so the map can't grow unbounded over many distinct public
        queries. Sweeps at most once per TTL window."""
        if now - self._last_prune < self.ttl_s:
            return
        self._last_prune = now
        expired = [k for k, e in self._shared.items() if (now - e.ts) > self.ttl_s]
        for k in expired:
            del self._shared[k]

    def active_keys(self) -> int:
        return len(self._shared)
