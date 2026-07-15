"""LAVARD integrated demo — the money + memory story judges remember.

Run:  python demo.py

Shows, end to end and offline:
  1. Intelligence Exchange — a burst of identical public lookups collapses to ONE upstream call.
  2. Optimization Engine   — the same candidates, ranked differently under cheapest vs smartest.
  3. Memory Network        — a second identical job reuses the blueprint crew (planning/search skipped).
  4. Live Crew + OS view   — network-effect telemetry after the run.

Uses throwaway DBs so it never touches your real stores.
"""

from __future__ import annotations

import json
import os
import tempfile


def _isolate() -> None:
    d = tempfile.mkdtemp(prefix="lavard_demo_")
    os.environ["LAVARD_DATABASE_URL"] = f"sqlite:///{d}/demo.db"
    os.environ["LAVARD_MEMORY_URL"] = f"sqlite:///{d}/demo_mem.db"
    os.environ.pop("LAVARD_MODEL_ENDPOINT", None)


class _DeliveringExecutor:
    via = "thehouse"
    loop = None

    async def call(self, asp_id, tool_name, arguments, caller_id, priority=False):
        from core.execution.executor import ExecutionResult
        return ExecutionResult(request_id="r", status="delivered", result="deliverable",
                               charged=0.8, via=self.via, list_price=1.0)


def run_demo() -> dict:
    from core.conductor import run_job
    from core.foreman.market import find_candidates
    from core.memory import distill_job
    from core.os_overview import os_overview
    from core.reputation import rank
    from core.router.exchange import IntelligenceExchange
    from core.store import get_store
    from core.vetter import vet_agent

    results: dict = {}

    # 1. Intelligence Exchange — 50 callers, one upstream call.
    ex = IntelligenceExchange(ttl_s=100)
    upstream = {"n": 0}

    def compute():
        upstream["n"] += 1
        return "BTC = $65,000"

    for i in range(50):
        ex.fetch("price_ai", "current BTC price", compute, cost=0.01, caller_id=f"caller{i}")
    results["intelligence_exchange"] = {"callers": 50, "upstream_calls": upstream["n"],
                                        **ex.stats.to_dict()}

    # 2. Optimization Engine — cheapest vs smartest over the same candidates.
    cands = find_candidates("engineering", limit=5)
    pairs = [(c, vet_agent(c.agent_id)) for c in cands]
    cheap = rank(pairs, "cheapest")
    smart = rank(pairs, "smartest")
    results["preference"] = {
        "cheapest_pick": cheap[0].name if cheap else None,
        "smartest_pick": smart[0].name if smart else None,
        "differs": bool(cheap and smart and cheap[0].agent_id != smart[0].agent_id),
    }

    # 3. Memory Network — second identical job reuses the blueprint crew. Job #1 runs as escrow
    # hires (recorded), so the blueprint captures a real crew that job #2 can pre-select.
    owner = "demo_owner"
    goal = "research competitors then design a logo and build a landing page"
    r1 = run_job(goal, owner_id=owner, demo=True)
    hires_job1 = sum(1 for o in r1.outcomes if o["decision"] == "hired")
    distill_job(r1.job_id)                       # capture the blueprint from job #1
    r2 = run_job(goal, owner_id=owner, demo=True)
    audit2 = get_store().get_audit(r2.job_id)
    skipped = sum(1 for a in audit2 if a["kind"] == "hire_skipped_memory")
    crew_reused = sum(1 for a in audit2 if a["kind"] == "crew_reused")
    results["memory"] = {
        "first_job": r1.job_id, "second_job": r2.job_id,
        "hires_job1": hires_job1,
        "nodes_skipped_from_memory_job2": skipped,
        "crew_reused_job2": crew_reused,
        "spend_job1": r1.spend_usd, "spend_job2": r2.spend_usd,
    }

    # 4. OS overview — the three network effects, live.
    results["os"] = os_overview(exchange=ex)
    return results


def main() -> int:
    _isolate()
    r = run_demo()
    ie = r["intelligence_exchange"]
    print("\n=== 1. Intelligence Exchange (AI CDN) ===")
    print(f"  {ie['callers']} callers asked 'current BTC price' → {ie['upstream_calls']} upstream call, "
          f"{ie['shared_hits']} shared, ${ie['cost_saved']:.2f} saved")

    pref = r["preference"]
    print("\n=== 2. Optimization Engine (preference) ===")
    print(f"  cheapest → {pref['cheapest_pick']}   |   smartest → {pref['smartest_pick']}   "
          f"(different agent: {pref['differs']})")

    mem = r["memory"]
    print("\n=== 3. Memory Network (before / after) ===")
    print(f"  job #1 {mem['first_job'][:8]} ran cold ({mem['hires_job1']} hires, "
          f"${mem['spend_job1']:.2f})")
    print(f"  job #2 {mem['second_job'][:8]} reused memory: {mem['nodes_skipped_from_memory_job2']} "
          f"node(s) skipped (no hire), {mem['crew_reused_job2']} crew reused, "
          f"${mem['spend_job2']:.2f} spend")

    net = r["os"]["network_effects"]
    print("\n=== 4. Network effects (live) ===")
    print(f"  memory:     {net['memory']['blueprints']} blueprints, "
          f"{net['memory']['reuse_events']} reuse events")
    print(f"  liquidity:  ${net['liquidity']['total_saved_usd']:.2f} saved "
          f"(TheHouse + Router + Exchange)")
    print(f"  reputation: {net['reputation']['agents_scored']} agents scored over "
          f"{net['reputation']['executions_recorded']} executions")
    print("\n(full JSON below)\n")
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
