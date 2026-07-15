"""Audit regressions (2026-07-08):
1. A tools/call naming a bad tool (or any unexpected failure) never burns the payment
   authorization — it stays spendable on an honest retry.
2. Crash-recovered requests re-open their window clock, so the next sweep fires them
   without waiting for fresh traffic.
3. A totally FAILED split still delivers the full response to every paid caller
   (no refund rail: everything rather than nothing), and nothing is cached.
4. Direct-fallback deliveries record what the 402 gate actually collected — the
   discounted price — not the original price nobody paid.
5. The gateway reports a failed request immediately instead of eating the poll window.
6. TTL-expired requests are purged from the redis queue so a late fire can't
   resurrect them.
"""

import httpx
from sqlalchemy import select, update

from thehouse.core.config import settings
from thehouse.core.deduplicator.service import DedupService
from thehouse.core.dispatcher.service import DispatchError, Dispatcher, McpHttpCaller
from thehouse.core.intake.service import IntakeService
from thehouse.core.models import FireReason, RequestStatus, Transport
from thehouse.core.pipeline import BatchPipeline
from thehouse.core.profiler.profiler import ToolCallResult
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import economics_ledger, request_log
from thehouse.core.window.manager import Batch
from thehouse.onchain.payments import DevPaymentVerifier, SettlementLedger
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def make_gateway(engine, redis, sim_app):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app), base_url="http://sim"
    )
    dispatcher = Dispatcher({Transport.MCP: McpHttpCaller(client=client)})
    agg = AggregatorService(engine, redis, dispatcher)
    return agg, build_gateway_app(agg, DevPaymentVerifier(), SettlementLedger(engine))


async def _paid_call(client, tool, query, header):
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": {"query": query}},
        },
        headers={"PAYMENT-SIGNATURE": header},
    )


# ---- 1. bad tool name / unexpected failure never burns the payment ----------------

async def test_wrong_tool_refused_without_consuming_payment(engine, redis):
    await seed_asp(
        engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
        break_even_batch_size=1,
    )
    _, gw = make_gateway(engine, redis, build_sim_asp_app())
    header = "DEV-PAYMENT 0xA 1000000 n1"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw), base_url="http://thehouse"
    ) as client:
        # asp exists, tool suffix is wrong → clean JSON-RPC error, no payment touched
        bad = await _paid_call(client, "news_ai.bogus_tool", "hi", header)
        assert bad.status_code == 200
        assert bad.json()["error"]["code"] == -32602

        # the SAME authorization then buys a real call — it was never registered
        good = await _paid_call(client, "news_ai.get_news", "current date", header)
        assert good.status_code == 200 and "result" in good.json()


# ---- 2. recovered requests fire on the next sweep ---------------------------------

async def test_reconciled_requests_fire_without_new_traffic(engine, redis):
    await seed_asp(
        engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
        break_even_batch_size=9, window_timer_ms=0,
    )
    intake = IntakeService(engine, redis)
    req = await intake.accept("news_ai", "news_ai.get_news", {"query": "current date"}, "c1")

    await redis.flushall()  # crash: redis gone, DB survives

    agg, _ = make_gateway(engine, redis, build_sim_asp_app())
    assert await agg.reconcile() == 1
    # the very next sweep must fire the recovered window — no fresh traffic needed
    assert await agg.sweep_once() == 1
    res = await agg.get_result(req.request_id)
    assert res["status"] == "delivered" and res["result"]


# ---- 3. FAILED split isolates each caller — never leaks the compound blob ----------

async def test_failed_split_isolates_per_caller_no_leak(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=2)

    class LeakyThenIsolatedDispatcher:
        """The compound (2-arg) call returns an un-splittable blob carrying BOTH callers'
        material; each isolated solo re-dispatch returns just that caller's own answer."""

        async def dispatch(self, entry, arguments):
            q = arguments.get("query", "")
            if "1)" in q or "\n2)" in q:            # the compound prompt
                return ToolCallResult(text="q1's secret and q2's secret, no numbering")
            return ToolCallResult(text=f"isolated answer for: {q}")

    intake = IntakeService(engine, redis)
    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "c1")
    r2 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "c2")
    pipeline = BatchPipeline(engine, redis, LeakyThenIsolatedDispatcher())
    report = await pipeline.process(
        Batch("b1", "news_ai", [r1, r2], FireReason.BREAK_EVEN, 0), entry
    )

    assert len(report.deliveries) == 2
    # PRIVACY: no caller ever receives the compound blob (which held the other's material)
    for d in report.deliveries:
        assert "no numbering" not in d.answer
        assert d.answer.startswith("isolated answer for:")
        assert d.charged == 0.8                    # the price the gate collected
    # the failed compound + 2 isolated re-dispatches = 3 target calls billed
    assert report.target_cost_paid == 3.0
    async with engine.connect() as conn:
        rows = (await conn.execute(select(request_log))).mappings().all()
        quality = (await conn.execute(select(economics_ledger.c.split_quality))).scalar()
    assert all(r["status"] == "delivered" for r in rows)
    assert quality == "failed"                     # still flagged for auto-protection
    # isolated answers are each the caller's OWN → safe to cache
    assert await DedupService(redis).get_cached("news_ai", r1.fingerprint) is not None
    assert await DedupService(redis).get_cached("news_ai", r2.fingerprint) is not None


# ---- 4. fallback records the price actually collected ------------------------------

async def test_direct_fallback_charges_what_the_gate_collected(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=2)  # 1.00 → 0.80 at the gate

    class CompoundFailsDispatcher:
        """The compound call fails; per-request fallback calls succeed."""

        def __init__(self):
            self.calls = 0

        async def dispatch(self, entry, arguments):
            self.calls += 1
            if self.calls == 1:
                raise DispatchError("compound refused")
            return ToolCallResult(text=f"answer {self.calls}")

    intake = IntakeService(engine, redis)
    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "c1")
    r2 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "c2")
    pipeline = BatchPipeline(engine, redis, CompoundFailsDispatcher())
    report = await pipeline.process(
        Batch("b1", "news_ai", [r1, r2], FireReason.BREAK_EVEN, 0), entry
    )

    assert len(report.deliveries) == 2
    # the gate collected 0.80 up-front; no rail exists to charge the 1.00 original
    assert all(d.charged == 0.8 for d in report.deliveries)
    assert report.revenue == 1.6


# ---- 5. failed requests surface immediately at the gateway -------------------------

async def test_gateway_reports_failed_request_fast(engine, redis, monkeypatch):
    await seed_asp(engine, break_even_batch_size=9)
    agg, gw = make_gateway(engine, redis, build_sim_asp_app())
    intake = IntakeService(engine, redis)
    req = await intake.accept("news_ai", "news_ai.get_news", {"query": "doomed"}, "c1")
    async with engine.begin() as conn:
        await conn.execute(
            update(request_log)
            .where(request_log.c.request_id == req.request_id)
            .values(status="failed", result="expired: no delivery within 600s")
        )

    async def fake_submit(*a, **k):
        return req

    monkeypatch.setattr(agg, "submit", fake_submit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw), base_url="http://thehouse"
    ) as client:
        import time

        t0 = time.monotonic()
        r = await _paid_call(client, "news_ai.get_news", "doomed", "DEV-PAYMENT 0xA 1000000 n9")
        elapsed = time.monotonic() - t0
    body = r.json()
    assert body["error"]["code"] == -32001
    assert "expired" in body["error"]["message"]
    assert elapsed < 2.0  # fail-fast, not the full poll window


# ---- 6. TTL expiry purges the redis queue too ---------------------------------------

async def test_expiry_removes_request_from_queue(engine, redis, monkeypatch):
    await seed_asp(engine, break_even_batch_size=9)
    intake = IntakeService(engine, redis)
    req = await intake.accept("news_ai", "news_ai.get_news", {"query": "stale"}, "c1")

    # age the request everywhere it lives: DB row and the queued copy
    monkeypatch.setattr(settings, "request_ttl_s", 1)
    async with engine.begin() as conn:
        await conn.execute(
            update(request_log)
            .where(request_log.c.request_id == req.request_id)
            .values(received_at_ms=0)
        )
    agg = AggregatorService(engine, redis, Dispatcher({}))
    (queued,) = await agg.window.queue.drain("news_ai")
    queued.received_at_ms = 0
    await agg.window.queue.push(queued)

    assert await agg.expire_stale() == 1
    # gone from the queue — a later window fire cannot resurrect it
    assert await agg.window.queue.size("news_ai") == 0
