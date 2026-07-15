"""The Foreman routes Agent-to-MCP (pay-per-call) candidates through the injected executor
(TheHouse, cheaper) instead of opening an A2A escrow; A2A candidates still get the escrow hire."""

from core.execution.executor import ExecutionResult
from core.foreman import hire_for_job
from core.service import submit_goal
from core.store import get_store
from core.vetter.schemas import VetterVerdict
from onchain.schemas import AgentIdentity, AgentListing, ReputationSignals, WalletRef


def _mcp_listing() -> AgentListing:
    return AgentListing(
        agent_id="mcp_ai_1", name="Utility MCP", capability="data", mode="mcp", price_usd=1.0,
        reputation=ReputationSignals(score=90, jobs_completed=200, disputes=0, stake_okb=0.0,
                                     first_seen_days=400),
        identity=AgentIdentity(agent_id="mcp_ai_1", display_name="Utility MCP",
                               wallets=[WalletRef("xlayer", "0xabc")]))


def _high_trust(_agent_id):
    return VetterVerdict(agent_id="mcp_ai_1", trust="high", confidence=0.9, score=85,
                         evidence=[], limits=[], recommendation="ok")


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        self.calls.append((asp_id, tool_name, arguments, caller_id))
        return ExecutionResult(request_id="req1", status="delivered", result="the answer",
                               charged=0.8, via="thehouse", batch_id="b1", list_price=1.0)


def _patch(monkeypatch):
    monkeypatch.setattr("core.foreman.hire.find_candidates", lambda cap, limit=5: [_mcp_listing()])
    monkeypatch.setattr("core.foreman.hire.vet_agent", _high_trust)


def test_mcp_candidate_is_executed_through_the_executor(monkeypatch):
    _patch(monkeypatch)
    view = submit_goal("Fetch the latest onchain data")   # single specialist node
    ex = _FakeExecutor()
    outcomes = hire_for_job(view.id, executor=ex)

    serviced = [o for o in outcomes if o.decision == "serviced_mcp"]
    assert serviced, f"expected a serviced_mcp outcome, got {[o.decision for o in outcomes]}"
    o = serviced[0]
    assert o.executed_via == "thehouse"
    assert o.amount_usd == 0.8 and o.saved_usd == 0.2   # 20% cheaper than the $1.00 direct price
    assert o.result == "the answer"
    assert ex.calls and ex.calls[0][0] == "mcp_ai_1"    # the executor actually got the call

    # pay-per-call does NOT open an A2A escrow hire
    assert not [h for h in get_store().get_hires(view.id) if h["node_key"] == "n1"]
    # and it's audited as an execution, not a hire
    kinds = {a["kind"] for a in get_store().get_audit(view.id)}
    assert "mcp_executed" in kinds


def test_without_executor_mcp_candidate_falls_back_to_escrow(monkeypatch):
    _patch(monkeypatch)
    view = submit_goal("Fetch the latest onchain data")
    outcomes = hire_for_job(view.id)   # no executor → backward-compatible escrow path

    assert any(o.decision == "hired" for o in outcomes)
    assert get_store().get_hires(view.id)   # an escrow hire row exists
