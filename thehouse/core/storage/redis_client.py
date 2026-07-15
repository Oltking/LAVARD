"""Redis factory: real Redis in prod, fakeredis in dev — identical asyncio API."""

from __future__ import annotations

from typing import Any

from thehouse.core.config import settings

_client: Any = None


def get_redis() -> Any:
    global _client
    if _client is None:
        if settings.profile == "prod":
            import redis.asyncio as redis

            _client = redis.from_url(settings.redis_url, decode_responses=True)
        else:
            import fakeredis

            _client = fakeredis.FakeAsyncRedis(decode_responses=True)
    return _client


async def reset_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
