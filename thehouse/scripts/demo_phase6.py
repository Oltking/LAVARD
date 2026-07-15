"""Phase 6 demo: three callers, ONE MCP call to the target, three correct answers, and a
split log proving the attribution.

Run: python -m scripts.demo_phase6
(uses the in-repo simulated MCP target; swap the endpoint to a live ASP to go real)
"""

import asyncio

import httpx
from sqlalchemy import select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import ASPEntry, Transport
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.service import AggregatorService
from thehouse.core.storage.db import economics_ledger, get_engine, init_db
from thehouse.core.storage.redis_client import get_redis
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


async def main() -> None:
    await init_db()
    engine, redis = get_engine(), get_redis()

    app = build_sim_asp_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))

    await RegistryService(engine).upsert(
        ASPEntry(
            asp_id="news_ai",
            tool_name="news_ai.get_news",
            mode="A_llm",
            transport="mcp",
            endpoint="http://sim/mcp/news_ai",
            tool_schema=LLM_SCHEMA,
            original_price_per_call=1.0,
            thehouse_price=0.8,
            break_even_batch_size=2,
        )
    )

    callers = [
        ("agent_A", "current date"),
        ("agent_B", "current president name"),
        ("agent_C", "current president name"),  # exact duplicate → merges into agent_B's slot
    ]
    reqs = [
        await agg.submit("news_ai", "news_ai.get_news", {"query": q}, c) for c, q in callers
    ]

    print(f"target received {len(app.state.calls)} call(s)\n")
    print("compound prompt sent to target:")
    print("  " + app.state.calls[0][1]["params"]["arguments"]["query"].replace("\n", "\n  "))
    print("\ndeliveries:")
    for (caller, q), r in zip(callers, reqs):
        res = await agg.get_result(r.request_id)
        print(f"  {caller}  {q!r}\n    → {res['result']!r}  (charged {res['charged']})")

    async with engine.connect() as conn:
        row = (await conn.execute(select(economics_ledger))).mappings().first()
    print(
        f"\nledger: batch={row['batch_id']} size={row['batch_size']} "
        f"cost={row['target_cost_paid']} revenue={row['thehouse_revenue_collected']} "
        f"margin={row['gross_margin']} split={row['split_quality']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
