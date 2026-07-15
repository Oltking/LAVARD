"""Fifth audit pass (runtime/load): under concurrency, the money ledgers stay balanced and no
settlement is ever booked to an unidentifiable caller (which would drift reconciliation)."""

import asyncio

import httpx
from sqlalchemy import func, select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.models import Transport
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import request_log, settlements
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


async def test_concurrent_load_stays_balanced_and_never_books_phantom(engine, redis):
    for a in ("news_ai", "price_ai"):
        await seed_asp(engine, asp_id=a, tool_name=f"{a}.go", tool_schema=LLM_SCHEMA,
                       endpoint="http://sim/mcp/news_ai", break_even_batch_size=3, window_timer_ms=20)
    sim = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_sim_asp_app()),
                            base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=sim)}))
    agg.start_sweeper()
    try:
        N = 120
        await asyncio.gather(*[
            agg.submit(["news_ai", "price_ai"][i % 2], f"{['news_ai','price_ai'][i%2]}.go",
                       {"query": f"q{i % 20}"}, f"c{i}") for i in range(N)])
        for _ in range(400):
            await asyncio.sleep(0.02)
            async with engine.connect() as c:
                if (await c.execute(select(func.count())
                                    .where(request_log.c.status == "queued"))).scalar() == 0:
                    break
    finally:
        await agg.stop_sweeper()

    async with engine.connect() as c:
        left = (await c.execute(select(func.count())
                                .where(request_log.c.status == "queued"))).scalar()
        unknown = (await c.execute(select(func.count())
                                   .where(settlements.c.counterparty == "unknown"))).scalar()
        # no request has more than one inbound settlement (no double-charge)
        dupes = (await c.execute(
            select(settlements.c.request_id, func.count()).where(settlements.c.direction == "in")
            .group_by(settlements.c.request_id).having(func.count() > 1))).all()

    assert left == 0                      # nothing lost/stranded under load
    assert unknown == 0                   # never booked money to a phantom caller
    assert dupes == []                    # never double-charged a caller
    rec = await EconomicsEngine(engine).reconcile_settlements(since_ms=0)
    assert rec.inbound_balanced is True   # collected == billed
