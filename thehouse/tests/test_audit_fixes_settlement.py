"""Deep-audit fixes on the settlement/money layer.

HIGH-1: reconcile counts only COLLECTED (settled) inbound — an invoiced-but-unpaid charge is drift.
HIGH-2: inbound is recorded as the caller's settled x402 authorization (scheme x402_settle), not an
        a2a pending charge.
MEDIUM-5: merged/fanned callers are attributed to their real caller_id, not a placeholder.
"""

import httpx
from sqlalchemy import insert, select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.models import Transport, now_ms
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import request_log, settlements
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


async def _seed_request(engine, rid, caller, charged):
    async with engine.begin() as conn:
        await conn.execute(insert(request_log).values(
            request_id=rid, asp_id="x", tool_name="x.go", caller_id=caller,
            status="delivered", charged=charged, received_at_ms=now_ms()))


async def test_uncollected_charge_shows_as_drift_not_balanced(engine):
    await _seed_request(engine, "r1", "cA", 0.8)
    await _seed_request(engine, "r2", "cB", 0.8)
    async with engine.begin() as conn:
        # r1 collected on-chain; r2 only invoiced (pending)
        await conn.execute(insert(settlements).values(
            ts_ms=now_ms(), direction="in", counterparty="cA", amount_usdt=0.8,
            scheme="x402_settle", network="eip155:196", request_id="r1", settle_status="settled"))
        await conn.execute(insert(settlements).values(
            ts_ms=now_ms(), direction="in", counterparty="cB", amount_usdt=0.8,
            scheme="a2a_pay", network="eip155:196", request_id="r2", settle_status="pending"))

    rec = await EconomicsEngine(engine).reconcile_settlements(since_ms=0)
    assert rec.inbound_charged == 1.6        # both invoiced
    assert rec.inbound_settled == 0.8        # only one collected
    assert rec.inbound_balanced is False     # the 0.8 uncollected surfaces as drift


async def test_all_collected_balances(engine):
    await _seed_request(engine, "r1", "cA", 0.8)
    async with engine.begin() as conn:
        await conn.execute(insert(settlements).values(
            ts_ms=now_ms(), direction="in", counterparty="cA", amount_usdt=0.8,
            scheme="x402_settle", network="eip155:196", request_id="r1", settle_status="settled"))
    rec = await EconomicsEngine(engine).reconcile_settlements(since_ms=0)
    assert rec.inbound_balanced is True


async def test_merged_caller_keeps_real_identity_in_settlement(engine, redis):
    # two identical queries from different callers → the second merges into the first slot; both
    # pay, and each inbound settlement must carry its OWN caller_id (audit MEDIUM-5).
    await seed_asp(engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
                   break_even_batch_size=2, window_timer_ms=0)  # merge → 1 slot → fires on timer
    sim = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_sim_asp_app()),
                            base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=sim)}))
    r1 = await agg.submit("news_ai", "news_ai.get_news", {"query": "current date"}, "caller_ALICE")
    await agg.submit("news_ai", "news_ai.get_news", {"query": "current date"}, "caller_BOB")
    for _ in range(40):
        await agg.sweep_once()
        if (await agg.get_result(r1.request_id) or {}).get("status") == "delivered":
            break
    async with engine.connect() as conn:
        cps = {r for (r,) in (await conn.execute(
            select(settlements.c.counterparty).where(settlements.c.direction == "in"))).all()}
    assert cps == {"caller_ALICE", "caller_BOB"}          # real identities, not "merged"/"unknown"
