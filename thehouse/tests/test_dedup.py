from thehouse.core.deduplicator.service import DedupService, fingerprint
from thehouse.core.intake.service import IntakeService
from thehouse.core.models import RequestStatus
from thehouse.tests.conftest import seed_asp


def test_fingerprint_is_strict_string_equality():
    a = fingerprint("t", {"query": "who is the US president?"})
    b = fingerprint("t", {"query": "  who is the US president?  "})  # outer trim only
    c = fingerprint("t", {"query": "who is the us president?"})      # case differs → distinct
    d = fingerprint("t", {"query": "BTC price"})
    assert a == b
    assert a != c
    assert a != d
    # key order must not matter
    assert fingerprint("t", {"a": 1, "b": 2}) == fingerprint("t", {"b": 2, "a": 1})


async def test_cache_hit_serves_immediately(engine, redis):
    await seed_asp(engine)
    intake = IntakeService(engine, redis)
    dedup = DedupService(redis)

    fp = fingerprint("news_ai.get_news", {"query": "current president name"})
    await dedup.cache_result("news_ai", fp, "Donald Trump", ttl_seconds=30)

    req = await intake.accept(
        "news_ai", "news_ai.get_news", {"query": "current president name"}, "caller_1"
    )
    assert req.status == RequestStatus.CACHED
    assert req.result == "Donald Trump"
    assert await intake.queue.size("news_ai") == 0  # never enters the window


async def test_cache_expires_with_ttl(engine, redis):
    await seed_asp(engine)
    dedup = DedupService(redis)
    fp = fingerprint("news_ai.get_news", {"query": "q"})
    await dedup.cache_result("news_ai", fp, "answer", ttl_seconds=30)
    assert await dedup.get_cached("news_ai", fp) == "answer"
    # emulate TTL expiry
    await redis.delete(f"cache:news_ai:{fp}")
    assert await dedup.get_cached("news_ai", fp) is None


async def test_identical_pending_requests_merge_to_one_slot(engine, redis):
    await seed_asp(engine)
    intake = IntakeService(engine, redis)

    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "BTC price"}, "c1")
    r2 = await intake.accept("news_ai", "news_ai.get_news", {"query": " BTC price "}, "c2")

    assert r1.status == RequestStatus.QUEUED
    assert r2.status == RequestStatus.MERGED
    assert r2.merged_into == r1.request_id
    assert await intake.queue.size("news_ai") == 1  # one slot consumed

    members = await intake.dedup.merged_members("news_ai", r1.request_id)
    assert members == [r2.request_id]


async def test_release_window_allows_new_slot(engine, redis):
    await seed_asp(engine)
    intake = IntakeService(engine, redis)

    r1 = await intake.accept("news_ai", "news_ai.get_news", {"query": "x"}, "c1")
    await intake.dedup.release_window("news_ai", [r1.fingerprint])

    r3 = await intake.accept("news_ai", "news_ai.get_news", {"query": "x"}, "c3")
    assert r3.status == RequestStatus.QUEUED  # new window, new slot
