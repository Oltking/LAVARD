"""The blend: LAVARD routes its paid Agent-to-MCP calls through TheHouse and each call comes back
~20% cheaper via batching. Drives TheHouse's proven Mode-A sim machinery through LAVARD's
`core.execution.TheHouseExecutor` — proving the two products work as one.
"""

import asyncio

import httpx

from core.execution import TheHouseExecutor, get_executor
from core.execution.executor import DirectExecutor
from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import Transport
from thehouse.core.service import AggregatorService
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def _executor(engine, redis):
    app = build_sim_asp_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim-asp")
    agg = AggregatorService(engine, redis,
                            Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))
    return TheHouseExecutor(agg)


async def test_lavard_paid_calls_are_cheaper_through_thehouse(engine, redis):
    await seed_asp(engine, mode="A_llm", tool_schema=LLM_SCHEMA,
                   endpoint="http://sim-asp/mcp/news_ai", break_even_batch_size=3)
    executor = _executor(engine, redis)

    # Three different LAVARD jobs each hire the same Agent-to-MCP ASP, concurrently.
    results = await asyncio.gather(
        executor.call("news_ai", "news_ai.get_news", {"query": "current date"}, "lavard_job_1"),
        executor.call("news_ai", "news_ai.get_news", {"query": "current president name"}, "lavard_job_2"),
        executor.call("news_ai", "news_ai.get_news", {"query": "BTC price right now"}, "lavard_job_3"),
    )

    for r in results:
        assert r.via == "thehouse"
        assert r.status == "delivered"
        assert r.result
        assert r.list_price == 1.0        # direct price to the target ASP
        assert r.charged == 0.8           # through TheHouse
        assert r.saved == 0.2             # 20% cheaper per call

    # distinct callers got distinct, correct answers (not a shared blob)
    answers = " ".join(r.result for r in results)
    assert "July 5 2026" in answers and "Donald Trump" in answers and "$107,432" in answers


async def test_get_executor_falls_back_to_direct_when_no_aggregator():
    # With TheHouse enabled but no aggregator wired, we fall back to the direct (full-price) path.
    ex = get_executor(aggregator=None)
    assert isinstance(ex, DirectExecutor)
