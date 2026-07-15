"""Non-demo Room delivers via a real executor (ExecutorRoomAgent), not a scripted stub.

A fake executor returns a genuine answer; the room's node result must be that answer. When the
executor can't deliver, the room completes the node with an honest 'pending' note, never a fake.
"""

from core.execution.executor import ExecutionResult
from core.room.agents import ExecutorRoomAgent
from core.room.models import RESULT


class DeliveringExecutor:
    via = "thehouse"
    loop = None

    def __init__(self, answer="REAL research deliverable", status="delivered"):
        self.answer = answer
        self.status = status
        self.calls = []

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        self.calls.append((asp_id, tool_name, arguments, caller_id))
        return ExecutionResult(request_id="r1", status=self.status,
                               result=self.answer if self.status == "delivered" else None,
                               charged=0.8, via=self.via, list_price=1.0)


def _hire():
    return {"in_room_id": "n1::AlphaResearch", "agent_id": "alpha", "capability": "research"}


def test_executor_room_agent_returns_real_deliverable():
    ex = DeliveringExecutor()
    agent = ExecutorRoomAgent(_hire(), ex, owner_id="owner1")
    ev = agent.step()
    assert ev.kind == RESULT
    assert ev.text == "REAL research deliverable"
    assert agent.charged_usd == 0.8
    assert ex.calls and ex.calls[0][0] == "alpha"  # the real agent was actually invoked


def test_unavailable_is_treated_as_a2a_escrow_not_failure():
    # 'unavailable' = this hire isn't MCP-serviceable (A2A escrow) → delivered via sign-off,
    # NOT a failure, so the controller won't retire/replace it (audit LOW-8).
    ex = DeliveringExecutor(status="unavailable")
    agent = ExecutorRoomAgent(_hire(), ex, owner_id="owner1")
    ev = agent.step()
    assert ev.kind == RESULT
    assert "a2a escrow" in ev.text.lower()
    assert agent.delivered_ok is True          # legitimate hire, not replaced
    assert agent.charged_usd == 0.0            # no MCP charge (escrow handled separately)


def test_failed_mcp_is_not_fabricated_and_flags_for_replacement():
    ex = DeliveringExecutor(status="failed")
    agent = ExecutorRoomAgent(_hire(), ex, owner_id="owner1")
    ev = agent.step()
    assert ev.kind == RESULT
    assert "fail" in ev.text.lower()
    assert agent.delivered_ok is False         # genuine failure → controller may replace
    assert agent.charged_usd == 0.0
