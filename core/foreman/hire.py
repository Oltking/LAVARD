"""Foreman hiring (§4.2, Phase 4): necessity test -> vet -> hire via A2A escrow -> in-room ID.

Flow per task node:
  1. Necessity test — hire only if the goal genuinely needs an external specialist here.
     (Portable-Memory dedup that can *also* skip a hire arrives in Phase 7.)
  2. Query candidates (marketplace) and run each through the Vetter.
  3. Pick the best non-low-trust candidate (blend of rank + trust). If every candidate is
     low-trust, DON'T auto-hire — escalate to the user (governance default: spending is always-ask).
  4. Open A2A escrow (Agent Payments Protocol) — releases only on sign-off.
  5. Assign a stable in-room ID and persist the hire.

Sign-off releases every open escrow for the job (the SETTLE step, demoed here end-to-end).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.config import get_settings
from core.foreman.market import find_candidates
from core.governance import Action, PermissionPolicy, audit, review_action
from core.memory import memory_answer_for_node
from core.reputation import rank as optimizer_rank
from core.reputation import record_delivery
from core.store import get_store
from core.vetter import vet_agent
from onchain import get_payments
from onchain.payments import OPEN, Escrow

LAVARD_PAYER = "lavard.agentic.wallet"


@dataclass
class HireOutcome:
    node_key: str
    decision: str                    # hired | serviced_mcp | skipped_* | escalated_* | no_candidates
    reason: str = ""
    agent_id: str | None = None
    agent_name: str | None = None
    in_room_id: str | None = None
    capability: str | None = None
    amount_usd: float | None = None
    trust: str | None = None
    confidence: float | None = None
    escrow_id: str | None = None
    hire_id: str | None = None
    # Agent-to-MCP pay-per-call execution (via TheHouse) — set on `serviced_mcp` outcomes.
    executed_via: str | None = None      # "thehouse" | "direct"
    saved_usd: float | None = None
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_async(coro, loop=None):
    """Run an async executor call from LAVARD's sync Foreman.

    `loop` = the aggregator's event loop (when the executor was built inside a running loop, e.g.
    the API lifespan). If given and live, the coroutine is scheduled onto THAT loop
    (`run_coroutine_threadsafe`) so it touches the loop-bound async engine/redis correctly. Only
    when there is no such loop (the pure-sync CLI path) do we spin our own with `asyncio.run`."""
    import asyncio

    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def necessity_test(node: dict) -> tuple[bool, str]:
    """Does this node genuinely require an external hire?"""
    if node["capability"] == "coordination":
        return False, "Coordination/verification handled by LAVARD itself."
    if not node["needs_hire"]:
        return False, "Marked self-serviceable during decomposition."
    return True, "Specialist capability the goal requires."


def hire_for_job(job_id: str, candidates_per_node: int = 5, executor=None,
                 preference: str = "balanced", preferred_crew: dict | None = None) -> list[HireOutcome]:
    """Hire specialists for a job's task graph.

    `executor` (an `McpExecutor`, e.g. `TheHouseExecutor`) makes Agent-to-MCP pay-per-call services
    run through TheHouse's aggregator for the batch discount instead of an A2A escrow hire. When it
    is None, MCP candidates fall back to the normal escrow path (backward-compatible default).

    `preference` (cheapest | fastest | smartest | balanced) drives the Optimization Engine's
    weighted candidate selection across cost/speed/quality/reliability/reputation."""
    store = get_store()
    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Unknown job {job_id}")
    payments = get_payments()
    owner_id = job.get("owner_id", "default-owner")
    policy = PermissionPolicy(auto_spend_ceiling_usd=get_settings().auto_spend_ceiling_usd)
    outcomes: list[HireOutcome] = []

    # IDEMPOTENCY: a node that already has an active hire must NOT be hired again — a retried or
    # duplicate call would otherwise open escrow twice for the same work (double-spend).
    already_hired = {h["node_key"] for h in store.get_hires(job_id)
                     if h["status"] in ("hired", "released")}

    for node in job["nodes"]:
        if node["key"] in already_hired:
            outcomes.append(HireOutcome(node["key"], "skipped_already_hired",
                                        "Node already has an active hire (idempotent).",
                                        capability=node["capability"]))
            continue

        needed, reason = necessity_test(node)
        if not needed:
            outcomes.append(HireOutcome(node["key"], "skipped_not_needed", reason,
                                        capability=node["capability"]))
            continue

        # REUSE (§4.5): if Portable Memory already answers this node, skip the hire entirely.
        mem_hit = memory_answer_for_node(owner_id, node)
        if mem_hit is not None:
            fact, sim = mem_hit
            audit(job_id, "hire_skipped_memory", "LAVARD",
                  f"{node['key']} answered from memory (sim {sim:.2f})",
                  {"node": node["key"], "capability": node["capability"]})
            outcomes.append(HireOutcome(
                node["key"], "skipped_memory",
                f"Answered from Portable Memory (sim {sim:.2f}, conf {fact.confidence:.2f}): "
                f"{fact.text}", capability=node["capability"]))
            continue

        cap = node["capability"]
        candidates = find_candidates(cap, limit=candidates_per_node)
        if not candidates:
            outcomes.append(HireOutcome(node["key"], "no_candidates",
                                        f"No marketplace candidates for '{cap}'.", capability=cap))
            continue

        # Vet each candidate, then let the Optimization Engine rank them under the preference
        # across cost/speed/quality/reliability/reputation (multi-dimensional, history-backed).
        verdicts = {c.agent_id: vet_agent(c.agent_id) for c in candidates}
        pairs = [(c, verdicts[c.agent_id]) for c in candidates]
        ranked = optimizer_rank(pairs, preference)

        # Blueprint fast-path: if a reused workflow blueprint names a known-good agent for this
        # capability and it's an available, non-excluded candidate, prefer it — reusing proven
        # crew instead of cold-ranking (the memory network effect). Otherwise take the optimizer's.
        top = ranked[0]
        crew_agent = (preferred_crew or {}).get(cap)
        if crew_agent:
            match = next((a for a in ranked if a.agent_id == crew_agent and not a.excluded), None)
            if match is not None:
                top = match
                audit(job_id, "crew_reused", "LAVARD",
                      f"{node['key']}: reused blueprint crew {top.name} for {cap}",
                      {"node": node["key"], "agent_id": top.agent_id, "capability": cap})
        best = top.listing
        verdict = verdicts[best.agent_id]
        audit(job_id, "optimizer_ranked", "LAVARD",
              f"{node['key']}: chose {best.name} under '{preference}' (score {top.score:.3f})",
              {"node": node["key"], "preference": preference,
               "breakdown": {k: round(v, 3) for k, v in top.breakdown.items()},
               "reputation_samples": top.reputation.samples})

        if top.excluded:
            outcomes.append(HireOutcome(
                node["key"], "escalated_low_trust",
                "All candidates are low-trust; spending requires user approval.",
                agent_id=best.agent_id, agent_name=best.name, capability=cap,
                trust=verdict.trust, confidence=verdict.confidence))
            continue

        # Action Review before spending (§4.6): route the escrow-open through governance.
        spend = Action("spend", f"Open escrow to hire {best.name} for {cap}",
                       amount_usd=best.price_usd, target=best.name, required=True)
        review_verdict = review_action(spend, policy)
        audit(job_id, "action_review", "LAVARD", review_verdict.verdict,
              {"node": node["key"], "amount_usd": best.price_usd, "tier": review_verdict.tier})
        if not review_verdict.will_execute:
            outcomes.append(HireOutcome(
                node["key"], "escalated_spend",
                f"Spend of ${best.price_usd:.2f} requires user approval "
                f"({review_verdict.rationale}).",
                agent_id=best.agent_id, agent_name=best.name, capability=cap,
                amount_usd=best.price_usd, trust=verdict.trust, confidence=verdict.confidence))
            continue

        # Agent-to-MCP (pay-per-call, §2.2): execute through TheHouse for the batch discount rather
        # than opening an A2A escrow. Falls back to the escrow-hire path when no executor is wired.
        if best.mode == "mcp" and executor is not None:
            args = {"query": node.get("description") or node["title"]}
            tool = f"{best.agent_id}.{cap}"
            try:
                res = _run_async(executor.call(best.agent_id, tool, args, f"lavard:{owner_id}"),
                                 getattr(executor, "loop", None))
            except Exception as e:
                # Graceful degradation: if TheHouse can't service this ASP (e.g. not registered),
                # fall through to the normal A2A escrow hire rather than failing the node.
                audit(job_id, "mcp_fallback", "LAVARD",
                      f"{node['key']}: MCP execution unavailable ({e}); falling back to escrow.",
                      {"node": node["key"], "agent_id": best.agent_id})
                res = None
            if res is not None and (res.result is None or res.status not in ("delivered",)):
                # The executor answered but did not actually deliver (e.g. DirectExecutor has no
                # live money/transport rail yet) — don't record a hollow serviced_mcp; fall back.
                audit(job_id, "mcp_fallback", "LAVARD",
                      f"{node['key']}: MCP execution not delivered (status={res.status}); "
                      "falling back to escrow.",
                      {"node": node["key"], "agent_id": best.agent_id, "status": res.status})
                res = None
            if res is not None:
                # Feed the reputation graph: a real delivery with its cost (a pay-per-call MCP
                # service is low-latency by nature).
                record_delivery(best.agent_id, job_id=job_id, capability=cap,
                                latency_ms=50, cost_usd=res.charged)
                audit(job_id, "mcp_executed", "LAVARD",
                      f"Serviced {node['key']} via {res.via} (charged ${res.charged:.2f}, "
                      f"saved ${res.saved:.2f})",
                      {"node": node["key"], "agent_id": best.agent_id, "via": res.via,
                       "charged": res.charged, "saved": res.saved, "request_id": res.request_id})
                outcomes.append(HireOutcome(
                    node["key"], "serviced_mcp",
                    f"Pay-per-call via {res.via}; charged ${res.charged:.2f} "
                    f"(saved ${res.saved:.2f} vs direct).",
                    agent_id=best.agent_id, agent_name=best.name, capability=cap,
                    amount_usd=res.charged, trust=verdict.trust, confidence=verdict.confidence,
                    executed_via=res.via, saved_usd=res.saved, result=res.result))
                continue

        payee = best.identity.wallets[0].address if best.identity.wallets else best.agent_id
        escrow = payments.open_escrow(
            LAVARD_PAYER, payee, best.price_usd, memo=f"{job_id}:{node['key']}:{cap}"
        )
        in_room_id = f"{node['key']}::{best.name}"
        hire_id = store.create_hire(
            job_id,
            node_key=node["key"],
            agent_id=best.agent_id,
            agent_name=best.name,
            in_room_id=in_room_id,
            capability=cap,
            amount_usd=best.price_usd,
            trust=verdict.trust,
            confidence=verdict.confidence,
            escrow_id=escrow.escrow_id,
            payee=payee,
            status="hired",
        )
        audit(job_id, "hire", "LAVARD",
              f"Hired {best.name} for {node['key']} ({cap})",
              {"agent_id": best.agent_id, "in_room_id": in_room_id, "amount_usd": best.price_usd,
               "trust": verdict.trust, "escrow_id": escrow.escrow_id})
        outcomes.append(HireOutcome(
            node["key"], "hired", "Best vetted candidate; escrow opened.",
            agent_id=best.agent_id, agent_name=best.name, in_room_id=in_room_id, capability=cap,
            amount_usd=best.price_usd, trust=verdict.trust, confidence=verdict.confidence,
            escrow_id=escrow.escrow_id, hire_id=hire_id))

    if any(o.decision == "hired" for o in outcomes):
        _mark_job(store, job_id, "hiring_complete")
    return outcomes


@dataclass
class SignOffResult:
    released: list[dict] = field(default_factory=list)
    total_released_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"released": self.released, "total_released_usd": round(self.total_released_usd, 2)}


def sign_off(job_id: str) -> SignOffResult:
    """User sign-off: release every open escrow for the job (SETTLE)."""
    store = get_store()
    payments = get_payments()
    result = SignOffResult()
    for hire in store.get_hires(job_id):
        if hire["status"] != "hired":
            continue
        # Settle to the SAME payee address the escrow was funded to (not the agent_id) so the
        # release can't misroute under a real Payments backend (audit finding MED-3).
        payee = hire.get("payee") or hire["agent_id"]
        escrow = Escrow(
            escrow_id=hire["escrow_id"], payer=LAVARD_PAYER, payee=payee,
            amount_usd=hire["amount_usd"], memo=f"{job_id}:{hire['node_key']}", status=OPEN,
        )
        released = payments.release(escrow)
        store.set_hire_status(hire["id"], "released")
        audit(job_id, "payment_released", "LAVARD",
              f"Released ${hire['amount_usd']:.2f} to {hire['agent_name']} on sign-off",
              {"escrow_id": hire["escrow_id"], "amount_usd": hire["amount_usd"]})
        from core.observability import alert

        alert("escrow_released", severity="notice", job_id=job_id, payee=payee,
              amount_usd=hire["amount_usd"], escrow_id=hire["escrow_id"])
        result.released.append({
            "in_room_id": hire["in_room_id"], "agent_id": hire["agent_id"],
            "payee": released.payee,
            "amount_usd": hire["amount_usd"], "escrow_id": released.escrow_id,
            "status": released.status,
        })
        result.total_released_usd += hire["amount_usd"]
    _mark_job(store, job_id, "signed_off")
    return result


def _mark_job(store, job_id: str, status: str) -> None:
    # lightweight status bump reusing the sqlite store connection
    with store._connect() as c:  # noqa: SLF001 - internal helper on our own store
        c.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                  (status, _now_iso(), job_id))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
