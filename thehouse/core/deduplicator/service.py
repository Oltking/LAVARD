"""Exact-match Deduplicator (spec §3.3, Phase 3).

Two effects, both keyed on a canonical fingerprint of (tool_name, arguments):

1. Cache hit — an identical request was answered within the ASP's TTL → serve the cached
   response immediately. Cost to TheHouse: zero. Margin: 100%.
2. In-window merge — an identical request is already pending in the current window → the
   new request merges into that slot; both callers get the answer from one call.

Semantic near-duplicates (embeddings) are Phase 8 and layer on top of this.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CACHE_KEY = "cache:{asp_id}:{fp}"
PENDING_KEY = "pending_fp:{asp_id}"          # hash: fp -> request_id currently in window
MERGED_KEY = "merged:{asp_id}:{request_id}"  # set: request_ids merged into this slot


def fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "args": _normalize(arguments)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalize(value: Any) -> Any:
    # Owner decision (QUESTIONS.md Q8): merge ONLY on exact equality — string for string,
    # array for array. Strings are trimmed of outer whitespace, nothing else (no case
    # folding, no inner-whitespace collapsing). Anything not byte-identical stays its own slot.
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    return value


class DedupService:
    def __init__(self, redis: Any):
        self.redis = redis

    # -- response cache ------------------------------------------------------
    async def get_cached(self, asp_id: str, fp: str) -> str | None:
        return await self.redis.get(CACHE_KEY.format(asp_id=asp_id, fp=fp))

    async def cache_result(self, asp_id: str, fp: str, result: str, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            await self.redis.set(
                CACHE_KEY.format(asp_id=asp_id, fp=fp), result, ex=ttl_seconds
            )

    # -- in-window exact merge -----------------------------------------------
    async def claim_slot(self, asp_id: str, fp: str, request_id: str) -> str | None:
        """Register this request as the owner of its fingerprint in the current window.

        Returns None if the slot was claimed (this request proceeds to the window), or the
        owning request_id if an identical request is already pending (merge into it).
        """
        key = PENDING_KEY.format(asp_id=asp_id)
        claimed = await self.redis.hsetnx(key, fp, request_id)
        if claimed:
            return None
        return await self.redis.hget(key, fp)

    async def record_merge(self, asp_id: str, slot_request_id: str, merged_request_id: str) -> None:
        await self.redis.sadd(
            MERGED_KEY.format(asp_id=asp_id, request_id=slot_request_id), merged_request_id
        )

    async def merged_members(self, asp_id: str, slot_request_id: str) -> list[str]:
        members = await self.redis.smembers(
            MERGED_KEY.format(asp_id=asp_id, request_id=slot_request_id)
        )
        return sorted(members)

    async def release_window(self, asp_id: str, fingerprints: list[str]) -> None:
        """Clear pending fingerprints when their window fires (merge sets survive until
        delivery fans the answer out)."""
        if fingerprints:
            await self.redis.hdel(PENDING_KEY.format(asp_id=asp_id), *fingerprints)

    async def clear_merge_set(self, asp_id: str, slot_request_id: str) -> None:
        await self.redis.delete(MERGED_KEY.format(asp_id=asp_id, request_id=slot_request_id))
