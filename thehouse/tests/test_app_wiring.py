"""Deployment wiring: one `uvicorn core.api:app` serves everything — pages, REST, and
the paid MCP gateway mounted at /mcp; the unpaid REST intake is operator-only in prod."""

from httpx import ASGITransport, AsyncClient

from thehouse.core.config import settings
from thehouse.core.dispatcher.service import Dispatcher
from thehouse.core.service import AggregatorService
from thehouse.tests.conftest import seed_asp


async def test_lifespan_mounts_gateway_and_serves_all_surfaces(engine, redis):
    from thehouse.core.api import app, lifespan

    async with lifespan(app):
        # the gateway serves against the app's own (global, dev-profile) engine
        await seed_asp(app.state.engine)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/")).status_code == 200
            assert (await client.get("/directory")).status_code == 200
            assert (await client.get("/seal.svg")).status_code == 200

            init = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert init.status_code == 200
            assert init.json()["result"]["serverInfo"]["name"] == "TheHouse"

            # unpaid tools/call on the mounted gateway → 402 with an x402 challenge
            call = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "news_ai.get_news", "arguments": {"query": "hi"}},
                },
            )
            assert call.status_code == 402
            assert "PAYMENT-REQUIRED" in call.headers


async def test_rest_intake_is_internal_only_in_prod(engine, redis, monkeypatch):
    from thehouse.core.api import app

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))
    await seed_asp(engine, break_even_batch_size=9)

    monkeypatch.setattr(settings, "profile", "prod")
    monkeypatch.setattr(settings, "internal_api_token", "s3cret")

    body = {
        "asp_id": "news_ai", "tool_name": "news_ai.get_news",
        "arguments": {"query": "q"}, "caller_id": "c1",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/v1/call", json=body)).status_code == 403
        assert (await client.get("/v1/queue/news_ai")).status_code == 403
        # the business's finances are operator-only too
        assert (await client.get("/metrics")).status_code == 403
        assert (await client.get("/desk")).status_code == 403

        ok = await client.post("/v1/call", json=body, headers={"X-Internal-Token": "s3cret"})
        assert ok.status_code == 202
        m = await client.get("/metrics", headers={"X-Internal-Token": "s3cret"})
        assert m.status_code == 200 and "thehouse_requests" in m.text

        # public surfaces stay public in prod
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/v1/directory")).status_code == 200
