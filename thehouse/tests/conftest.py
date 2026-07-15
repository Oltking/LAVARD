import os
import tempfile

os.environ.setdefault("THEHOUSE_PROFILE", "dev")
os.environ.setdefault(
    "THEHOUSE_DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/thehouse-test.db"
)

import fakeredis
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from thehouse.core.models import ASPEntry
from thehouse.core.storage.db import asp_registry, metadata


@pytest.fixture
async def engine(tmp_path):
    # A file-backed DB (not a shared-connection :memory: StaticPool) so concurrent tasks
    # get real per-checkout connections, as in the dev/prod profiles.
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/thehouse.db")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def redis():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


async def seed_asp(engine, **overrides) -> ASPEntry:
    entry = ASPEntry(
        asp_id=overrides.pop("asp_id", "news_ai"),
        tool_name=overrides.pop("tool_name", "news_ai.get_news"),
        mode=overrides.pop("mode", "A_llm"),
        original_price_per_call=overrides.pop("original_price_per_call", 1.0),
        thehouse_price=overrides.pop("thehouse_price", 0.8),
        **overrides,
    )
    async with engine.begin() as conn:
        await conn.execute(insert(asp_registry).values(**entry.model_dump()))
    return entry
