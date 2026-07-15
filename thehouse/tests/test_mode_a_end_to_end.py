"""Phase 6 demo criterion: 3 callers get 3 correct distinct answers from ONE call to a
real MCP target; the split log proves the attribution."""

import httpx
from sqlalchemy import select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import SplitQuality, Transport
from thehouse.core.service import AggregatorService
from thehouse.core.splitter.service import split_numbered
from thehouse.core.storage.db import economics_ledger, request_log
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def make_aggregator(engine, redis, app) -> AggregatorService:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sim-asp"
    )
    dispatcher = Dispatcher({Transport.MCP: McpHttpCaller(client=client)})
    return AggregatorService(engine, redis, dispatcher)


async def test_three_callers_one_call_three_answers(engine, redis):
    await seed_asp(
        engine,
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/news_ai",
        break_even_batch_size=3,
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    r1 = await agg.submit("news_ai", "news_ai.get_news", {"query": "current date"}, "agent_A")
    r2 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "current president name"}, "agent_B"
    )
    r3 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "BTC price right now"}, "agent_C"
    )

    # max 2 questions per compound call (owner decision): 3 slots → sub-batches of 2 + 1
    assert len(app.state.calls) == 2
    prompt = app.state.calls[0][1]["params"]["arguments"]["query"]
    assert "1) current date" in prompt and "2) current president name" in prompt
    assert app.state.calls[1][1]["params"]["arguments"]["query"] == "BTC price right now"

    # each caller got their own, correct answer
    res1 = await agg.get_result(r1.request_id)
    res2 = await agg.get_result(r2.request_id)
    res3 = await agg.get_result(r3.request_id)
    assert "July 5 2026" in res1["result"]
    assert "Donald Trump" in res2["result"]
    assert "$107,432" in res3["result"]
    assert res1["status"] == res2["status"] == res3["status"] == "delivered"

    # split log proves it: one ledger row, batch of 3, clean split, positive margin
    async with engine.connect() as conn:
        ledger = (await conn.execute(select(economics_ledger))).mappings().all()
    assert len(ledger) == 1
    row = ledger[0]
    assert row["batch_size"] == 3
    assert row["split_quality"] == "clean"
    assert row["target_cost_paid"] == 2.0  # two sub-batch calls (2 + 1)
    assert row["thehouse_revenue_collected"] == 2.4  # 3 × 0.8
    assert row["gross_margin"] == 0.4
    assert res1["batch_id"] == row["batch_id"]


async def test_timer_fire_delivers_solo_request(engine, redis):
    await seed_asp(
        engine,
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/news_ai",
        break_even_batch_size=5,
        window_timer_ms=0,  # expires immediately for the test sweeper
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    r = await agg.submit("news_ai", "news_ai.get_news", {"query": "capital of France"}, "c1")
    assert (await agg.get_result(r.request_id))["status"] == "queued"

    fired = await agg.sweep_once()
    assert fired == 1
    res = await agg.get_result(r.request_id)
    assert res["status"] == "delivered"
    assert "Paris" in res["result"]
    # solo request passes through unwrapped — the target saw the raw query
    assert app.state.calls[0][1]["params"]["arguments"]["query"] == "capital of France"


async def test_dispatch_failure_falls_back_and_logs(engine, redis):
    await seed_asp(
        engine,
        asp_id="flaky_ai",
        tool_name="flaky_ai.ask",
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/flaky_ai",
        break_even_batch_size=2,
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    await agg.submit("flaky_ai", "flaky_ai.ask", {"query": "a"}, "c1")
    await agg.submit("flaky_ai", "flaky_ai.ask", {"query": "b"}, "c2")

    async with engine.connect() as conn:
        ledger = (await conn.execute(select(economics_ledger))).mappings().all()
        rows = (await conn.execute(select(request_log))).mappings().all()
    assert len(ledger) == 1
    # compound call failed; direct fallback also failed (target always errors) —
    # requests stay queued/undelivered and the batch is recorded, not silently dropped
    assert all(r["status"] != "delivered" for r in rows)


async def test_batch_answers_are_cached_for_dedup(engine, redis):
    await seed_asp(
        engine,
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim-asp/mcp/news_ai",
        break_even_batch_size=2,
        cache_ttl_seconds=60,
    )
    app = build_sim_asp_app()
    agg = make_aggregator(engine, redis, app)

    await agg.submit("news_ai", "news_ai.get_news", {"query": "current date"}, "c1")
    await agg.submit("news_ai", "news_ai.get_news", {"query": "president"}, "c2")
    assert len(app.state.calls) == 1

    # byte-identical question after the batch → served from cache, no second target call
    r4 = await agg.submit("news_ai", "news_ai.get_news", {"query": "current date"}, "c3")
    assert r4.status.value == "cached"
    assert "July 5 2026" in r4.result
    assert len(app.state.calls) == 1


def test_split_numbered_maps_anchors_to_request_ids():
    text = "1) Sunday, July 5 2026\n2) Donald Trump\n3) BTC is at $107,432"
    outcome = split_numbered(text, ["ra", "rb", "rc"])
    assert outcome.quality == SplitQuality.CLEAN
    assert outcome.answers == {
        "ra": "Sunday, July 5 2026",
        "rb": "Donald Trump",
        "rc": "BTC is at $107,432",
    }


def test_split_partial_on_missing_anchor():
    text = "1) first answer\nand some unnumbered rambling"
    outcome = split_numbered(text, ["ra", "rb"])
    assert outcome.quality == SplitQuality.PARTIAL
    assert outcome.partial_split
    # the caller whose segment parsed gets exactly their segment — no leak
    assert outcome.answers["ra"].startswith("first answer")
    # only the caller whose segment is missing receives the full response
    assert outcome.answers["rb"] == text.strip()
    assert outcome.full_ids == {"rb"}


def test_split_failed_when_no_anchors():
    outcome = split_numbered("no numbering at all", ["ra", "rb"])
    assert outcome.quality == SplitQuality.FAILED
    # no refund rail: both callers still receive the full text (never cached) —
    # everything rather than nothing
    assert outcome.answers == {"ra": "no numbering at all", "rb": "no numbering at all"}
    assert outcome.full_ids == {"ra", "rb"}
