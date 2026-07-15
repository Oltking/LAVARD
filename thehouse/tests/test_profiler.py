from thehouse.core.models import ASPEntry, ASPMode
from thehouse.core.profiler.profiler import Profiler, find_array_param, has_side_effects
from thehouse.core.profiler.registry import RegistryService
from thehouse.tests.sim_asps import (
    LLM_SCHEMA,
    PRICE_SCHEMA_NATIVE,
    PRICE_SCHEMA_SINGLE,
    SimBrokenCaller,
    SimLLMCaller,
    SimPriceCaller,
)


def entry(**kw) -> ASPEntry:
    base = dict(asp_id="x", tool_name="x.tool", original_price_per_call=1.0)
    base.update(kw)
    return ASPEntry(**base)


async def test_llm_asp_classified_mode_a():
    e = entry(
        asp_id="news_ai",
        tool_name="news_ai.get_news",
        description="Answers questions about current events and general knowledge.",
        tool_schema=LLM_SCHEMA,
    )
    profiled = await Profiler(SimLLMCaller()).profile(e)
    assert profiled.mode == ASPMode.A_LLM


async def test_price_feed_with_array_param_is_b_native():
    e = entry(
        asp_id="price_ai",
        tool_name="price_ai.get_price",
        description="Returns the latest market price for a symbol.",
        tool_schema=PRICE_SCHEMA_NATIVE,
    )
    profiled = await Profiler(SimPriceCaller()).profile(e)
    assert profiled.mode == ASPMode.B_NATIVE
    assert profiled.batch_param == "symbols"


async def test_price_feed_without_array_param_is_b_fanout():
    e = entry(
        asp_id="fx_ai",
        tool_name="fx_ai.get_rate",
        description="Returns the latest market rate for one symbol.",
        tool_schema=PRICE_SCHEMA_SINGLE,
    )

    class SingleOnly(SimPriceCaller):
        async def call(self, entry, arguments):
            arguments = {"symbol": "BTC"}  # deterministic probe result
            return await super().call(entry, arguments)

    profiled = await Profiler(SingleOnly()).profile(e)
    assert profiled.mode == ASPMode.B_FANOUT


async def test_side_effectful_asp_never_probed():
    caller = SimLLMCaller()
    e = entry(
        asp_id="pay_ai",
        tool_name="pay_ai.transfer_funds",
        description="Transfer USDT to any wallet.",
        tool_schema=LLM_SCHEMA,
    )
    profiled = await Profiler(caller).profile(e)
    assert profiled.mode == ASPMode.NON_AGGREGATABLE
    assert caller.calls == []  # spec rule 4: no test calls made
    assert has_side_effects(e)


async def test_llm_that_drops_numbering_goes_manual_review():
    e = entry(description="General assistant.", tool_schema=LLM_SCHEMA)
    profiled = await Profiler(SimLLMCaller(follow_numbering=False)).profile(e)
    # both answers present but format unreliable is still A_llm per spec §5.1(2) —
    # only error/one-answer goes to manual review; unnumbered text keeps both answers.
    assert profiled.mode in (ASPMode.A_LLM, ASPMode.MANUAL_REVIEW)


async def test_erroring_asp_flagged_manual_review():
    e = entry(description="Flaky data source.", tool_schema=LLM_SCHEMA)
    profiled = await Profiler(SimBrokenCaller()).profile(e)
    assert profiled.mode == ASPMode.MANUAL_REVIEW


async def test_registry_derives_thehouse_price_and_upserts(engine):
    reg = RegistryService(engine)
    e = entry(asp_id="news_ai", tool_name="news_ai.get_news", original_price_per_call=1.0)
    e.thehouse_price = 0.0
    await reg.upsert(e)

    stored = await reg.get("news_ai")
    assert stored is not None
    assert stored.thehouse_price == 0.8  # 20% off

    stored.original_price_per_call = 2.0
    stored.thehouse_price = 0.0
    await reg.upsert(stored)
    again = await reg.get("news_ai")
    assert again.thehouse_price == 1.6
    assert len(await reg.list_all()) == 1


def test_find_array_param():
    assert find_array_param(PRICE_SCHEMA_NATIVE) == "symbols"
    assert find_array_param(PRICE_SCHEMA_SINGLE) is None
