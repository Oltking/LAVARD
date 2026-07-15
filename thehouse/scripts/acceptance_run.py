"""TheHouse acceptance run (spec §10) — one scripted end-to-end pass over every mechanism,
against the in-repo simulated OKX.AI targets and the dev payment rail.

Run: python -m scripts.acceptance_run
Each numbered step asserts its criterion and prints the proof. Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import fakeredis

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.models import ASPEntry, Transport
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import economics_ledger, metadata, request_log, settlements
from thehouse.core.service import AggregatorService
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.onchain.payments import DevPaymentVerifier, DevSigner, SettlementLedger, make_payment_hook
from thehouse.tests.sim_asps import LLM_SCHEMA, PRICE_SCHEMA_NATIVE
from thehouse.tests.sim_mcp_server import build_sim_asp_app

OK = "  ✓"


async def run(db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    sim = build_sim_asp_app()
    target_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim), base_url="http://sim"
    )
    ledger = SettlementLedger(engine)
    dispatcher = Dispatcher(
        {Transport.MCP: McpHttpCaller(
            client=target_client,
            payment_hook=make_payment_hook(DevSigner("0xTHEHOUSE"), ledger),
        )}
    )
    # Owner decision (QUESTIONS.md Q8): merging is exact-string only; the semantic
    # embedder stays available as an opt-in (`semantic=SemanticDedup(...)`) but is off.
    agg = AggregatorService(engine, redis, dispatcher)
    registry = RegistryService(engine)

    await registry.upsert(ASPEntry(
        asp_id="news_ai", tool_name="news_ai.get_news", mode="A_llm", transport="mcp",
        description="LLM-backed news & knowledge agent",
        endpoint="http://sim/mcp/paid_news_ai", tool_schema=LLM_SCHEMA,
        original_price_per_call=1.0, thehouse_price=0.8, break_even_batch_size=2,
    ))
    await registry.upsert(ASPEntry(
        asp_id="price_ai", tool_name="price_ai.get_price", mode="B_native", transport="mcp",
        description="Deterministic price oracle", batch_param="symbols",
        endpoint="http://sim/mcp/price_ai", tool_schema=PRICE_SCHEMA_NATIVE,
        original_price_per_call=1.0, thehouse_price=0.8, break_even_batch_size=3,
    ))
    await registry.upsert(ASPEntry(
        asp_id="pay_ai", tool_name="pay_ai.transfer", mode="non_aggregatable", transport="mcp",
        description="Transfers funds — side-effectful", endpoint="http://sim/mcp/news_ai",
        tool_schema=LLM_SCHEMA, original_price_per_call=1.0, thehouse_price=1.0,
    ))

    gateway = build_gateway_app(agg, DevPaymentVerifier(), ledger, wallet_address="0xTHEHOUSE")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://thehouse")

    async def mcp_call(tool: str, args: dict[str, Any], payer: str, units: str) -> dict[str, Any]:
        # each authorization carries a unique nonce, as real x402 payments do — the
        # gateway rejects byte-identical (replayed) authorizations
        nonce = uuid.uuid4().hex[:8]
        resp = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, headers={"PAYMENT-SIGNATURE": f"DEV-PAYMENT {payer} {units} {nonce}"})
        body = resp.json()
        assert "result" in body, f"call failed: {body}"
        return body["result"]["content"][0]["text"]

    print("STEP 1-5 — Mode A: 3 callers, exact-duplicate merge, one compound call, clean split")
    # B and C ask the byte-identical question and enter the window first (C merges into
    # B's slot); A's distinct question then fills the second slot and fires the batch.
    task_b = asyncio.create_task(
        mcp_call("news_ai.get_news", {"query": "who is the current president?"}, "0xAGENT_B", "1000000")
    )
    task_c = asyncio.create_task(
        mcp_call("news_ai.get_news", {"query": "who is the current president?"}, "0xAGENT_C", "1000000")
    )
    await asyncio.sleep(0.05)  # well inside the 300 ms window
    r1 = await mcp_call("news_ai.get_news", {"query": "current date"}, "0xAGENT_A", "1000000")
    r2, r3 = await task_b, await task_c
    assert "July 5 2026" in r1 and "Donald Trump" in r2 and r3 == r2
    news_calls = [c for c in sim.state.calls if c[0] == "news_ai"]
    assert len(news_calls) == 1, f"expected ONE target call, saw {len(news_calls)}"
    async with engine.connect() as conn:
        merged = (await conn.execute(
            select(request_log).where(request_log.c.merged_into.isnot(None)))).mappings().all()
    assert len(merged) == 1
    assert merged[0]["result"] is not None and merged[0]["charged"] == 0.8
    print(f"{OK} 3 callers, 2 slots (1 exact-string merge), 1 target call, 3 correct answers")
    print(f"{OK} compound prompt: {news_calls[0][1]['params']['arguments']['query']!r:.80}...")

    print("STEP 6 — priority caller fires the next batch immediately")
    r4 = await mcp_call("news_ai.get_news", {"query": "BTC price right now", "priority": True},
                        "0xAGENT_D", "990000")  # original 1.00 − $0.01
    assert "$107,432" in r4
    assert len([c for c in sim.state.calls if c[0] == "news_ai"]) == 2
    print(f"{OK} priority request answered without waiting for window fill (paid 0.99 = original − $0.01)")

    print("STEP 7 — Mode B: 3 symbols packed into one native multi-parameter call")
    p1, p2, p3 = await asyncio.gather(
        mcp_call("price_ai.get_price", {"symbol": "BTC"}, "0xAGENT_A", "1000000"),
        mcp_call("price_ai.get_price", {"symbol": "ETH"}, "0xAGENT_B", "1000000"),
        mcp_call("price_ai.get_price", {"symbol": "SOL"}, "0xAGENT_C", "1000000"),
    )
    assert p1 == "107432.5" and p2 == "3821.0" and p3 == "178.0"
    price_calls = [c for c in sim.state.calls if c[0] == "price_ai"]
    assert len(price_calls) == 1
    packed_args = price_calls[0][1]["params"]["arguments"]
    assert set(packed_args["symbols"]) == {"BTC", "ETH", "SOL"}
    print(f"{OK} one call paid, keyed response split to 3 callers")

    print("STEP 8 — non-aggregatable ASP routes directly, full price, no margin")
    d1 = await mcp_call("pay_ai.transfer", {"query": "current date"}, "0xAGENT_E", "1000000")
    assert d1
    async with engine.connect() as conn:
        direct = (await conn.execute(select(economics_ledger).where(
            economics_ledger.c.asp_id == "pay_ai"))).mappings().first()
    assert direct["gross_margin"] == 0.0
    print(f"{OK} DIRECT ROUTE — caller paid 1.00, TheHouse margin 0.00 (never a loss leader)")

    print("STEP 9-10 — economics ledger + onchain settlement")
    async with engine.connect() as conn:
        ledger_rows = (await conn.execute(select(economics_ledger))).mappings().all()
        setl = (await conn.execute(select(settlements))).mappings().all()
    inbound = sum(s["amount_usdt"] for s in setl if s["direction"] == "in")
    outbound = sum(s["amount_usdt"] for s in setl if s["direction"] == "out")
    total_margin = sum(r["gross_margin"] for r in ledger_rows)
    dedup_hits = sum(r["dedup_hits"] for r in ledger_rows)
    assert dedup_hits >= 1 and total_margin > 0 and inbound > outbound
    print(f"{OK} {len(ledger_rows)} batches ledgered, margin {total_margin:.2f} USDT, "
          f"{dedup_hits} dedup merge(s)")
    print(f"{OK} settlements: {inbound:.2f} in − {outbound:.2f} out → "
          f"{inbound - outbound:.2f} USDT sits in TheHouse wallet")

    print("STEP 11 — weekly economics report renders")
    econ = EconomicsEngine(engine)
    report = await econ.weekly_report()
    assert report.total_gross_margin > 0
    print("  " + report.render().replace("\n", "\n  "))

    print("\nSTEP 12 — below-break-even ASP triggers auto window extension")
    from thehouse.tests.test_economics import ledger_row  # reuse the fixture-free helper
    await registry.upsert(ASPEntry(
        asp_id="slow_ai", tool_name="slow_ai.ask", mode="A_llm",
        original_price_per_call=1.0, thehouse_price=0.8, window_timer_ms=300,
    ))
    for i in range(2):
        await ledger_row(engine, asp_id="slow_ai", batch_id=f"slow_bad{i}", batch_size=1,
                         revenue=0.8, gross_margin=-0.2, below_break_even=True)
    for i in range(3):
        await ledger_row(engine, asp_id="slow_ai", batch_id=f"slow_ok{i}")
    actions = await econ.run_auto_protection()
    slow = await registry.get("slow_ai")
    assert any(a.action == "window_extended" and a.asp_id == "slow_ai" for a in actions)
    assert slow.window_timer_ms == 450
    print(f"{OK} slow_ai window timer 300 → {slow.window_timer_ms} ms "
          f"({[a.detail for a in actions if a.asp_id == 'slow_ai'][0]})")

    await client.aclose()
    await target_client.aclose()
    await engine.dispose()
    print("\nACCEPTANCE RUN PASSED — all 12 criteria hold.")


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(f"sqlite+aiosqlite:///{tmp}/acceptance.db"))


if __name__ == "__main__":
    main()
