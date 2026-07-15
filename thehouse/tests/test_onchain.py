"""Phase 11: end-to-end paid flow — callers pay TheHouse (402-gated MCP gateway),
TheHouse pays the target, margin settles as inbound − outbound in the settlements ledger."""

import base64
import json

import httpx
from sqlalchemy import select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import Transport
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import settlements
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.onchain.payments import (
    DevPaymentVerifier,
    DevSigner,
    SettlementLedger,
    build_challenge,
    from_base_units,
    make_payment_hook,
    to_base_units,
)
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def test_base_units_roundtrip():
    assert to_base_units(0.8) == "800000"
    assert from_base_units("800000") == 0.8


def test_challenge_encodes_price_and_payee():
    raw = build_challenge("https://thehouse/mcp", 0.8, "0xTHEHOUSE")
    decoded = json.loads(base64.b64decode(raw))
    accept = decoded["accepts"][0]
    assert decoded["x402Version"] == 2
    assert accept["scheme"] == "exact"
    assert accept["network"] == "eip155:196"
    assert accept["amount"] == "800000"
    assert accept["payTo"] == "0xTHEHOUSE"


def test_dev_verifier_rejects_underpayment():
    v = DevPaymentVerifier()
    assert v.verify("DEV-PAYMENT 0xA 800000", 0.8) is not None
    assert v.verify("DEV-PAYMENT 0xA 700000", 0.8) is None
    assert v.verify("garbage", 0.8) is None


def make_stack(engine, redis, sim_app, endpoint: str):
    ledger = SettlementLedger(engine)
    target_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app), base_url="http://sim"
    )
    dispatcher = Dispatcher(
        {
            Transport.MCP: McpHttpCaller(
                client=target_client,
                payment_hook=make_payment_hook(DevSigner("0xTHEHOUSE"), ledger),
            )
        }
    )
    agg = AggregatorService(engine, redis, dispatcher)
    gateway = build_gateway_app(agg, DevPaymentVerifier(), ledger, wallet_address="0xTHEHOUSE")
    return agg, gateway, ledger


async def test_unpaid_call_gets_402_challenge(engine, redis):
    await seed_asp(engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai")
    _, gateway, _ = make_stack(engine, redis, build_sim_asp_app(), "http://sim/mcp/news_ai")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse"
    ) as client:
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "news_ai.get_news", "arguments": {"query": "current date"}},
            },
        )
    assert resp.status_code == 402
    challenge = json.loads(base64.b64decode(resp.headers["PAYMENT-REQUIRED"]))
    assert challenge["accepts"][0]["amount"] == to_base_units(1.0)
    assert challenge["accepts"][0]["payTo"] == "0xTHEHOUSE"


async def test_end_to_end_paid_aggregated_flow(engine, redis):
    """Two paying callers → one paid target call → margin = 1.6 in − 1.0 out."""
    await seed_asp(
        engine,
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim/mcp/paid_news_ai",
        break_even_batch_size=2,
    )
    sim_app = build_sim_asp_app()
    _, gateway, _ = make_stack(engine, redis, sim_app, "http://sim/mcp/paid_news_ai")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse"
    ) as client:
        def call(i, query, payer):
            return client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0", "id": i, "method": "tools/call",
                    "params": {"name": "news_ai.get_news", "arguments": {"query": query}},
                },
                headers={"PAYMENT-SIGNATURE": f"DEV-PAYMENT {payer} 1000000"},
            )

        import asyncio

        r1, r2 = await asyncio.gather(
            call(1, "current date", "0xAGENT_A"),
            call(2, "current president name", "0xAGENT_B"),
        )

    b1, b2 = r1.json(), r2.json()
    texts = {b1["result"]["content"][0]["text"], b2["result"]["content"][0]["text"]}
    assert any("July 5 2026" in t for t in texts)
    assert any("Donald Trump" in t for t in texts)

    # target was paid exactly once, via the signed x402 replay
    assert len(sim_app.state.payments_received) == 1
    assert sim_app.state.payments_received[0].startswith("DEV-PAYMENT 0xTHEHOUSE")

    async with engine.connect() as conn:
        rows = (await conn.execute(select(settlements))).mappings().all()
    inbound = [r for r in rows if r["direction"] == "in"]
    outbound = [r for r in rows if r["direction"] == "out"]
    assert {r["counterparty"] for r in inbound} == {"0xAGENT_A", "0xAGENT_B"}
    assert sum(r["amount_usdt"] for r in inbound) == 1.6
    assert len(outbound) == 1 and outbound[0]["amount_usdt"] == 1.0
    # margin sits in TheHouse's wallet: +0.6 USDT on this batch


async def test_tools_list_mirrors_registry(engine, redis):
    await seed_asp(engine, tool_schema=LLM_SCHEMA, description="News agent via TheHouse")
    _, gateway, _ = make_stack(engine, redis, build_sim_asp_app(), "")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse"
    ) as client:
        resp = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
    tools = resp.json()["result"]["tools"]
    assert tools[0]["name"] == "news_ai.get_news"
    assert tools[0]["inputSchema"]["properties"]["query"]["type"] == "string"
