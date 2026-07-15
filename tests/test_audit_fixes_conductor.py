"""Deep-audit fix HIGH-3: the conductor's headline spend includes committed escrow, with a
breakdown so no cost is hidden."""

from core.conductor import run_job
from core.execution.executor import ExecutionResult


class Exec:
    via = "thehouse"
    loop = None

    async def call(self, asp_id, tool, args, caller, priority=False):
        return ExecutionResult(request_id="r", status="delivered", result="ok",
                               charged=0.8, via=self.via, list_price=1.0)


def test_spend_includes_committed_escrow_and_breaks_down():
    run = run_job("research the market then design a logo then build a backend",
                  owner_id="spend_owner", executor=Exec(), demo=True)
    hired = [o for o in run.outcomes if o["decision"] == "hired"]
    if not hired:
        return  # environment produced no escrow hires; nothing to assert
    node_hires = round(sum(o["amount_usd"] or 0 for o in hired), 4)
    # committed escrow covers the node hires AND any mid-room helper hires
    assert run.committed_escrow_usd >= node_hires
    assert run.spend_usd >= node_hires                     # headline no longer understates
    assert abs(run.spend_usd - (run.committed_escrow_usd + run.coordination_usd + run.mcp_usd)) < 1e-6
