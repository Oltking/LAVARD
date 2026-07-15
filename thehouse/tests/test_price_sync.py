"""Price re-sync: target fee changes propagate to the registry, the 402 gate, the
directory, and the OKX listing updater — automatically and with an audit trail."""

import json

from sqlalchemy import select

from thehouse.core.models import ASPEntry
from thehouse.core.pricing import caller_price
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import audit_log
from thehouse.onchain.sync import PriceSyncService, RecordingListingUpdater, StaticFeeSource
from thehouse.tests.conftest import seed_asp


async def test_fee_change_updates_both_prices(engine, redis):
    await seed_asp(engine)  # news_ai @ 1.00 → 0.80
    source = StaticFeeSource({"news_ai": 1.5})

    changes = await PriceSyncService(engine, source).sync_once()

    assert len(changes) == 1
    assert changes[0].new_fee == 1.5
    assert changes[0].new_thehouse_price == 1.2  # 1.5 × 0.80

    entry = await RegistryService(engine).get("news_ai")
    assert entry.original_price_per_call == 1.5
    assert entry.thehouse_price == 1.2
    assert caller_price(entry) == 1.2  # what the 402 gate now quotes


async def test_unchanged_and_unknown_fees_are_noops(engine, redis):
    await seed_asp(engine)
    source = StaticFeeSource({"news_ai": 1.0, "ghost_ai": 9.9})

    assert await PriceSyncService(engine, source).sync_once() == []

    entry = await RegistryService(engine).get("news_ai")
    assert entry.thehouse_price == 0.8


async def test_absent_target_is_left_untouched(engine, redis):
    await seed_asp(engine)
    # partial read from the source: no entry for news_ai at all
    assert await PriceSyncService(engine, StaticFeeSource({})).sync_once() == []


async def test_listing_updater_receives_new_fee(engine, redis):
    await seed_asp(engine)
    await seed_asp(engine, asp_id="price_ai", tool_name="price_ai.get_price")
    source = StaticFeeSource({"news_ai": 2.0, "price_ai": 1.0})
    updater = RecordingListingUpdater()

    changes = await PriceSyncService(engine, source, updater).sync_once()

    assert len(changes) == 1  # price_ai unchanged
    assert updater.pushed == [("news_ai", 1.6)]


async def test_every_change_is_audited(engine, redis):
    await seed_asp(engine)
    await PriceSyncService(engine, StaticFeeSource({"news_ai": 0.5})).sync_once()

    async with engine.connect() as conn:
        rows = (
            await conn.execute(select(audit_log).where(audit_log.c.event == "price_sync"))
        ).mappings().all()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["asp_id"] == "news_ai"
    assert payload["old_fee"] == 1.0
    assert payload["new_fee"] == 0.5
    assert payload["new_thehouse_price"] == 0.4


async def test_price_drop_flows_to_directory(engine, redis):
    from thehouse.directory.service import DirectoryService

    await seed_asp(engine)
    await PriceSyncService(engine, StaticFeeSource({"news_ai": 0.5})).sync_once()

    rows = await DirectoryService(engine).listing()
    assert rows[0]["original_price"] == 0.5
    assert rows[0]["thehouse_price"] == 0.4


async def test_sync_preserves_entry_fields(engine, redis):
    await seed_asp(engine, description="LLM news agent", max_batch_size=2, cache_ttl_seconds=99)
    await PriceSyncService(engine, StaticFeeSource({"news_ai": 3.0})).sync_once()

    entry = await RegistryService(engine).get("news_ai")
    assert isinstance(entry, ASPEntry)
    assert entry.description == "LLM news agent"
    assert entry.cache_ttl_seconds == 99
    assert entry.mode.value == "A_llm"
