"""Phase 9: weekly report renders; a failing ASP triggers auto window extension."""

from sqlalchemy import insert

from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.models import now_ms
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import economics_ledger, request_log
from thehouse.tests.conftest import seed_asp


async def ledger_row(engine, asp_id="news_ai", **overrides):
    values = dict(
        batch_id=overrides.pop("batch_id"),
        asp_id=asp_id,
        batch_size=overrides.pop("batch_size", 3),
        window_open_ms=120,
        window_fire_reason="break_even",
        target_cost_paid=overrides.pop("target_cost_paid", 1.0),
        thehouse_revenue_collected=overrides.pop("revenue", 2.4),
        gross_margin=overrides.pop("gross_margin", 1.4),
        below_break_even=overrides.pop("below_break_even", False),
        dedup_hits=overrides.pop("dedup_hits", 0),
        priority_surcharges=overrides.pop("priority_surcharges", 0.0),
        split_quality=overrides.pop("split_quality", "clean"),
        created_at_ms=overrides.pop("created_at_ms", now_ms()),
    )
    async with engine.begin() as conn:
        await conn.execute(insert(economics_ledger).values(**values))


async def cached_request(engine, asp_id="news_ai", rid="req_cached_1"):
    async with engine.begin() as conn:
        await conn.execute(
            insert(request_log).values(
                request_id=rid,
                asp_id=asp_id,
                tool_name=f"{asp_id}.tool",
                arguments={},
                caller_id="c",
                priority=False,
                received_at_ms=now_ms(),
                status="cached",
                charged=0.8,
            )
        )


async def test_weekly_report_renders_all_sections(engine, redis):
    await seed_asp(engine, max_batch_size=8)
    await ledger_row(engine, batch_id="b1", dedup_hits=1, priority_surcharges=0.08)
    await ledger_row(engine, batch_id="b2", batch_size=1, revenue=0.8,
                     gross_margin=-0.2, below_break_even=True)
    await cached_request(engine)

    report = await EconomicsEngine(engine).weekly_report()
    assert report.total_gross_margin == 1.2
    assert report.priority_surcharge_revenue == 0.08
    # savings: 1 cache hit + 1 merged slot, each avoiding a $1.00 target call
    assert report.dedup_savings == 2.0
    assert report.below_break_even_by_asp == {"news_ai": 1}
    assert report.top_by_margin[0][0] == "news_ai"
    assert 0 < report.avg_batch_fill_rate <= 1

    text = report.render()
    assert "Weekly Economics Report" in text
    assert "Deduplication savings" in text
    assert "news_ai" in text


async def test_persistent_below_break_even_extends_window(engine, redis):
    await seed_asp(engine, window_timer_ms=300)
    # 2 of 5 batches below break-even (40% → >30% rule)
    for i in range(3):
        await ledger_row(engine, batch_id=f"ok{i}")
    for i in range(2):
        await ledger_row(engine, batch_id=f"bad{i}", batch_size=1, revenue=0.8,
                         gross_margin=-0.2, below_break_even=True)

    actions = await EconomicsEngine(engine).run_auto_protection()
    assert [a.action for a in actions] == ["window_extended"]

    entry = await RegistryService(engine).get("news_ai")
    assert entry.window_timer_ms == 450  # 300 × 1.5


async def test_chronic_below_break_even_demotes_to_parallel(engine, redis):
    await seed_asp(engine)
    for i in range(4):
        await ledger_row(engine, batch_id=f"bad{i}", batch_size=1, revenue=0.8,
                         gross_margin=-0.2, below_break_even=True)
    await ledger_row(engine, batch_id="ok1")

    actions = await EconomicsEngine(engine).run_auto_protection()
    assert [a.action for a in actions] == ["demoted_parallel"]
    entry = await RegistryService(engine).get("news_ai")
    assert entry.mode.value == "B_fanout"


async def test_two_failed_splits_send_mode_a_to_manual_review(engine, redis):
    await seed_asp(engine)
    await ledger_row(engine, batch_id="f1", split_quality="failed")
    await ledger_row(engine, batch_id="f2", split_quality="failed")

    actions = await EconomicsEngine(engine).run_auto_protection()
    assert [a.action for a in actions] == ["manual_review"]
    entry = await RegistryService(engine).get("news_ai")
    assert entry.mode.value == "manual_review"
