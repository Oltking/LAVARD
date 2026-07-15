"""DirectExecutor must never be a raising grenade: with no live money rail it degrades to a
graceful `unavailable` result, and get_executor never hands back something that throws on use."""

import asyncio

from core.execution.executor import DirectExecutor, ExecutionResult, get_executor


def test_direct_executor_degrades_without_payer():
    ex = DirectExecutor(payer=None)
    res = asyncio.run(ex.call("asp1", "asp1.research", {"query": "x"}, "caller"))
    assert isinstance(res, ExecutionResult)
    assert res.status == "unavailable"
    assert res.result is None
    assert res.charged == 0.0
    assert res.saved == 0.0  # no fabricated savings


def test_get_executor_returns_usable_executor_without_aggregator():
    # No aggregator wired → DirectExecutor, and calling it does not raise
    ex = get_executor(aggregator=None)
    assert isinstance(ex, DirectExecutor)
    res = asyncio.run(ex.call("asp1", "asp1.research", {"query": "x"}, "caller"))
    assert res.status == "unavailable"  # graceful, not an exception
