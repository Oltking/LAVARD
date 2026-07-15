"""Production hardening (gap fixes 1–4 + 7):
replay protection, backpressure (rate limit / queue depth), crash reconciliation,
request TTL expiry, and the observability surfaces."""

import httpx
from sqlalchemy import select, update

from thehouse.core.config import settings
from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.intake.service import IntakeService, QueueFullError, RateLimitedError
from thehouse.core.models import RequestStatus, Transport
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import request_log
from thehouse.onchain.payments import DevPaymentVerifier, SettlementLedger
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app

import pytest


def make_gateway(engine, redis, sim_app):
    ledger = SettlementLedger(engine)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app), base_url="http://sim"
    )
    dispatcher = Dispatcher({Transport.MCP: McpHttpCaller(client=client)})
    agg = AggregatorService(engine, redis, dispatcher)
    return agg, build_gateway_app(agg, DevPaymentVerifier(), ledger)


async def _paid_call(client, query, payer, units="1000000", nonce=""):
    header = f"DEV-PAYMENT {payer} {units}"
    if nonce:
        header += f" {nonce}"
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "news_ai.get_news", "arguments": {"query": query}},
        },
        headers={"PAYMENT-SIGNATURE": header},
    )


# ---- 2. replay protection ---------------------------------------------------

async def test_replayed_authorization_is_refused(engine, redis):
    await seed_asp(
        engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
        break_even_batch_size=1,
    )
    _, gateway = make_gateway(engine, redis, build_sim_asp_app())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse"
    ) as client:
        first = await _paid_call(client, "current date", "0xA")
        assert first.status_code == 200 and "result" in first.json()

        # byte-identical authorization presented again → refused with a fresh 402
        replay = await _paid_call(client, "current date", "0xA")
        assert replay.status_code == 402

        # a new authorization (different nonce) from the same payer works
        fresh = await _paid_call(client, "current date", "0xA", nonce="n2")
        assert fresh.status_code == 200 and "result" in fresh.json()


# ---- 4. backpressure ----------------------------------------------------------

async def test_rate_limit_refuses_before_charging(engine, redis, monkeypatch):
    await seed_asp(engine, break_even_batch_size=9)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    intake = IntakeService(engine, redis)

    await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "spammer")
    await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "spammer")
    with pytest.raises(RateLimitedError):
        await intake.accept("news_ai", "news_ai.get_news", {"query": "q3"}, "spammer")
    # other callers are unaffected
    await intake.accept("news_ai", "news_ai.get_news", {"query": "q4"}, "polite")


async def test_queue_depth_cap_and_slot_release(engine, redis, monkeypatch):
    await seed_asp(engine, break_even_batch_size=9)
    monkeypatch.setattr(settings, "max_queue_depth", 1)
    intake = IntakeService(engine, redis)

    await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "c1")
    with pytest.raises(QueueFullError):
        await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "c2")

    # the refused request's dedup slot was released: an identical request later (after
    # capacity frees) must not merge into a ghost
    monkeypatch.setattr(settings, "max_queue_depth", 10)
    ok = await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "c3")
    assert ok.status == RequestStatus.QUEUED
    assert ok.merged_into is None


async def test_gateway_refusal_does_not_consume_payment(engine, redis, monkeypatch):
    await seed_asp(
        engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
        break_even_batch_size=1,
    )
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    _, gateway = make_gateway(engine, redis, build_sim_asp_app())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse"
    ) as client:
        ok = await _paid_call(client, "current date", "0xA", nonce="n1")
        assert "result" in ok.json()

        refused = await _paid_call(client, "another question", "0xA", nonce="n2")
        body = refused.json()
        assert body["error"]["code"] == -32029
        assert "payment not consumed" in body["error"]["message"]

        # the SAME authorization is spendable once the limit clears — it was released
        monkeypatch.setattr(settings, "rate_limit_per_minute", 100)
        retry = await _paid_call(client, "another question", "0xA", nonce="n2")
        assert "result" in retry.json()


# ---- 1. crash reconciliation + 7. TTL ------------------------------------------

async def test_reconcile_requeues_paid_but_lost_requests(engine, redis):
    await seed_asp(engine, break_even_batch_size=9)
    intake = IntakeService(engine, redis)
    req = await intake.accept("news_ai", "news_ai.get_news", {"query": "lost"}, "c1")
    assert req.status == RequestStatus.QUEUED

    # simulate a crash: redis state (queues, windows, slots) is wiped, DB survives
    await redis.flushall()

    agg = AggregatorService(engine, redis, Dispatcher({}))
    assert await agg.window.queue.size("news_ai") == 0
    recovered = await agg.reconcile()
    assert recovered == 1
    pending = await agg.window.queue.peek_all("news_ai")
    assert pending[0].request_id == req.request_id
    # idempotent: a second pass finds nothing missing
    assert await agg.reconcile() == 0


async def test_stale_requests_expire_loudly(engine, redis, monkeypatch):
    await seed_asp(engine, break_even_batch_size=9)
    intake = IntakeService(engine, redis)
    req = await intake.accept("news_ai", "news_ai.get_news", {"query": "doomed"}, "c1")

    monkeypatch.setattr(settings, "request_ttl_s", 1)
    async with engine.begin() as conn:
        await conn.execute(
            update(request_log)
            .where(request_log.c.request_id == req.request_id)
            .values(received_at_ms=0)  # ancient
        )

    agg = AggregatorService(engine, redis, Dispatcher({}))
    assert await agg.expire_stale() == 1

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(request_log).where(request_log.c.request_id == req.request_id)
            )
        ).mappings().first()
    assert row["status"] == "failed"
    assert "expired" in row["result"]


# ---- 3. observability -----------------------------------------------------------

async def test_metrics_and_desk_serve(engine, redis):
    from httpx import ASGITransport, AsyncClient

    from thehouse.core.api import app

    await seed_asp(engine)
    intake = IntakeService(engine, redis)
    await intake.accept("news_ai", "news_ai.get_news", {"query": "q"}, "c1")

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        m = await client.get("/metrics")
        assert m.status_code == 200
        assert 'thehouse_requests{status="queued"} 1' in m.text
        assert 'thehouse_queue_depth{asp_id="news_ai"} 1' in m.text

        d = await client.get("/desk")
        assert d.status_code == 200
        assert "The Desk" in d.text
        assert "news_ai" in d.text
