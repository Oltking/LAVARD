"""Audit regressions (2026-07-07):
1. Side-effectful (non_aggregatable) calls are never cached and never merged — every
   identical submission reaches the target.
2. Cache hits record what the 402 gate actually collected (priority price included).
3. A priority request that merges into a pending slot still fires the window it paid
   to skip, and is charged its own priority price.
"""

import httpx
from sqlalchemy import select

from thehouse.core.deduplicator.service import DedupService, fingerprint
from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.intake.service import IntakeService
from thehouse.core.models import RequestStatus, Transport
from thehouse.core.storage.db import request_log
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def make_aggregator(engine, redis, app):
    from thehouse.core.service import AggregatorService

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sim-asp"
    )
    return AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))


async def test_side_effectful_calls_never_merge_or_cache(engine, redis):
    await seed_asp(
        engine,
        mode="non_aggregatable",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/news_ai",
        window_timer_ms=0,
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    # two byte-identical "transfers": both must execute — no merge
    r1 = await agg.submit("news_ai", "news_ai.get_news", {"query": "send 100 to 0xA"}, "c1")
    r2 = await agg.submit("news_ai", "news_ai.get_news", {"query": "send 100 to 0xA"}, "c2")
    assert r1.status == RequestStatus.QUEUED
    assert r2.status == RequestStatus.QUEUED  # NOT merged
    assert r2.merged_into is None
    assert len(app.state.calls) == 2  # break-even fire → direct route → one call each

    # a third identical one after delivery must dispatch again — no cache serve
    r3 = await agg.submit("news_ai", "news_ai.get_news", {"query": "send 100 to 0xA"}, "c3")
    assert r3.status == RequestStatus.QUEUED
    await agg.sweep_once()
    assert len(app.state.calls) == 3

    async with engine.connect() as conn:
        rows = (await conn.execute(select(request_log))).mappings().all()
    assert all(r["status"] == "delivered" for r in rows)
    assert all(r["charged"] == 1.0 for r in rows)  # direct route: original price, no discount


async def test_cache_hit_records_priority_price(engine, redis):
    await seed_asp(engine)  # A_llm, 1.00 → 0.80, priority = 0.99
    intake = IntakeService(engine, redis)
    fp = fingerprint("news_ai.get_news", {"query": "BTC price"})
    await DedupService(redis).cache_result("news_ai", fp, "$107,432", ttl_seconds=30)

    req = await intake.accept(
        "news_ai", "news_ai.get_news", {"query": "BTC price"}, "c1", priority=True
    )
    assert req.status == RequestStatus.CACHED

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(request_log).where(request_log.c.request_id == req.request_id)
            )
        ).mappings().first()
    assert row["charged"] == 0.99  # what the 402 gate collected — not the base 0.80


async def test_merged_priority_fires_window_and_pays_its_price(engine, redis):
    await seed_asp(
        engine,
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/news_ai",
        break_even_batch_size=9,  # never fires on size
        window_timer_ms=60_000,   # never fires on timer in this test
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    r1 = await agg.submit("news_ai", "news_ai.get_news", {"query": "BTC price right now"}, "c1")
    assert r1.status == RequestStatus.QUEUED
    assert len(app.state.calls) == 0  # window holding

    # identical query, priority — merges into r1's slot AND fires the window now
    r2 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "BTC price right now"}, "c2", priority=True
    )
    assert r2.status == RequestStatus.MERGED
    assert r2.merged_into == r1.request_id
    assert len(app.state.calls) == 1  # one dispatch, immediately

    async with engine.connect() as conn:
        rows = {
            r["request_id"]: r
            for r in (await conn.execute(select(request_log))).mappings().all()
        }
    assert rows[r1.request_id]["status"] == "delivered"
    assert rows[r2.request_id]["status"] == "delivered"
    assert "$107,432" in rows[r2.request_id]["result"]
    assert rows[r1.request_id]["charged"] == 0.8   # waited at the discounted price
    assert rows[r2.request_id]["charged"] == 0.99  # paid priority, got priority
