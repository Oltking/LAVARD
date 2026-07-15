"""A scripted Router run that proves the economics (Phase 6 demo criterion):
a near-duplicate query is served from the semantic cache, and a redundant *paid* call by a
second agent is collapsed via cross-agent dedup. Returns the decision log.
"""

from __future__ import annotations

from core.router import Router
from core.router.log import RouterLog


def run_router_demo() -> RouterLog:
    r = Router(log=RouterLog())
    paid = {"n": 0}

    def compute(answer: str):
        def _c() -> str:
            paid["n"] += 1
            return answer
        return _c

    # 1) Agent A pays for a complex analysis (cache miss -> route).
    r.ask("Analyze and compare the throughput of three rollups",
          compute("Rollup throughput: A>B>C ..."), agent_id="n1")

    # 2) Agent A asks a near-duplicate -> served from the semantic cache for free.
    r.ask("Compare the throughput of three rollups",
          compute("SHOULD NOT RUN"), agent_id="n1")

    # 3) Agent A pays for a trivial lookup.
    r.ask("What is the price of OKB", compute("OKB = $xx.x"), agent_id="n1")

    # 4) Agent B independently needs the same lookup -> cross-agent dedup collapse.
    r.ask("What is the price of OKB", compute("SHOULD NOT RUN"), agent_id="n2")

    assert paid["n"] == 2, "only the two first-of-a-kind queries should have been paid for"
    return r.log
