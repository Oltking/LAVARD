"""Live Crew Optimization: when a hired agent fails to deliver mid-room, the controller retires it,
hires a replacement for the same capability, and continues — invisibly to the user."""

from core.execution.executor import ExecutionResult
from core.room import run_room
from core.service import submit_goal
from core.store import get_store


class FailFirstAgentExecutor:
    """Fails (unavailable) for the first distinct agent_id it sees; delivers for any other —
    modelling a specific hired agent going bad while replacements work."""
    via = "thehouse"
    loop = None

    def __init__(self):
        self.first: str | None = None

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        if self.first is None:
            self.first = asp_id
        if asp_id == self.first:
            # a genuine MCP failure (not merely 'unavailable') is what triggers live-crew replacement
            return ExecutionResult(request_id="", status="failed", result=None,
                                   charged=0.0, via=self.via)
        return ExecutionResult(request_id="r", status="delivered", result="replacement delivered",
                               charged=0.8, via=self.via, list_price=1.0)


def test_failed_agent_is_replaced_and_node_completes():
    view = submit_goal("build a backend service", "crew_owner")
    store = get_store()
    # one real hire whose agent will fail through the executor
    store.create_hire(view.id, node_key="n1", agent_id="bad_agent", agent_name="BadCo",
                      in_room_id="n1::BadCo", capability="engineering", amount_usd=10.0,
                      trust="high", confidence=0.9, escrow_id="e", payee="p", status="hired")

    transcript = run_room(view.id, demo=False, executor=FailFirstAgentExecutor())
    methods = [t.to_dict().get("method", "") for t in transcript.turns]
    assert "crew_replaced" in methods                 # a replacement was hired mid-room
    # the node ultimately completes (not left unresolved)
    assert any(nc == "n1" for nc in transcript.nodes_completed)
    # a replacement hire is recorded
    assert any(h.get("replacement") for h in transcript.hired_in_room)
