from thehouse.core.intake.service import IntakeService
from thehouse.core.models import FireReason
from thehouse.core.window.manager import WindowManager
from thehouse.tests.conftest import seed_asp


class FakeClock:
    def __init__(self, start: int = 1_000_000):
        self.t = start

    def __call__(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


async def submit_through(intake, wm, entry, query, caller, priority=False):
    req = await intake.accept(
        entry.asp_id, entry.tool_name, {"query": query}, caller, priority=priority
    )
    if req.status.value != "queued":
        return req, None
    return req, await wm.submit(req, entry.break_even_batch_size)


async def test_fires_on_break_even(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=2)
    intake, wm = IntakeService(engine, redis), WindowManager(redis, FakeClock())

    _, batch1 = await submit_through(intake, wm, entry, "q one", "c1")
    assert batch1 is None  # 1 < break-even, keep collecting

    _, batch2 = await submit_through(intake, wm, entry, "q two", "c2")
    assert batch2 is not None
    assert batch2.fire_reason == FireReason.BREAK_EVEN
    assert len(batch2.requests) == 2


async def test_fires_on_timer_expiry(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=5, window_timer_ms=300)
    clock = FakeClock()
    intake, wm = IntakeService(engine, redis), WindowManager(redis, clock)

    _, batch = await submit_through(intake, wm, entry, "slow service query", "c1")
    assert batch is None

    assert await wm.check_timer(entry.asp_id, entry.window_timer_ms) is None  # not yet
    clock.advance(301)
    fired = await wm.check_timer(entry.asp_id, entry.window_timer_ms)
    assert fired is not None
    assert fired.fire_reason == FireReason.TIMER
    assert len(fired.requests) == 1
    assert fired.window_open_ms >= 300


async def test_priority_skips_window(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=5)
    intake, wm = IntakeService(engine, redis), WindowManager(redis, FakeClock())

    _, none_batch = await submit_through(intake, wm, entry, "normal query", "c1")
    assert none_batch is None

    _, fired = await submit_through(intake, wm, entry, "urgent query", "c2", priority=True)
    assert fired is not None
    assert fired.fire_reason == FireReason.PRIORITY
    assert len(fired.requests) == 2  # priority fires the whole current batch


async def test_fired_window_resets_dedup_slots(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=2)
    intake, wm = IntakeService(engine, redis), WindowManager(redis, FakeClock())

    r1, _ = await submit_through(intake, wm, entry, "same q", "c1")
    r2, batch = await submit_through(intake, wm, entry, "other q", "c2")
    assert batch is not None

    # after fire, the same question opens a fresh slot instead of merging
    r3 = await intake.accept(entry.asp_id, entry.tool_name, {"query": "same q"}, "c3")
    assert r3.status.value == "queued"


async def test_empty_window_never_fires(engine, redis):
    await seed_asp(engine)
    wm = WindowManager(redis, FakeClock())
    assert await wm.fire("news_ai", FireReason.TIMER) is None
