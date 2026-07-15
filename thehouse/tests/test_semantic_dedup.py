"""Phase 8: two semantically identical queries in one window → merged to one slot,
both served the same answer from one call."""

import httpx
from thehouse.core.deduplicator.semantic import SemanticDedup, TokenHashEmbedder, cosine
from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import Transport
from thehouse.core.service import AggregatorService
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


def test_embedder_scores_paraphrases_high_and_unrelated_low():
    emb = TokenHashEmbedder()
    a = emb.embed("who is the US president?")
    b = emb.embed("US president — who?")
    c = emb.embed("BTC price right now")
    assert cosine(a, b) > 0.9
    assert cosine(a, c) < 0.5


async def test_semantic_check_merges_and_releases():
    sem = SemanticDedup(merge_threshold=0.9, overlap_threshold=0.5)
    novel = await sem.check("news_ai", "r1", "who is the US president?")
    assert novel.owner_request_id is None

    dup = await sem.check("news_ai", "r2", "US president — who?")
    assert dup.owner_request_id == "r1"

    await sem.release("news_ai", ["r1"])
    fresh = await sem.check("news_ai", "r3", "who is the US president?")
    assert fresh.owner_request_id is None


async def test_moderate_overlap_flags_but_keeps_slot():
    sem = SemanticDedup(merge_threshold=0.99, overlap_threshold=0.30)
    await sem.check("news_ai", "r1", "is BTC up today?")
    verdict = await sem.check("news_ai", "r2", "BTC price today")
    assert verdict.owner_request_id is None
    assert verdict.overlap_with == "r1"


async def test_semantic_merge_end_to_end(engine, redis):
    await seed_asp(
        engine,
        mode="A_llm",
        tool_schema=LLM_SCHEMA,
        endpoint="http://sim/mcp/news_ai",
        break_even_batch_size=2,
    )
    app = build_sim_asp_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    agg = AggregatorService(
        engine,
        redis,
        Dispatcher({Transport.MCP: McpHttpCaller(client=client)}),
        semantic=SemanticDedup(merge_threshold=0.9, overlap_threshold=0.5),
    )

    r1 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "who is the current president?"}, "c1"
    )
    r2 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "the current president — who?"}, "c2"
    )
    assert r2.status.value == "merged"
    assert r2.merged_into == r1.request_id

    # window still holds ONE slot; a third caller fires it at break-even 2
    r3 = await agg.submit(
        "news_ai", "news_ai.get_news", {"query": "BTC price right now"}, "c3"
    )

    assert len(app.state.calls) == 1  # one call for three callers
    res1 = await agg.get_result(r1.request_id)
    res2 = await agg.get_result(r2.request_id)
    res3 = await agg.get_result(r3.request_id)
    assert "Donald Trump" in res1["result"]
    assert res2["result"] == res1["result"]  # merged caller: same answer, also charged
    assert res2["charged"] == res1["charged"] == 0.8
    assert "$107,432" in res3["result"]
