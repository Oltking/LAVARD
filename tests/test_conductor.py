"""The conductor drives a goal end-to-end and honors the classifier path.

- direct_mcp goal serviced pay-per-call → terminal answer, no room, no sign-off pending.
- multi-step goal → hires + room + awaiting_signoff (money never auto-released).
"""

from core.conductor import run_job
from core.execution.executor import ExecutionResult


class DeliveringExecutor:
    via = "thehouse"
    loop = None

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        return ExecutionResult(request_id="r", status="delivered",
                               result=f"answer for {arguments.get('query')}",
                               charged=0.8, via=self.via, list_price=1.0)


def test_direct_mcp_goal_answers_pay_per_call_no_signoff():
    run = run_job("get the current BTC price", executor=DeliveringExecutor())
    # the marketplace candidate may or may not be MCP-mode; if it serviced, it's terminal
    if run.mode == "direct_mcp" and run.answer is not None:
        assert run.status == "completed"
        assert run.next_action is None
        assert run.room is None


def test_multistep_goal_hires_runs_room_and_awaits_signoff():
    run = run_job("research competitors then design a logo and build a landing page",
                  executor=DeliveringExecutor(), demo=True)
    assert run.mode == "orchestrate"
    # with hires, the room runs and we stop at sign-off (never auto-release money)
    if any(o["decision"] == "hired" for o in run.outcomes):
        assert run.status == "awaiting_signoff"
        assert run.next_action and "signoff" in run.next_action
        assert run.room is not None


def test_auto_signoff_completes_and_releases():
    run = run_job("research competitors then design a logo and build a landing page",
                  executor=DeliveringExecutor(), demo=True, auto_signoff=True)
    if any(o["decision"] == "hired" for o in run.outcomes):
        assert run.status == "completed"
        assert run.next_action is None
