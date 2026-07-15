"""Semantic near-duplicate detection (spec §3.3 / §5.3, Phase 8).

Pluggable pieces:
- Embedder — dev/test default is a deterministic token-hash embedder (zero dependencies,
  zero cost); prod can swap in fastembed/sentence-transformers behind the same interface.
- VectorStore — in-memory store for dev; QdrantVectorStore (server or local mode) for prod.

Decision rules on the best match among the same window's pending queries:
- score > merge_threshold (default 0.98)  → duplicate: merge into the owning slot
- overlap_threshold..merge_threshold      → compose as adjacent but flag (audit) — answers may
  cross-reference; if that makes the compound split badly, the pipeline isolates the affected
  callers with individual re-dispatches (BatchPipeline._isolate_failed), so an overlap never
  degrades another caller's answer or leaks the compound blob
- below overlap_threshold                 → novel

NOTE on the default embedder: `TokenHashEmbedder` is bag-of-tokens+bigrams, so at the 0.98 merge
threshold it effectively catches stopword/word-order variants — NOT genuine paraphrases with
disjoint vocabulary. True paraphrase merging needs a real embedding model swapped in behind the
`Embedder` interface. Semantic dedup is off by default (exact-string merge only, QUESTIONS.md Q8).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

from thehouse.core.config import settings

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the is are was be been do does did what who whom whose which when where how why "
    "of in on at to for with by from as it its this that these those and or not now right "
    "me my i you your please tell give show current currently today".split()
)


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    # Async so a networked backend (Qdrant) never blocks the intake event loop (audit fix #3).
    async def upsert(self, asp_id: str, request_id: str, vector: list[float]) -> None: ...
    async def search(self, asp_id: str, vector: list[float]) -> "tuple[str, float] | None": ...
    async def remove(self, asp_id: str, request_ids: list[str]) -> None: ...


class TokenHashEmbedder:
    """Deterministic bag-of-tokens embedding: unigrams + bigrams hashed into `dim` buckets,
    L2-normalized. Not a language model — but paraphrases sharing content words score high,
    unrelated queries score low, and it costs nothing. Swap for a real embedder in prod."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        tokens = [t for t in _WORD.findall(text.lower()) if t not in _STOP]
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vec = [0.0] * self.dim
        for gram in grams:
            bucket = int.from_bytes(hashlib.md5(gram.encode()).digest()[:4], "big") % self.dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # inputs are L2-normalized


class MemoryVectorStore:
    """Per-ASP pending-query vectors for the current window (dev profile). In-process, so the
    async methods do no real I/O — they satisfy the async VectorStore contract."""

    def __init__(self):
        self._data: dict[str, dict[str, list[float]]] = {}

    async def upsert(self, asp_id: str, request_id: str, vector: list[float]) -> None:
        self._data.setdefault(asp_id, {})[request_id] = vector

    async def search(self, asp_id: str, vector: list[float]) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for request_id, other in self._data.get(asp_id, {}).items():
            score = cosine(vector, other)
            if best is None or score > best[1]:
                best = (request_id, score)
        return best

    async def remove(self, asp_id: str, request_ids: list[str]) -> None:
        pending = self._data.get(asp_id, {})
        for rid in request_ids:
            pending.pop(rid, None)


class QdrantVectorStore:
    """Same contract against Qdrant (prod profile; requires the `semantic` extra)."""

    def __init__(self, url: str | None = None, collection: str = "thehouse_pending", dim: int = 512):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.client = QdrantClient(url=url) if url else QdrantClient(":memory:")
        self.collection = collection
        if not self.client.collection_exists(collection):
            self.client.create_collection(
                collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
            )

    async def upsert(self, asp_id: str, request_id: str, vector: list[float]) -> None:
        import asyncio

        from qdrant_client.models import PointStruct

        point_id = int.from_bytes(hashlib.md5(request_id.encode()).digest()[:8], "big")
        # qdrant-client is synchronous/blocking — run it off the event loop (audit fix #3).
        await asyncio.to_thread(
            self.client.upsert,
            self.collection,
            [PointStruct(id=point_id, vector=vector, payload={"asp_id": asp_id, "request_id": request_id})],
        )

    async def search(self, asp_id: str, vector: list[float]) -> tuple[str, float] | None:
        import asyncio

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        result = await asyncio.to_thread(
            self.client.query_points,
            self.collection,
            query=vector,
            query_filter=Filter(must=[FieldCondition(key="asp_id", match=MatchValue(value=asp_id))]),
            limit=1,
        )
        hits = result.points
        if not hits:
            return None
        return hits[0].payload["request_id"], hits[0].score

    async def remove(self, asp_id: str, request_ids: list[str]) -> None:
        import asyncio

        from qdrant_client.models import FieldCondition, Filter, MatchAny

        await asyncio.to_thread(
            self.client.delete,
            self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="request_id", match=MatchAny(any=request_ids))]
            ),
        )


@dataclass
class SemanticVerdict:
    owner_request_id: str | None = None   # merge target if score > merge_threshold
    overlap_with: str | None = None       # flagged adjacent slot (moderate overlap)
    score: float = 0.0


class SemanticDedup:
    def __init__(
        self,
        embedder: Embedder | None = None,
        store: MemoryVectorStore | QdrantVectorStore | None = None,
        merge_threshold: float | None = None,
        overlap_threshold: float | None = None,
    ):
        self.embedder = embedder or TokenHashEmbedder()
        self.store = store or MemoryVectorStore()
        self.merge_threshold = merge_threshold or settings.merge_threshold
        self.overlap_threshold = overlap_threshold or settings.overlap_threshold

    async def check(self, asp_id: str, request_id: str, query: str) -> SemanticVerdict:
        """Compare against the window's pending queries; register this one if novel."""
        vector = self.embedder.embed(query)
        best = await self.store.search(asp_id, vector)
        if best is not None:
            owner, score = best
            if score > self.merge_threshold:
                return SemanticVerdict(owner_request_id=owner, score=score)
            if score > self.overlap_threshold:
                await self.store.upsert(asp_id, request_id, vector)
                return SemanticVerdict(overlap_with=owner, score=score)
        await self.store.upsert(asp_id, request_id, vector)
        return SemanticVerdict()

    async def release(self, asp_id: str, request_ids: list[str]) -> None:
        await self.store.remove(asp_id, request_ids)
