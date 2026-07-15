import pytest

from thehouse.core.composer.service import chunk, compose
from thehouse.core.models import CallerRequest


def req(q: str, **kw) -> CallerRequest:
    return CallerRequest(
        asp_id="news_ai",
        tool_name="news_ai.get_news",
        arguments={"query": q},
        query=q,
        caller_id=kw.pop("caller_id", "c"),
        **kw,
    )


def test_compose_three_queries_numbered():
    requests = [req("current date"), req("current president name"), req("BTC price right now")]
    composed = compose(requests)

    assert composed.compound
    assert composed.prompt.startswith("Please answer the following questions")
    assert "1) current date" in composed.prompt
    assert "2) current president name" in composed.prompt
    assert "3) BTC price right now" in composed.prompt
    assert composed.order == [r.request_id for r in requests]
    # no branding, no extra instructions
    assert "TheHouse" not in composed.prompt


def test_solo_request_passes_through_unwrapped():
    r = req("what is the capital of France?")
    composed = compose([r])
    assert not composed.compound
    assert composed.prompt == "what is the capital of France?"
    assert composed.order == [r.request_id]


def test_empty_batch_rejected():
    with pytest.raises(ValueError):
        compose([])


def test_chunk_caps_batch_size():
    requests = [req(f"q{i}") for i in range(19)]
    subs = chunk(requests, 8)
    assert [len(s) for s in subs] == [8, 8, 3]
    # order preserved end-to-end
    flat = [r.request_id for s in subs for r in s]
    assert flat == [r.request_id for r in requests]
