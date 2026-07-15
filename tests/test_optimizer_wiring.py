"""The hire loop uses the Optimization Engine (preference honored) and feeds the reputation graph."""

from core.conductor import run_job
from core.execution.executor import ExecutionResult
from core.store import get_store


class DeliveringExecutor:
    via = "thehouse"
    loop = None

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        return ExecutionResult(request_id="r", status="delivered",
                               result="ok", charged=0.8, via=self.via, list_price=1.0)


def test_conductor_records_optimizer_decision_in_audit():
    run = run_job("get the current BTC price", executor=DeliveringExecutor(),
                  demo=True, preference="cheapest")
    audit = get_store().get_audit(run.job_id)
    kinds = [a["kind"] for a in audit]
    assert "optimizer_ranked" in kinds
    ranked = next(a for a in audit if a["kind"] == "optimizer_ranked")
    assert ranked["data"]["preference"] == "cheapest"
    assert "breakdown" in ranked["data"]


def test_delivered_hire_or_service_is_recorded_in_reputation():
    store = get_store()
    run = run_job("research the market then write a brief", executor=DeliveringExecutor(),
                  demo=True, preference="balanced")
    # at least one agent should now have an execution record from this run
    agent_ids = [o["agent_id"] for o in run.outcomes if o.get("agent_id")]
    assert any(store.get_agent_stats(aid)["samples"] > 0 for aid in agent_ids)
