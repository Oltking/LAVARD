"""Phase 7: Mode B — native multi-parameter packing and parallel fan-out."""

import httpx
from sqlalchemy import select

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import ASPEntry, CallerRequest, Transport
from thehouse.core.packer.service import duplicate_map, pack
from thehouse.core.service import AggregatorService
from thehouse.core.splitter.service import split_keyed
from thehouse.core.storage.db import economics_ledger
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_mcp_server import build_sim_asp_app
from thehouse.tests.sim_asps import PRICE_SCHEMA_NATIVE, PRICE_SCHEMA_SINGLE


def price_req(symbol: str, caller: str) -> CallerRequest:
    return CallerRequest(
        asp_id="price_ai",
        tool_name="price_ai.get_price",
        arguments={"symbol": symbol},
        caller_id=caller,
    )


def native_entry(**kw) -> ASPEntry:
    return ASPEntry(
        asp_id="price_ai",
        tool_name="price_ai.get_price",
        mode="B_native",
        batch_param="symbols",
        tool_schema=PRICE_SCHEMA_NATIVE,
        original_price_per_call=1.0,
        thehouse_price=0.8,
        **kw,
    )


def test_pack_builds_array_and_key_map():
    reqs = [price_req("BTC", "c1"), price_req("ETH", "c2"), price_req("SOL", "c3")]
    packed = pack(native_entry(), reqs)
    assert packed.arguments == {"symbols": ["BTC", "ETH", "SOL"]}
    assert packed.key_map == {
        "BTC": reqs[0].request_id,
        "ETH": reqs[1].request_id,
        "SOL": reqs[2].request_id,
    }


def test_pack_collapses_duplicate_values():
    reqs = [price_req("BTC", "c1"), price_req("BTC", "c2")]
    packed = pack(native_entry(), reqs)
    assert packed.arguments == {"symbols": ["BTC"]}
    dupes = duplicate_map(native_entry(), reqs)
    assert dupes == {reqs[0].request_id: [reqs[1].request_id]}


def test_split_keyed_maps_values_to_request_ids():
    outcome = split_keyed(
        {"BTC": 107432, "ETH": 3821}, {"BTC": "ra", "ETH": "rb"}
    )
    assert outcome.quality.value == "clean"
    assert outcome.answers == {"ra": "107432", "rb": "3821"}


def test_split_keyed_partial_on_missing_key():
    outcome = split_keyed({"BTC": 107432}, {"BTC": "ra", "DOGE": "rb"})
    assert outcome.quality.value == "partial"
    assert outcome.answers["ra"] == "107432"
    # the missing key still paid: it gets the full payload, marked full (never cached)
    assert outcome.answers["rb"] == '{"BTC":107432}'
    assert outcome.full_ids == {"rb"}


async def test_native_batch_one_call_three_symbols(engine, redis):
    await seed_asp(
        engine,
        asp_id="price_ai",
        tool_name="price_ai.get_price",
        mode="B_native",
        batch_param="symbols",
        tool_schema=PRICE_SCHEMA_NATIVE,
        endpoint="http://sim/mcp/price_ai",
        break_even_batch_size=3,
    )
    app = build_sim_asp_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))

    r1 = await agg.submit("price_ai", "price_ai.get_price", {"symbol": "BTC"}, "c1")
    r2 = await agg.submit("price_ai", "price_ai.get_price", {"symbol": "ETH"}, "c2")
    r3 = await agg.submit("price_ai", "price_ai.get_price", {"symbol": "SOL"}, "c3")

    assert len(app.state.calls) == 1  # one call paid — the log proves it
    assert app.state.calls[0][1]["params"]["arguments"] == {"symbols": ["BTC", "ETH", "SOL"]}

    assert (await agg.get_result(r1.request_id))["result"] == "107432.5"
    assert (await agg.get_result(r2.request_id))["result"] == "3821.0"
    assert (await agg.get_result(r3.request_id))["result"] == "178.0"

    async with engine.connect() as conn:
        row = (await conn.execute(select(economics_ledger))).mappings().first()
    assert row["target_cost_paid"] == 1.0
    assert row["thehouse_revenue_collected"] == 2.4


async def test_fanout_fires_parallel_calls_and_merges(engine, redis):
    await seed_asp(
        engine,
        asp_id="fx_ai",
        tool_name="fx_ai.get_price",
        mode="B_fanout",
        tool_schema=PRICE_SCHEMA_SINGLE,
        endpoint="http://sim/mcp/price_ai",
        break_even_batch_size=2,
    )
    app = build_sim_asp_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=client)}))

    r1 = await agg.submit("fx_ai", "fx_ai.get_price", {"symbol": "BTC"}, "c1")
    r2 = await agg.submit("fx_ai", "fx_ai.get_price", {"symbol": "ETH"}, "c2")

    # fan-out: one call per request, fired in parallel, merged back to each caller
    assert len(app.state.calls) == 2
    assert "107432.5" in (await agg.get_result(r1.request_id))["result"]
    assert "3821.0" in (await agg.get_result(r2.request_id))["result"]

    async with engine.connect() as conn:
        row = (await conn.execute(select(economics_ledger))).mappings().first()
    # no aggregation margin promise on fan-out: cost 2 calls; callers pay
    # original × (1 + coordination fee) — PARALLEL ROUTE, REDUCED FEE
    assert row["target_cost_paid"] == 2.0
    assert row["thehouse_revenue_collected"] == 2.1
    assert row["gross_margin"] == 0.1
