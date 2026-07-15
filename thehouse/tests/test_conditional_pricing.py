"""Model B conditional pricing + solo short-circuit + the fire-time orphan race.

- A batch that never fills (solo) pays ~full (full − 0.1%): the discount is never given away.
- A batch of 2 pays the 20% discount (a real saving backs it).
- A solo aggregated fire dispatches the target EXACTLY once even if the split parser would choke
  — no mis-split re-dispatch double-charge.
- fire() re-opens the window if a request races in after the atomic drain, so it can't be orphaned.
"""

from thehouse.core.intake.service import IntakeService
from thehouse.core.models import FireReason
from thehouse.core.pipeline import BatchPipeline
from thehouse.core.profiler.profiler import ToolCallResult
from thehouse.core.window.manager import WINDOW_OPEN_KEY, Batch, WindowManager
from thehouse.tests.conftest import seed_asp


class CountingDispatcher:
    """Returns un-splittable text and counts dispatches (to prove no double-dispatch)."""

    def __init__(self):
        self.calls = 0

    async def dispatch(self, entry, arguments):
        self.calls += 1
        return ToolCallResult(text="a single blob with no numbering whatsoever")


async def test_solo_fire_pays_full_minus_token(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=9)   # never size-fires
    intake = IntakeService(engine, redis)
    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "c1")
    disp = CountingDispatcher()

    report = await BatchPipeline(engine, redis, disp).process(
        Batch("b1", "news_ai", [r1], FireReason.TIMER, 0), entry)

    assert report.tier_size == 1
    assert report.deliveries[0].charged == round(1.0 * (1 - 0.001), 6)   # 0.999, ~full
    # solo short-circuit: the target was called ONCE (no compose/split, no mis-split re-dispatch)
    assert disp.calls == 1
    assert report.target_cost_paid == 1.0


async def test_batched_fire_gets_the_discount(engine, redis):
    entry = await seed_asp(engine, break_even_batch_size=2)
    intake = IntakeService(engine, redis)
    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q1"}, "c1")
    r2 = await intake.accept("news_ai", "news_ai.get_news", {"query": "q2"}, "c2")

    class OkDispatcher:
        async def dispatch(self, entry, arguments):
            # numbered compound so the split succeeds for both
            return ToolCallResult(text="1) answer one\n2) answer two")


    report = await BatchPipeline(engine, redis, OkDispatcher()).process(
        Batch("b1", "news_ai", [r1, r2], FireReason.BREAK_EVEN, 0), entry)

    assert report.tier_size == 2
    assert all(d.charged == 0.8 for d in report.deliveries)     # 20% off — real saving
    assert report.gross_margin > 0


async def test_fire_reopens_window_when_a_request_races_in(redis):
    from thehouse.core.models import CallerRequest, now_ms

    wm = WindowManager(redis)
    r = CallerRequest(request_id="r1", asp_id="asp1", tool_name="asp1.go", arguments={"query": "x"},
                      query="x", caller_id="c1", priority=False, received_at_ms=now_ms())
    await wm.queue.push(r)
    await redis.set(WINDOW_OPEN_KEY.format(asp_id="asp1"), wm.clock())

    # simulate a concurrent push landing right after the atomic drain
    orig_drain = wm.queue.drain

    async def racing_drain(asp_id, count=None):
        drained = await orig_drain(asp_id, count)
        late = CallerRequest(request_id="r2", asp_id="asp1", tool_name="asp1.go",
                             arguments={"query": "y"}, query="y", caller_id="c2",
                             priority=False, received_at_ms=now_ms())
        await wm.queue.push(late)
        return drained

    wm.queue.drain = racing_drain
    batch = await wm.fire("asp1", FireReason.TIMER)

    assert batch is not None and len(batch.requests) == 1     # the raced-in one wasn't drained
    # …but it is NOT orphaned: the window is re-opened so the next sweep will fire it
    assert await redis.get(WINDOW_OPEN_KEY.format(asp_id="asp1")) is not None
    assert await wm.queue.size("asp1") == 1
