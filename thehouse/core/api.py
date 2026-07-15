"""FastAPI surface for TheHouse.

Run: uvicorn core.api:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from thehouse.core.config import settings
from thehouse.core.dispatcher.service import Dispatcher
from thehouse.core.intake.service import (
    InactiveASPError,
    QueueFullError,
    RateLimitedError,
    UnknownASPError,
)
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import get_engine, init_db
from thehouse.core.storage.redis_client import get_redis
from thehouse.core.window.queue import ASPQueue


@asynccontextmanager
async def lifespan(app: FastAPI):
    from thehouse.core.logging_setup import setup_logging

    setup_logging()
    await init_db()
    app.state.engine = get_engine()
    app.state.redis = get_redis()
    app.state.aggregator = AggregatorService(
        app.state.engine, app.state.redis, Dispatcher.default()
    )
    await app.state.aggregator.reconcile()  # re-queue paid work lost to a crash
    app.state.aggregator.start_sweeper()
    _mount_gateway(app)
    yield
    await app.state.aggregator.stop_sweeper()


def _mount_gateway(app: FastAPI) -> None:
    """One deployable app: the paid MCP gateway rides at POST /mcp.

    In prod the mount is skipped (loudly) until a facilitator-backed verifier is wired
    (GO_LIVE step 2) — never serve real callers against the dev verifier."""
    from thehouse.gateway.mcp_server import build_gateway_app
    from thehouse.onchain.payments import SettlementLedger, make_verifier

    verifier = make_verifier()
    if verifier is None:
        import logging

        logging.getLogger("thehouse").warning(
            "MCP gateway NOT mounted: no payment verifier for profile %s (GO_LIVE step 2)",
            settings.profile,
        )
        return
    build_gateway_app(
        app.state.aggregator,
        verifier,
        SettlementLedger(app.state.engine),
        dev_allow_unpaid=settings.profile == "dev",
        mount_on=app,
    )


app = FastAPI(title="TheHouse", version="0.1.0", lifespan=lifespan)


class CallIn(BaseModel):
    asp_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    caller_id: str
    priority: bool = False


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def landing_page():
    from fastapi.responses import HTMLResponse

    from thehouse.directory.landing import render_html as render_landing

    return HTMLResponse(render_landing())


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus text format, no client library needed. Operator-only in prod — these
    are the business's finances; the scraper sends X-Internal-Token."""
    _internal_only(request)
    from fastapi.responses import PlainTextResponse
    from sqlalchemy import func, select

    from thehouse.core.storage.db import asp_registry, economics_ledger, request_log, settlements
    from thehouse.core.window.queue import ASPQueue

    L, R, S, A = economics_ledger.c, request_log.c, settlements.c, asp_registry.c
    engine = app.state.engine
    async with engine.connect() as conn:
        statuses = (
            await conn.execute(select(R.status, func.count()).group_by(R.status))
        ).all()
        ledger = (
            await conn.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(L.thehouse_revenue_collected), 0.0),
                    func.coalesce(func.sum(L.target_cost_paid), 0.0),
                    func.coalesce(func.sum(L.gross_margin), 0.0),
                    func.coalesce(func.sum(L.dedup_hits), 0),
                )
            )
        ).first()
        # cache hits settle at intake, never through a batch — count their revenue too
        cached = (
            await conn.execute(
                select(func.count(), func.coalesce(func.sum(R.charged), 0.0)).where(
                    R.status == "cached"
                )
            )
        ).first()
        settled = (
            await conn.execute(
                select(S.direction, func.coalesce(func.sum(S.amount_usdt), 0.0))
                .group_by(S.direction)
            )
        ).all()
        active = (await conn.execute(select(A.asp_id).where(A.active.is_(True)))).scalars().all()

    queue = ASPQueue(app.state.redis)
    lines = [
        # current status counts move in both directions (queued → delivered) — a gauge
        "# TYPE thehouse_requests gauge",
        *(f'thehouse_requests{{status="{s}"}} {n}' for s, n in statuses),
        "# TYPE thehouse_batches_total counter",
        f"thehouse_batches_total {ledger[0]}",
        "# TYPE thehouse_revenue_usdt_total counter",
        f"thehouse_revenue_usdt_total {ledger[1] + cached[1]:.6f}",
        "# TYPE thehouse_target_cost_usdt_total counter",
        f"thehouse_target_cost_usdt_total {ledger[2]:.6f}",
        "# TYPE thehouse_gross_margin_usdt_total counter",
        f"thehouse_gross_margin_usdt_total {ledger[3] + cached[1]:.6f}",
        "# TYPE thehouse_dedup_hits_total counter",
        f"thehouse_dedup_hits_total {ledger[4] + cached[0]}",
        "# TYPE thehouse_settlements_usdt_total counter",
        *(f'thehouse_settlements_usdt_total{{direction="{d}"}} {a:.6f}' for d, a in settled),
        "# TYPE thehouse_queue_depth gauge",
    ]
    for asp_id in active:
        lines.append(f'thehouse_queue_depth{{asp_id="{asp_id}"}} {await queue.size(asp_id)}')
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/desk")
async def desk_page(request: Request):
    """Operator dashboard — internal-only in prod."""
    _internal_only(request)
    from fastapi.responses import HTMLResponse

    from thehouse.directory import desk

    data = await desk.gather(app.state.engine, app.state.redis)
    return HTMLResponse(desk.render_html(data))


@app.get("/seal.svg")
async def seal_svg():
    from fastapi.responses import Response

    from thehouse.directory.landing import standalone_seal

    return Response(standalone_seal(), media_type="image/svg+xml")


def _internal_only(request: Request) -> None:
    """REST intake bypasses the 402 gate, so in prod it is operator-only: callers must
    come through the paid /mcp gateway. Dev profile stays open for local work."""
    if settings.profile != "prod":
        return
    token = settings.internal_api_token
    if not token or request.headers.get("x-internal-token") != token:
        raise HTTPException(
            status_code=403,
            detail="internal endpoint — callers use the paid MCP gateway at /mcp; "
            "operators set THEHOUSE_INTERNAL_API_TOKEN and send X-Internal-Token",
        )


@app.post("/v1/call", status_code=202)
async def submit_call(body: CallIn, request: Request) -> dict[str, Any]:
    """Intake: accept a caller's request for a target ASP tool."""
    _internal_only(request)
    try:
        req = await app.state.aggregator.submit(
            asp_id=body.asp_id,
            tool_name=body.tool_name,
            arguments=body.arguments,
            caller_id=body.caller_id,
            priority=body.priority,
        )
    except UnknownASPError as e:
        raise HTTPException(status_code=404, detail=f"unknown ASP/tool: {e}") from e
    except InactiveASPError as e:
        raise HTTPException(status_code=409, detail=f"ASP inactive: {e}") from e
    except RateLimitedError as e:
        raise HTTPException(status_code=429, detail=f"rate limit exceeded for caller {e}") from e
    except QueueFullError as e:
        raise HTTPException(status_code=503, detail=f"queue full for target {e}") from e
    return {"request_id": req.request_id, "status": req.status.value, "result": req.result}


@app.get("/v1/result/{request_id}")
async def get_result(request_id: str) -> dict[str, Any]:
    res = await app.state.aggregator.get_result(request_id)
    if res is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    return res


@app.get("/v1/directory")
async def directory_json() -> list[dict[str, Any]]:
    from thehouse.directory.service import DirectoryService

    return await DirectoryService(app.state.engine).listing()


@app.get("/directory")
async def directory_page():
    from fastapi.responses import HTMLResponse

    from thehouse.directory.service import DirectoryService, render_html

    rows = await DirectoryService(app.state.engine).listing()
    return HTMLResponse(render_html(rows))


@app.get("/v1/queue/{asp_id}")
async def inspect_queue(asp_id: str, request: Request) -> dict[str, Any]:
    _internal_only(request)
    queue = ASPQueue(app.state.redis)
    pending = await queue.peek_all(asp_id)
    return {
        "asp_id": asp_id,
        "size": len(pending),
        "requests": [r.model_dump() for r in pending],
    }
