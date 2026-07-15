"""BATCHING WINDOW (spec §5.2, Phase 4). One window per target ASP, managed in Redis.

Fire conditions (first wins):
1. len(requests) >= break_even_batch_size  → fire immediately (profitable, no reason to wait)
2. now - opened_at >= window_timer_ms      → fire on expiry (QoS guarantee)
3. any request has priority=True           → fire immediately

The manager owns fire *decisions*; dispatch/composition happen downstream on the returned
batch. On fire it releases the window's dedup fingerprints so identical future requests
open fresh slots.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from thehouse.core.deduplicator.service import DedupService
from thehouse.core.models import CallerRequest, FireReason, now_ms
from thehouse.core.window.queue import ASPQueue

WINDOW_OPEN_KEY = "window_open:{asp_id}"
FIRE_LOCK_KEY = "window_fire_lock:{asp_id}"


@dataclass
class Batch:
    batch_id: str
    asp_id: str
    requests: list[CallerRequest]
    fire_reason: FireReason
    window_open_ms: int  # how long the window was open before firing


class WindowManager:
    def __init__(self, redis: Any, clock: Callable[[], int] = now_ms, semantic=None):
        self.redis = redis
        self.queue = ASPQueue(redis)
        self.dedup = DedupService(redis)
        self.semantic = semantic  # SemanticDedup; pending vectors released on fire
        self.clock = clock

    async def submit(self, req: CallerRequest, break_even_batch_size: int) -> Batch | None:
        """Add an already-accepted request to its window and fire if a condition is met.

        Returns the fired Batch, or None if the window keeps collecting.
        """
        key = WINDOW_OPEN_KEY.format(asp_id=req.asp_id)
        await self.redis.set(key, self.clock(), nx=True)
        size = await self.queue.size(req.asp_id)

        if req.priority:
            return await self.fire(req.asp_id, FireReason.PRIORITY)
        if size >= break_even_batch_size:
            return await self.fire(req.asp_id, FireReason.BREAK_EVEN)
        return None

    async def check_timer(self, asp_id: str, window_timer_ms: int) -> Batch | None:
        """Fire the window if it has been open past its timer. Called by the sweeper."""
        opened_at = await self.redis.get(WINDOW_OPEN_KEY.format(asp_id=asp_id))
        if opened_at is None:
            return None
        if self.clock() - int(opened_at) >= window_timer_ms:
            return await self.fire(asp_id, FireReason.TIMER)
        return None

    async def fire(self, asp_id: str, reason: FireReason) -> Batch | None:
        """Atomically drain the window into a Batch. Returns None if it was empty."""
        lock_key = FIRE_LOCK_KEY.format(asp_id=asp_id)
        got_lock = await self.redis.set(lock_key, "1", nx=True, px=5_000)
        if not got_lock:
            return None
        try:
            opened_at = await self.redis.get(WINDOW_OPEN_KEY.format(asp_id=asp_id))
            requests = await self.queue.drain(asp_id)
            await self.redis.delete(WINDOW_OPEN_KEY.format(asp_id=asp_id))
            # RACE: a request may have been pushed AFTER the atomic drain but before (or as) we
            # deleted the window-open key. It now sits in the queue with no open window, so neither
            # a size-fire nor a timer-fire would ever pick it up — it would hang until TTL. Re-open
            # the window if anything is waiting, so its timer starts and the next sweep fires it.
            if await self.queue.size(asp_id) > 0:
                await self.redis.set(WINDOW_OPEN_KEY.format(asp_id=asp_id), self.clock())
            if not requests:
                return None
            await self.dedup.release_window(
                asp_id, [r.fingerprint for r in requests if r.fingerprint]
            )
            if self.semantic is not None:
                await self.semantic.release(asp_id, [r.request_id for r in requests])
            open_ms = self.clock() - int(opened_at) if opened_at is not None else 0
            return Batch(
                batch_id=f"batch_{uuid.uuid4().hex[:12]}",
                asp_id=asp_id,
                requests=requests,
                fire_reason=reason,
                window_open_ms=max(open_ms, 0),
            )
        finally:
            await self.redis.delete(lock_key)
