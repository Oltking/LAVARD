import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from thehouse.core.intake.service import IntakeService, UnknownASPError
from thehouse.core.storage.db import request_log
from thehouse.core.window.queue import ASPQueue
from thehouse.tests.conftest import seed_asp


async def test_accept_stamps_request_id_and_enqueues(engine, redis):
    await seed_asp(engine)
    intake = IntakeService(engine, redis)

    req = await intake.accept(
        asp_id="news_ai",
        tool_name="news_ai.get_news",
        arguments={"query": "current president name"},
        caller_id="caller_1",
    )

    assert req.request_id.startswith("req_")
    assert req.query == "current president name"

    queue = ASPQueue(redis)
    pending = await queue.peek_all("news_ai")
    assert [r.request_id for r in pending] == [req.request_id]

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(request_log).where(request_log.c.request_id == req.request_id)
            )
        ).mappings().first()
    assert row is not None
    assert row["status"] == "queued"
    assert row["caller_id"] == "caller_1"


async def test_unknown_asp_rejected(engine, redis):
    intake = IntakeService(engine, redis)
    with pytest.raises(UnknownASPError):
        await intake.accept(
            asp_id="ghost_ai", tool_name="ghost_ai.x", arguments={}, caller_id="c1"
        )


async def test_queue_is_per_asp_and_fifo(engine, redis):
    await seed_asp(engine)
    await seed_asp(engine, asp_id="price_ai", tool_name="price_ai.get_price", mode="B_native")
    intake = IntakeService(engine, redis)

    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "a"}, "c1")
    r2 = await intake.accept("news_ai", "news_ai.get_news", {"query": "b"}, "c2")
    r3 = await intake.accept("price_ai", "price_ai.get_price", {"symbol": "BTC"}, "c3")

    queue = ASPQueue(redis)
    news = await queue.peek_all("news_ai")
    price = await queue.peek_all("price_ai")
    assert [r.request_id for r in news] == [r1.request_id, r2.request_id]
    assert [r.request_id for r in price] == [r3.request_id]

    drained = await queue.drain("news_ai")
    assert len(drained) == 2
    assert await queue.size("news_ai") == 0


async def test_api_intake_roundtrip(engine, redis):
    await seed_asp(engine, break_even_batch_size=5)
    from thehouse.core.api import app
    from thehouse.core.dispatcher.service import Dispatcher
    from thehouse.core.service import AggregatorService

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/call",
            json={
                "asp_id": "news_ai",
                "tool_name": "news_ai.get_news",
                "arguments": {"query": "current date"},
                "caller_id": "agent_A",
            },
        )
        assert resp.status_code == 202
        request_id = resp.json()["request_id"]

        q = await client.get("/v1/queue/news_ai")
        body = q.json()
        assert body["size"] == 1
        assert body["requests"][0]["request_id"] == request_id

        missing = await client.post(
            "/v1/call",
            json={"asp_id": "nope", "tool_name": "nope.t", "arguments": {}, "caller_id": "x"},
        )
        assert missing.status_code == 404
