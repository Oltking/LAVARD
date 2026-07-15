"""Phase 1 demo: a request enters TheHouse, is stamped with a request_id, and appears
in its target ASP's queue.

Run: python -m scripts.demo_phase1
"""

import asyncio

from sqlalchemy import insert

from thehouse.core.intake.service import IntakeService
from thehouse.core.models import ASPEntry
from thehouse.core.storage.db import asp_registry, get_engine, init_db
from thehouse.core.storage.redis_client import get_redis
from thehouse.core.window.queue import ASPQueue


async def main() -> None:
    await init_db()
    engine, redis = get_engine(), get_redis()

    entry = ASPEntry(
        asp_id="news_ai",
        tool_name="news_ai.get_news",
        mode="A_llm",
        original_price_per_call=1.0,
        thehouse_price=0.8,
    )
    async with engine.begin() as conn:
        from sqlalchemy import delete

        await conn.execute(delete(asp_registry).where(asp_registry.c.asp_id == "news_ai"))
        await conn.execute(insert(asp_registry).values(**entry.model_dump()))

    intake = IntakeService(engine, redis)
    for caller, q in [
        ("agent_A", "current date"),
        ("agent_B", "current president name"),
        ("agent_C", "BTC price right now"),
    ]:
        req = await intake.accept(
            "news_ai", "news_ai.get_news", {"query": q}, caller_id=caller
        )
        print(f"accepted {req.request_id}  caller={caller!r}  query={q!r}")

    queue = ASPQueue(redis)
    pending = await queue.peek_all("news_ai")
    print(f"\nqueue news_ai holds {len(pending)} requests:")
    for r in pending:
        print(f"  {r.request_id}  ←  {r.caller_id}: {r.query}")


if __name__ == "__main__":
    asyncio.run(main())
