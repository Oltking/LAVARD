"""Semantic cache (§4.4): embed every sub-query; serve near-duplicate answers from cache instead
of paying again, with a freshness check. Backed by an in-memory cosine index by default; a
Qdrant-backed index (docs/vendor/memory/qdrant.md) drops in behind the same `VectorIndex` seam.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from core.router.embedder import Embedder, cosine, get_embedder


@dataclass
class CacheEntry:
    vector: list[float]
    query: str
    answer: str
    cost: float
    ts: float
    asker: str = ""      # which agent first paid for this answer (for cross-agent dedup)


class VectorIndex(Protocol):
    def add(self, entry: CacheEntry) -> None: ...
    def nearest(self, vector: list[float]) -> tuple[CacheEntry, float] | None: ...


@dataclass
class InMemoryVectorIndex:
    entries: list[CacheEntry] = field(default_factory=list)
    max_entries: int = 5000      # bound memory; evict oldest first (FIFO) when full

    def add(self, entry: CacheEntry) -> None:
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            del self.entries[0:len(self.entries) - self.max_entries]

    def nearest(self, vector: list[float]) -> tuple[CacheEntry, float] | None:
        best: tuple[CacheEntry, float] | None = None
        for e in self.entries:
            sim = cosine(vector, e.vector)
            if best is None or sim > best[1]:
                best = (e, sim)
        return best


class SemanticCache:
    def __init__(self, embedder: Embedder | None = None, index: VectorIndex | None = None,
                 threshold: float = 0.85, max_age_s: float = 3600.0) -> None:
        self.embedder = embedder or get_embedder()
        self.index = index or InMemoryVectorIndex()
        self.threshold = threshold
        self.max_age_s = max_age_s

    def get(self, query: str, now: float | None = None) -> CacheEntry | None:
        now = now if now is not None else time.time()
        vec = self.embedder.embed(query)
        near = self.index.nearest(vec)
        if not near:
            return None
        entry, sim = near
        if sim < self.threshold:
            return None
        if (now - entry.ts) > self.max_age_s:      # stale — freshness check (§4.5 weighting)
            return None
        return entry

    def put(self, query: str, answer: str, cost: float, asker: str = "",
            now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.index.add(CacheEntry(self.embedder.embed(query), query, answer, cost, now, asker))
