"""AggregatorService — the facade wiring intake → dedup → window → pipeline → delivery.

This is what the API (and TheHouse's MCP gateway) talk to. A background sweeper fires
windows whose timers expired (spec §5.2 fire condition 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.config import settings
from thehouse.core.dispatcher.service import Dispatcher
from thehouse.core.intake.service import IntakeService
from thehouse.core.models import (
    DEDUP_SAFE_MODES,
    CallerRequest,
    FireReason,
    RequestStatus,
    now_ms,
)
from thehouse.core.pipeline import BatchPipeline
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import audit, request_log
from thehouse.core.window.manager import WindowManager

SWEEP_INTERVAL_S = 0.05

logger = logging.getLogger("thehouse.service")


class AggregatorService:
    def __init__(
        self, engine: AsyncEngine, redis: Any, dispatcher: Dispatcher, semantic=None
    ):
        self.engine = engine
        self.redis = redis
        self.intake = IntakeService(engine, redis, semantic=semantic)
        self.registry = RegistryService(engine)
        self.window = WindowManager(redis, semantic=semantic)
        self.pipeline = BatchPipeline(engine, redis, dispatcher)
        self._sweeper: asyncio.Task | None = None

    async def submit(
        self,
        asp_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        caller_id: str,
        priority: bool = False,
    ) -> CallerRequest:
        """Accept a request; if it fires its window, process the batch inline."""
        req = await self.intake.accept(asp_id, tool_name, arguments, caller_id, priority)
        if req.status == RequestStatus.MERGED and priority:
            # a priority caller paid to skip the wait — fire the window its slot owner
            # sits in, so both are answered now (the merge fan-out delivers this one)
            entry = await self.intake.lookup_asp(asp_id)
            batch = await self.window.fire(asp_id, FireReason.PRIORITY)
            if batch is not None:
                await self.pipeline.process(batch, entry)
            return req
        if req.status != RequestStatus.QUEUED:
            return req  # cached or merged — no window interaction

        entry = await self.intake.lookup_asp(asp_id)
        batch = await self.window.submit(req, entry.break_even_batch_size)
        if batch is not None:
            await self.pipeline.process(batch, entry)
        return req

    async def sweep_once(self) -> int:
        """Fire every window whose timer expired. Returns number of batches fired."""
        fired = 0
        for entry in await self.registry.list_all(active_only=True):
            batch = await self.window.check_timer(entry.asp_id, entry.window_timer_ms)
            if batch is not None:
                await self.pipeline.process(batch, entry)
                fired += 1
        await self.expire_stale()
        return fired

    async def reconcile(self) -> int:
        """Crash recovery, run at startup: any request logged `queued` but absent from
        its redis queue was paid for and then lost mid-flight — re-queue it so the next
        window delivers it. Returns the number of requests recovered."""
        from thehouse.core.window.manager import WINDOW_OPEN_KEY

        requeued = 0
        for entry in await self.registry.list_all(active_only=True):
            oldest: int | None = None
            in_queue = {r.request_id for r in await self.window.queue.peek_all(entry.asp_id)}
            async with self.engine.connect() as conn:
                rows = (
                    await conn.execute(
                        select(request_log).where(
                            request_log.c.asp_id == entry.asp_id,
                            request_log.c.status == RequestStatus.QUEUED.value,
                        )
                    )
                ).mappings().all()
            for row in rows:
                if row["request_id"] in in_queue:
                    continue
                from thehouse.core.deduplicator.service import fingerprint

                req = CallerRequest(
                    request_id=row["request_id"],
                    asp_id=row["asp_id"],
                    tool_name=row["tool_name"],
                    arguments=row["arguments"] or {},
                    query=row["query"],
                    caller_id=row["caller_id"],
                    priority=bool(row["priority"]),
                    received_at_ms=row["received_at_ms"],
                    fingerprint=(
                        fingerprint(row["tool_name"], row["arguments"] or {})
                        if entry.mode in DEDUP_SAFE_MODES
                        else None
                    ),
                )
                await self.window.queue.push(req)
                requeued += 1
                oldest = req.received_at_ms if oldest is None else min(oldest, req.received_at_ms)
            if oldest is not None:
                # re-open the window clock too (queue.push alone leaves no open
                # timestamp, and check_timer never fires a window it can't date) —
                # backdated to the oldest recovered request so the very next sweep
                # fires it instead of waiting for fresh traffic
                await self.redis.set(
                    WINDOW_OPEN_KEY.format(asp_id=entry.asp_id), oldest, nx=True
                )
        if requeued:
            await audit("reconcile_requeued", {"count": requeued}, engine=self.engine)
            logger.warning("reconcile: re-queued %d paid-but-lost request(s)", requeued)
        return requeued

    async def expire_stale(self) -> int:
        """Fail loudly instead of hanging silently: requests still undelivered past the
        TTL flip to status=failed with an explanation, and the expiry is audited."""
        ttl_ms = settings.request_ttl_s * 1000
        if ttl_ms <= 0:
            return 0
        cutoff = now_ms() - ttl_ms
        async with self.engine.begin() as conn:
            res = await conn.execute(
                update(request_log)
                .where(
                    request_log.c.status.in_(
                        (RequestStatus.QUEUED.value, RequestStatus.MERGED.value)
                    ),
                    request_log.c.received_at_ms < cutoff,
                )
                .values(
                    status=RequestStatus.FAILED.value,
                    result=f"expired: no delivery within {settings.request_ttl_s}s "
                    "— contact the operator with this request_id",
                )
            )
        expired = res.rowcount or 0
        if expired:
            # purge the same stale entries from the redis queues so a late window fire
            # can't resurrect a request already reported failed to its caller
            for entry in await self.registry.list_all(active_only=True):
                for req in await self.window.queue.peek_all(entry.asp_id):
                    if req.received_at_ms < cutoff:
                        await self.window.queue.remove(req)
            await audit("requests_expired", {"count": expired}, engine=self.engine)
            logger.warning("expired %d stale request(s) past %ds TTL", expired, settings.request_ttl_s)
        return expired

    def start_sweeper(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                try:
                    await self.sweep_once()
                except Exception:
                    logger.exception("sweep failed; continuing")

        self._sweeper = asyncio.create_task(loop())

    async def stop_sweeper(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None

    async def get_result(self, request_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(request_log).where(request_log.c.request_id == request_id)
                )
            ).mappings().first()
        if row is None:
            return None
        return {
            "request_id": row["request_id"],
            "status": row["status"],
            "result": row["result"],
            "charged": row["charged"],
            "batch_id": row["batch_id"],
            "merged_into": row["merged_into"],
        }
