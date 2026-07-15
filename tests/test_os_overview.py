"""OS overview: layered OS + honest network-effect telemetry aggregated from real activity."""

from core.conductor import run_job
from core.execution.executor import ExecutionResult
from core.os_overview import os_overview
from core.router.exchange import IntelligenceExchange


class DeliveringExecutor:
    via = "thehouse"
    loop = None

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        return ExecutionResult(request_id="r", status="delivered", result="ok",
                               charged=0.8, via=self.via, list_price=1.0)


def test_overview_has_layers_and_three_network_effects():
    ov = os_overview()
    assert ov["product"].startswith("LAVARD")
    assert len(ov["layers"]) >= 6
    assert set(ov["network_effects"]) == {"memory", "liquidity", "reputation"}


def test_metrics_reflect_real_activity():
    # run a job so reputation + optimizer metrics accrue
    run_job("research the market then write a brief", executor=DeliveringExecutor(), demo=True)
    ov = os_overview()
    assert ov["jobs_run"] >= 1
    assert ov["network_effects"]["reputation"]["optimizer_selections"] >= 1


def test_exchange_savings_fold_into_liquidity():
    ex = IntelligenceExchange(ttl_s=100)
    for i in range(5):
        ex.fetch("price_ai", "current BTC price", lambda: "x", cost=0.01, caller_id=f"c{i}")
    ov = os_overview(exchange=ex)
    assert ov["network_effects"]["liquidity"]["intelligence_exchange"]["calls_saved"] == 4
    assert ov["network_effects"]["liquidity"]["total_saved_usd"] >= 0.04
