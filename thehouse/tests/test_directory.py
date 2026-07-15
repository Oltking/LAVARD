"""Phase 10: storefront renders with correct badges, slashed prices, and live stats."""

from httpx import ASGITransport, AsyncClient

from thehouse.directory.service import DirectoryService, render_html
from thehouse.core.dispatcher.service import Dispatcher
from thehouse.core.intake.service import IntakeService
from thehouse.core.service import AggregatorService
from thehouse.tests.conftest import seed_asp


async def test_listing_badges_and_prices(engine, redis):
    await seed_asp(engine, description="LLM news agent")  # A_llm → AGGREGATED
    await seed_asp(
        engine, asp_id="fx_ai", tool_name="fx_ai.get_rate", mode="B_fanout",
        original_price_per_call=2.0, thehouse_price=1.6,
    )
    await seed_asp(
        engine, asp_id="pay_ai", tool_name="pay_ai.transfer", mode="non_aggregatable",
        original_price_per_call=5.0, thehouse_price=4.0,
    )

    rows = {r["asp_id"]: r for r in await DirectoryService(engine).listing()}

    assert rows["news_ai"]["badge"] == "AGGREGATED"
    assert rows["news_ai"]["thehouse_price"] == 0.8
    assert rows["news_ai"]["discounted"]

    assert rows["fx_ai"]["badge"] == "PARALLEL ROUTE — REDUCED FEE"
    assert rows["fx_ai"]["thehouse_price"] == 2.1  # original × 1.05, no discount promise

    assert rows["pay_ai"]["badge"] == "DIRECT ROUTE — NO DISCOUNT"
    assert rows["pay_ai"]["thehouse_price"] == 5.0  # never a loss leader


async def test_call_volume_counts_requests(engine, redis):
    await seed_asp(engine, break_even_batch_size=9)
    intake = IntakeService(engine, redis)
    for i in range(3):
        await intake.accept("news_ai", "news_ai.get_news", {"query": f"q{i}"}, f"c{i}")

    rows = await DirectoryService(engine).listing()
    assert rows[0]["call_volume"] == 3


async def test_html_storefront_renders(engine, redis):
    await seed_asp(engine, description="LLM news agent")
    from thehouse.core.api import app

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/directory")
        assert page.status_code == 200
        assert "AGGREGATED" in page.text
        assert "0.80 USDT" in page.text        # TheHouse price prominent
        assert "1.00 USDT" in page.text        # original struck through
        assert 'class="was"' in page.text

        data = (await client.get("/v1/directory")).json()
        assert data[0]["asp_id"] == "news_ai"


def test_render_html_empty():
    assert "The board is empty" in render_html([])


async def test_seal_svg_serves_without_okx(engine, redis):
    from thehouse.core.api import app

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/seal.svg")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/svg+xml")
    assert ">THEHOUSE<" in page.text            # north inscription
    assert ">AGENT SERVICE PROVIDER<" in page.text  # south inscription
    assert "OKX" not in page.text  # the seal carries only the House's own name


async def test_landing_page_serves(engine, redis):
    from thehouse.core.api import app

    app.state.engine = engine
    app.state.redis = redis
    app.state.aggregator = AggregatorService(engine, redis, Dispatcher({}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
    assert page.status_code == 200
    assert "House rules" in page.text
    assert "Enter the directory" in page.text
    assert 'href="/directory"' in page.text
