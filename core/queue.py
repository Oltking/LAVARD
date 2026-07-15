"""Task queue seam.

Spec §3 calls for an async task queue (Arq/Redis). To keep the repo runnable with zero infra,
execution is **inline by default**; if `LAVARD_REDIS_URL` is set, a real Arq worker can pick jobs
up instead. Callers use `submit_job_processing(job_id)` and never care which path ran.

Phase 1 keeps decomposition fast enough to run inline in the request. Long-running phases (Room
execution, Vetter forensics) move behind the real queue.
"""

from __future__ import annotations

from core.config import get_settings


def submit_job_processing(job_id: str) -> None:
    """Process a job's plan. Inline now; Arq-enqueued when Redis is configured."""
    settings = get_settings()
    if settings.redis_url:
        try:
            _enqueue_arq(job_id)
            return
        except Exception:
            # If the queue is misconfigured/unreachable, degrade to inline rather than drop work.
            pass
    _process_inline(job_id)


def _process_inline(job_id: str) -> None:
    from core.service import process_job  # local import avoids a cycle

    process_job(job_id)


def _enqueue_arq(job_id: str) -> None:  # pragma: no cover - exercised only with Redis present
    import asyncio

    from arq import create_pool
    from arq.connections import RedisSettings

    settings = get_settings()

    async def _go() -> None:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("process_job_task", job_id)

    asyncio.run(_go())
