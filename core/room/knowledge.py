"""Knowledge the controller answers from (first-responder step 1).

`PortableMemory` is the simple in-memory dict (still used for demo seeds and tests).
`MemoryBackedKnowledge` implements the SAME `lookup` contract but reads the real persistent,
owner-scoped, freshness-weighted Portable Memory store (core/memory) — so the room's
first-responder actually answers from cross-job distilled memory, not a demo stub
(audit finding HIGH-2). An optional in-memory `seed` overlays it (used to keep demos hermetic).
"""

from __future__ import annotations


class PortableMemory:
    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self._facts: dict[str, str] = dict(seed or {})

    def lookup(self, topic: str) -> str | None:
        if topic in self._facts:
            return self._facts[topic]
        t = topic.lower()
        for k, v in self._facts.items():
            if t and t in k.lower():
                return v
        return None

    def remember(self, topic: str, text: str) -> None:
        self._facts[topic] = text


class MemoryBackedKnowledge:
    """First-responder knowledge backed by the persistent owner-scoped memory store.

    Checks the real store first (semantic search over distilled facts), then an optional in-memory
    seed overlay. Same `lookup(topic) -> str | None` shape as `PortableMemory`, so the controller
    is unchanged.
    """

    def __init__(self, owner_id: str, seed: dict[str, str] | None = None,
                 min_sim: float = 0.55) -> None:
        self.owner_id = owner_id
        self.min_sim = min_sim
        self._seed = PortableMemory(seed)

    def lookup(self, topic: str) -> str | None:
        if not topic:
            return self._seed.lookup(topic)
        from core.memory.reuse import FRESH_MAX_AGE_S, MIN_CONF
        from core.memory.store import get_memory

        hits = get_memory().search_facts(
            self.owner_id, topic, min_conf=MIN_CONF, max_age_s=FRESH_MAX_AGE_S,
            top_k=1, threshold=self.min_sim,
        )
        if hits:
            fact, sim = hits[0]
            return f"(from memory, sim {sim:.2f}) {fact.text}"
        return self._seed.lookup(topic)

    def remember(self, topic: str, text: str) -> None:  # pragma: no cover - overlay convenience
        self._seed.remember(topic, text)
