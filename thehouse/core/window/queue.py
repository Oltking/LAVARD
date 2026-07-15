"""Per-ASP request queue (Redis-backed). Phase 1: plain FIFO; Phase 4 layers window
fire-logic on top of the same keys."""

from __future__ import annotations

from typing import Any

from thehouse.core.models import CallerRequest

QUEUE_KEY = "queue:{asp_id}"


class ASPQueue:
    def __init__(self, redis: Any):
        self.redis = redis

    def _key(self, asp_id: str) -> str:
        return QUEUE_KEY.format(asp_id=asp_id)

    async def push(self, req: CallerRequest) -> int:
        """Append a request to its target ASP's queue. Returns queue length."""
        return await self.redis.rpush(self._key(req.asp_id), req.model_dump_json())

    async def peek_all(self, asp_id: str) -> list[CallerRequest]:
        raw = await self.redis.lrange(self._key(asp_id), 0, -1)
        return [CallerRequest.model_validate_json(item) for item in raw]

    async def drain(self, asp_id: str, count: int | None = None) -> list[CallerRequest]:
        """Atomically remove and return up to `count` requests (all if None)."""
        key = self._key(asp_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            if count is None:
                pipe.lrange(key, 0, -1)
                pipe.delete(key)
            else:
                pipe.lrange(key, 0, count - 1)
                pipe.ltrim(key, count, -1)
            raw, _ = await pipe.execute()
        return [CallerRequest.model_validate_json(item) for item in raw]

    async def remove(self, req: CallerRequest) -> int:
        """Remove one matching entry from its queue (exact serialized form — the same
        JSON push wrote). Returns the number of entries removed (0 or 1)."""
        return await self.redis.lrem(self._key(req.asp_id), 1, req.model_dump_json())

    async def size(self, asp_id: str) -> int:
        return await self.redis.llen(self._key(asp_id))
