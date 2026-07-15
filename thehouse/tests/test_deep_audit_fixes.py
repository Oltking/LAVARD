"""Deep-audit fixes (2026-07-13):
1. max_batch_size DB column default is 2 (owner cap), not 8.
4. Settlement reconciliation surfaces on-chain vs internal-ledger drift.
5. RegistryService.upsert always re-derives thehouse_price from the current fee (no stale discount).
(2 privacy-isolation and 3 async-semantic are covered in test_audit_fixes_2 / test_semantic_dedup.)
"""

import httpx
from sqlalchemy import insert, select, update

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.models import ASPEntry, Transport
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import asp_registry, request_log
from thehouse.onchain.payments import DevPaymentVerifier, SettlementLedger
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


# ---- 1. max_batch_size column default is 2 -----------------------------------------
async def test_max_batch_size_db_default_is_two(engine):
    # insert WITHOUT specifying max_batch_size → the column default applies
    async with engine.begin() as conn:
        await conn.execute(insert(asp_registry).values(asp_id="x", tool_name="x.go"))
        val = (await conn.execute(
            select(asp_registry.c.max_batch_size).where(asp_registry.c.asp_id == "x")
        )).scalar()
    assert val == 2


# ---- 5. upsert always re-derives thehouse_price ------------------------------------
async def test_upsert_rederives_price_on_fee_change(engine):
    reg = RegistryService(engine)
    await reg.upsert(ASPEntry(asp_id="a", tool_name="a.go", original_price_per_call=1.0))
    first = await reg.get("a")
    assert first.thehouse_price == 0.8

    # fee doubles but a stale thehouse_price is carried in — upsert must re-derive, not keep it
    await reg.upsert(ASPEntry(asp_id="a", tool_name="a.go",
                              original_price_per_call=2.0, thehouse_price=0.8))
    second = await reg.get("a")
    assert second.thehouse_price == 1.6      # 2.0 × 0.80, not the stale 0.8


# ---- 4. settlement reconciliation --------------------------------------------------
async def _paid_call(client, query, header):
    return await client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "news_ai.get_news", "arguments": {"query": query}},
    }, headers={"PAYMENT-SIGNATURE": header})


async def test_reconciliation_balances_then_detects_drift(engine, redis):
    await seed_asp(engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
                   break_even_batch_size=1)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_sim_asp_app()),
                               base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))
    gateway = build_gateway_app(agg, DevPaymentVerifier(), SettlementLedger(engine))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                 base_url="http://thehouse") as c:
        # authorize the ceiling (full price); a solo caller settles the solo tier (~full − 0.1%)
        r = await _paid_call(c, "current date", "DEV-PAYMENT 0xA 1000000 n1")
        assert r.status_code == 200 and "result" in r.json()

    econ = EconomicsEngine(engine)
    rec = await econ.reconcile_settlements(since_ms=0)
    # inbound (caller money) reconciles exactly: billed 0.999 (solo) == settled 0.999
    assert rec.inbound_settled == 0.999 and rec.inbound_charged == 0.999
    assert rec.inbound_balanced is True

    # simulate a billing bug: charge drifts from what settled on-chain → reconciliation flags it
    async with engine.begin() as conn:
        await conn.execute(update(request_log).values(charged=0.9))
    rec2 = await econ.reconcile_settlements(since_ms=0)
    assert rec2.inbound_balanced is False
    assert abs(rec2.inbound_delta - (0.999 - 0.9)) < 1e-9
