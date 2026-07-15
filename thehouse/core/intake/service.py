"""INTAKE — validate, stamp request_id, registry lookup, dedup check, enqueue (spec §4).

Order per spec §3.3: the Deduplicator runs before the window on every request —
exact cache hit → served immediately; identical pending request → merged into its slot;
novel → proceeds to the per-ASP window queue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.deduplicator.semantic import SemanticDedup
from thehouse.core.deduplicator.service import DedupService, fingerprint
from thehouse.core.models import DEDUP_SAFE_MODES, ASPEntry, CallerRequest, RequestStatus
from thehouse.core.storage.db import asp_registry, audit, request_log
from thehouse.core.window.queue import ASPQueue


class UnknownASPError(Exception):
    pass


class InactiveASPError(Exception):
    pass


class RateLimitedError(Exception):
    """Caller exceeded settings.rate_limit_per_minute. Refused before any charge."""


class QueueFullError(Exception):
    """Target queue at settings.max_queue_depth. Refused before any charge."""


class IntakeService:
    def __init__(self, engine: AsyncEngine, redis: Any, semantic: SemanticDedup | None = None):
        self.engine = engine
        self.redis = redis
        self.queue = ASPQueue(redis)
        self.dedup = DedupService(redis)
        self.semantic = semantic

    async def _check_rate(self, caller_id: str) -> None:
        from thehouse.core.config import settings
        from thehouse.core.models import now_ms

        limit = settings.rate_limit_per_minute
        if limit <= 0:
            return
        key = f"ratelimit:{caller_id}:{now_ms() // 60_000}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 120)
        if count > limit:
            raise RateLimitedError(caller_id)

    async def _check_depth(self, asp_id: str) -> None:
        from thehouse.core.config import settings

        depth = settings.max_queue_depth
        if depth > 0 and await self.queue.size(asp_id) >= depth:
            raise QueueFullError(asp_id)

    async def lookup_asp(self, asp_id: str) -> ASPEntry:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(select(asp_registry).where(asp_registry.c.asp_id == asp_id))
            ).mappings().first()
        if row is None:
            raise UnknownASPError(asp_id)
        entry = ASPEntry(**dict(row))
        if not entry.active:
            raise InactiveASPError(asp_id)
        return entry

    async def accept(
        self,
        asp_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        caller_id: str,
        priority: bool = False,
        query: str | None = None,
    ) -> CallerRequest:
        """Validate, stamp, dedup-check, and route the request (cache / merge / window)."""
        entry = await self.lookup_asp(asp_id)
        if tool_name != entry.tool_name:
            raise UnknownASPError(f"{asp_id} does not expose tool {tool_name}")
        await self._check_rate(caller_id)

        # Side-effectful (and unclassified) calls carry no fingerprint: every one must
        # reach the target — never served from cache, never merged with a lookalike.
        dedup_safe = entry.mode in DEDUP_SAFE_MODES

        req = CallerRequest(
            asp_id=asp_id,
            tool_name=tool_name,
            arguments=arguments,
            caller_id=caller_id,
            priority=priority,
            query=query or _extract_query(arguments),
            fingerprint=fingerprint(tool_name, arguments) if dedup_safe else None,
        )
        if not dedup_safe:
            await self._check_depth(asp_id)
            await self._log(req)
            await self.queue.push(req)
            await audit(
                "request_accepted",
                {"request_id": req.request_id, "asp_id": asp_id, "caller_id": caller_id,
                 "dedup": "skipped_side_effectful"},
                engine=self.engine,
            )
            return req

        # 1. Exact-match cache: identical request answered within TTL → serve, zero cost.
        cached = await self.dedup.get_cached(asp_id, req.fingerprint)
        if cached is not None:
            from thehouse.core.pricing import caller_price

            req.status = RequestStatus.CACHED
            req.result = cached
            # charged = what the 402 gate actually collected — zero cost, 100% margin
            await self._log(req, charged=caller_price(entry, priority=priority))
            await audit(
                "dedup_cache_hit",
                {"request_id": req.request_id, "asp_id": asp_id, "caller_id": caller_id},
                engine=self.engine,
            )
            return req

        # 2. In-window exact merge: identical request already pending → share its slot.
        slot_owner = await self.dedup.claim_slot(asp_id, req.fingerprint, req.request_id)
        if slot_owner is not None and slot_owner != req.request_id:
            req.status = RequestStatus.MERGED
            req.merged_into = slot_owner
            await self.dedup.record_merge(asp_id, slot_owner, req.request_id)
            await self._log(req)
            await audit(
                "dedup_window_merge",
                {
                    "request_id": req.request_id,
                    "merged_into": slot_owner,
                    "asp_id": asp_id,
                },
                engine=self.engine,
            )
            return req

        # 3. Semantic near-duplicate: same meaning, different words → share the slot
        #    (spec §3.3). Moderate overlap composes as adjacent but is flagged.
        if self.semantic is not None and req.query:
            verdict = await self.semantic.check(asp_id, req.request_id, req.query)
            if verdict.owner_request_id is not None:
                await self.dedup.release_window(asp_id, [req.fingerprint])
                req.status = RequestStatus.MERGED
                req.merged_into = verdict.owner_request_id
                await self.dedup.record_merge(asp_id, verdict.owner_request_id, req.request_id)
                await self._log(req)
                await audit(
                    "dedup_semantic_merge",
                    {
                        "request_id": req.request_id,
                        "merged_into": verdict.owner_request_id,
                        "score": round(verdict.score, 4),
                        "asp_id": asp_id,
                    },
                    engine=self.engine,
                )
                return req
            if verdict.overlap_with is not None:
                await audit(
                    "semantic_overlap_flagged",
                    {
                        "request_id": req.request_id,
                        "overlaps": verdict.overlap_with,
                        "score": round(verdict.score, 4),
                        "asp_id": asp_id,
                    },
                    engine=self.engine,
                )

        # 4. Novel request → window queue. (Depth check sits after the merge attempt on
        # purpose: a merge adds no queue slot, so it is allowed even at capacity. On
        # refusal, release the dedup slot claimed above so no one merges into a ghost.)
        try:
            await self._check_depth(asp_id)
        except QueueFullError:
            await self.dedup.release_window(asp_id, [req.fingerprint])
            raise
        await self._log(req)
        await self.queue.push(req)
        await audit(
            "request_accepted",
            {"request_id": req.request_id, "asp_id": asp_id, "caller_id": caller_id},
            engine=self.engine,
        )
        return req

    async def _log(self, req: CallerRequest, charged: float | None = None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(request_log).values(
                    charged=charged,
                    request_id=req.request_id,
                    asp_id=req.asp_id,
                    tool_name=req.tool_name,
                    arguments=req.arguments,
                    query=req.query,
                    caller_id=req.caller_id,
                    priority=req.priority,
                    received_at_ms=req.received_at_ms,
                    status=req.status.value,
                    merged_into=req.merged_into,
                    result=req.result,
                )
            )


def _extract_query(arguments: dict[str, Any]) -> str | None:
    """Best-effort primary query string for Mode A composition."""
    for key in ("query", "question", "prompt", "q", "text", "input"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None
