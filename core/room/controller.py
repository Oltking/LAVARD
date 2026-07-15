"""The controller-mediated Room + controller-as-first-responder loop (§4.3, Phase 5).

Every agent turn passes through the Referee (turn/budget/kill-switch/loop checks). When an agent
asks a question or stalls, the controller is the FIRST responder:
  1. answer from Portable Memory / the blackboard   (ANSWERED_FROM_MEMORY)
  2. else poll the room for a peer who knows          (POLLED_ROOM)
  3. else find/hire a new specialist into the room    (HIRED_NEW)
All traffic is controller-mediated (default transport); nothing bypasses the meter.
"""

from __future__ import annotations

from core.foreman.market import find_candidates, rank_score
from core.room.agents import DEMO_MEMORY_SEED, MockRoomAgent, build_demo_scripts
from core.room.blackboard import Blackboard
from core.room.knowledge import MemoryBackedKnowledge, PortableMemory
from core.router.router import Router, classify_step
from core.room.models import (
    ANSWERED_FROM_MEMORY,
    HIRED_NEW,
    POLLED_ROOM,
    QUESTION,
    RESULT,
    STALL,
    UNRESOLVED,
    Event,
    RoomTranscript,
    TurnLog,
    result,
)
from core.room.referee import Referee, RefereeStop
from core.store import get_store
from core.vetter import vet_agent
from onchain import get_payments
from onchain.payments import Escrow

# Room model-spend now flows through the shared Router (tiered cost + cache/dedup), so there are
# no flat per-turn constants — the meter charges the true routed/cached price (audit finding HIGH-1).
LAVARD_PAYER = "lavard.agentic.wallet"
_TRUST_BONUS = {"high": 20.0, "medium": 0.0, "low": -1000.0}


class FirstResponder:
    def __init__(self, job_id: str, memory, blackboard: Blackboard,
                 participants: dict[str, MockRoomAgent], transcript: RoomTranscript,
                 router: Router | None = None, referee=None, executor=None,
                 preference: str = "balanced", owner_id: str = "default-owner") -> None:
        self.job_id = job_id
        self.memory = memory
        self.blackboard = blackboard
        self.participants = participants
        self.transcript = transcript
        self.router = router or Router()
        self.referee = referee
        self.executor = executor
        self.preference = preference
        self.owner_id = owner_id

    def resolve(self, asker_id: str, ev: Event) -> tuple[str, str, float]:
        # 1) LAVARD answers from Portable Memory or the shared blackboard (free).
        hit = self.memory.lookup(ev.topic)
        if hit:
            return hit, ANSWERED_FROM_MEMORY, 0.0
        fact = self.blackboard.find(ev.topic)
        if fact:
            return f"(from blackboard) {fact.text}", ANSWERED_FROM_MEMORY, 0.0

        # 2) Poll the room for a peer who knows. Route the brokering through the Router so a
        #    repeated/duplicate poll is served from cache instead of paying again (HIGH-1).
        for pid, p in self.participants.items():
            if pid == asker_id:
                continue
            ans = p.knows(ev.topic)
            if ans:
                answer, cost = self.router.ask_costed(
                    f"poll:{ev.topic}", lambda a=ans: a, tier="routine", agent_id=asker_id)
                return answer, POLLED_ROOM, cost

        # 3) Find/hire a new specialist into the room.
        cap = ev.needed_capability or "general"
        answer, cost = self._hire_specialist(cap, ev.topic)
        if answer is not None:
            return answer, HIRED_NEW, cost

        return "No resolution found; escalating to user.", UNRESOLVED, 0.0

    def _hire_specialist(self, capability: str, topic: str) -> tuple[str | None, float]:
        candidates = find_candidates(capability, limit=3)
        scored = []
        for c in candidates:
            v = vet_agent(c.agent_id)
            scored.append((rank_score(c) + _TRUST_BONUS[v.trust], c, v))
        scored.sort(key=lambda t: t[0], reverse=True)
        if not scored or scored[0][2].trust == "low":
            return None, 0.0
        _, best, verdict = scored[0]
        # MED-1: refuse the hire BEFORE opening escrow if it would breach the budget ceiling.
        if self.referee is not None and not self.referee.affordable(best.price_usd):
            return None, 0.0
        payee = best.identity.wallets[0].address if best.identity.wallets else best.agent_id
        escrow = get_payments().open_escrow(LAVARD_PAYER, payee, best.price_usd,
                                            memo=f"{self.job_id}:room-hire:{capability}")
        in_room_id = f"helper::{best.name}"
        agent = MockRoomAgent(in_room_id, capability, [result(f"{in_room_id} resolved '{topic}'.")],
                              expertise={capability, topic})
        self.participants[in_room_id] = agent
        get_store().create_hire(
            self.job_id, node_key=f"room-helper:{capability}", agent_id=best.agent_id,
            agent_name=best.name, in_room_id=in_room_id, capability=capability,
            amount_usd=best.price_usd, trust=verdict.trust, confidence=verdict.confidence,
            escrow_id=escrow.escrow_id, payee=payee, status="hired")
        self.transcript.hired_in_room.append(
            {"in_room_id": in_room_id, "agent": best.name, "capability": capability,
             "amount_usd": best.price_usd, "trust": verdict.trust, "escrow_id": escrow.escrow_id})
        return f"Hired {best.name} ({capability}); they resolved '{topic}'.", best.price_usd

    def hire_replacement(self, hire: dict, failed_ids: set[str]):
        """Live Crew Optimization: retire a failed specialist and hire the next-best for the same
        capability (excluding those already tried), via the Optimization Engine. Accumulated
        blackboard state is shared, so the replacement continues from where work stood — the user
        never sees the swap. Returns a new room agent, or None if no affordable replacement exists."""
        from core.reputation import choose_best
        from core.room.agents import ExecutorRoomAgent

        cap = hire["capability"]
        candidates = [c for c in find_candidates(cap, limit=6) if c.agent_id not in failed_ids]
        pairs = [(c, vet_agent(c.agent_id)) for c in candidates]
        top = choose_best(pairs, self.preference) if pairs else None
        if top is None:
            return None
        best = top.listing
        if self.referee is not None and not self.referee.affordable(best.price_usd):
            return None
        payee = best.identity.wallets[0].address if best.identity.wallets else best.agent_id
        escrow = get_payments().open_escrow(LAVARD_PAYER, payee, best.price_usd,
                                            memo=f"{self.job_id}:replacement:{cap}")
        new_hire = {"job_id": self.job_id, "node_key": hire["node_key"], "agent_id": best.agent_id,
                    "agent_name": best.name, "capability": cap, "amount_usd": best.price_usd,
                    "in_room_id": f"replacement::{best.name}"}
        get_store().create_hire(
            self.job_id, node_key=hire["node_key"], agent_id=best.agent_id, agent_name=best.name,
            in_room_id=new_hire["in_room_id"], capability=cap, amount_usd=best.price_usd,
            trust=top.trust, confidence=0.0, escrow_id=escrow.escrow_id, payee=payee,
            status="hired")
        self.transcript.hired_in_room.append(
            {"in_room_id": new_hire["in_room_id"], "agent": best.name, "capability": cap,
             "amount_usd": best.price_usd, "trust": top.trust, "escrow_id": escrow.escrow_id,
             "replacement": True})
        return ExecutorRoomAgent(new_hire, self.executor, self.owner_id)


def run_room(job_id: str, *, demo: bool = False, freeze_before_turn: int | None = None,
             budget_usd: float | None = None, clear_frozen: bool = True,
             memory=None, resume: bool = False, router: Router | None = None,
             executor=None, preference: str = "balanced") -> RoomTranscript:
    store = get_store()
    if clear_frozen:
        store.unfreeze_room(job_id)

    job = store.get_job(job_id)
    owner_id = job.get("owner_id", "default-owner") if job else "default-owner"

    # IDEMPOTENCY: a room that already completed must not re-execute on a retried call — that would
    # re-charge coordination and could re-hire mid-room helpers (duplicate escrow). Resume is the
    # explicit exception (it continues an interrupted run).
    if not resume and job and job.get("status") == "room_completed":
        done = RoomTranscript(job_id)
        done.status = "completed"
        done.record(TurnLog(0, "LAVARD", "room already completed (idempotent no-op)", "",
                            method="idempotent"))
        return done

    hires = [h for h in store.get_hires(job_id) if not h["node_key"].startswith("room-helper")]
    transcript = RoomTranscript(job_id)
    if not hires:
        store.clear_checkpoint(job_id)
        transcript.status = "completed"
        return transcript

    # Resume-after-crash (Phase 10): reload progress + carry the budget/turn meter forward so
    # already-completed nodes are skipped and caps still hold across the restart.
    checkpoint = store.get_checkpoint(job_id) if resume else None
    done_nodes: set[str] = set(checkpoint["completed_nodes"]) if checkpoint else set()
    resume_spend = checkpoint["spend_usd"] if checkpoint else 0.0
    resume_turns = checkpoint["room_turns"] if checkpoint else 0
    if checkpoint:
        transcript.resumed_from = {"completed_nodes": sorted(done_nodes),
                                   "spend_usd": resume_spend, "room_turns": resume_turns}

    # A real hire delivers via the paid executor (ExecutorRoomAgent) when one is wired; the
    # scripted stub is the offline default. Demo mode always uses the scripted three-branch scenario.
    real_participants: dict[str, MockRoomAgent] = {}
    if demo:
        scripts, expertise = build_demo_scripts(hires)
    elif executor is not None:
        from core.room.agents import ExecutorRoomAgent

        real_participants = {h["in_room_id"]: ExecutorRoomAgent(h, executor, owner_id)
                             for h in hires}
        scripts, expertise = {}, {}
    else:
        scripts = {h["in_room_id"]: [result(f"{h['in_room_id']} delivered.", topic=h["capability"])]
                   for h in hires}
        expertise = {h["in_room_id"]: {h["capability"]} for h in hires}
    # First-responder knowledge = the REAL owner-scoped persistent memory (HIGH-2). In demo mode
    # a small in-memory seed overlays it so the answer-from-memory branch is exercised hermetically.
    if memory is not None:
        mem = memory
    else:
        mem = MemoryBackedKnowledge(owner_id, seed=DEMO_MEMORY_SEED if demo else None)

    participants: dict[str, MockRoomAgent] = real_participants or {
        h["in_room_id"]: MockRoomAgent(h["in_room_id"], h["capability"],
                                       scripts[h["in_room_id"]], expertise[h["in_room_id"]])
        for h in hires
    }
    blackboard = Blackboard()
    # Budget carries forward across restarts (the money cap is the cross-restart invariant);
    # the turn counter is a per-run loop-guard and resets each run.
    referee = Referee(job_id, budget_usd=budget_usd, resume_spend=resume_spend)
    # One Router shared across the whole job so its semantic cache + cross-agent dedup actually
    # mediate the room's model spend (HIGH-1), not just the standalone demo.
    router = router or Router()
    responder = FirstResponder(job_id, mem, blackboard, participants, transcript,
                               router=router, referee=referee, executor=executor,
                               preference=preference, owner_id=owner_id)

    try:
        for h in hires:
            if h["node_key"] in done_nodes:
                transcript.record(TurnLog(referee.room_turns, "LAVARD",
                                          f"skipped {h['node_key']} (done pre-restart)", "",
                                          method="resumed"))
                continue
            referee.check_budget()  # halt before starting a node if already at the ceiling
            _run_node(h, participants[h["in_room_id"]], referee, responder, blackboard,
                      transcript, freeze_before_turn, router)
            # Checkpoint after every completed node so a crash resumes from here.
            done_nodes.add(h["node_key"])
            store.save_checkpoint(job_id, sorted(done_nodes), referee.spend, referee.room_turns)
        transcript.status = "completed"
        store.clear_checkpoint(job_id)  # clean finish -> no stale checkpoint
    except RefereeStop as stop:
        transcript.status = stop.reason
        transcript.record(TurnLog(referee.room_turns, "LAVARD", "ROOM HALTED", str(stop),
                                  method=""))
        if "budget" in stop.reason:
            from core.observability import alert

            alert("budget_halt", severity="warning", job_id=job_id, reason=stop.reason,
                  spend_usd=referee.spend)
        # Preserve progress so the job can resume after a freeze/crash/budget stop.
        store.save_checkpoint(job_id, sorted(done_nodes), referee.spend, referee.room_turns)
    transcript.spend_usd = referee.spend
    transcript.router_saved_usd = router.log.total_saved
    _mark(store, job_id, "room_" + transcript.status)
    from core.governance import audit

    audit(job_id, "room_" + transcript.status, "LAVARD",
          f"Room {transcript.status}; spend ${transcript.spend_usd:.2f}",
          {"resolutions": transcript.resolutions, "nodes_completed": transcript.nodes_completed,
           "router_saved_usd": transcript.router_saved_usd})
    return transcript


def _run_node(hire: dict, agent: MockRoomAgent, referee: Referee, responder: FirstResponder,
              blackboard: Blackboard, transcript: RoomTranscript,
              freeze_before_turn: int | None, router: Router) -> None:
    node_key = hire["node_key"]
    while True:
        if freeze_before_turn is not None and referee.room_turns + 1 == freeze_before_turn:
            referee.freeze()  # simulate the kill-switch being hit mid-run
        referee.turn(agent.id)  # raises RefereeStop on frozen / limits
        ev = agent.step()

        # Live Crew Optimization: a real hire that did NOT actually deliver is retired mid-room and
        # replaced with the next-best specialist, carrying blackboard state forward. Capped retries
        # so a systemic outage escalates instead of looping.
        if ev.kind == RESULT and hasattr(agent, "delivered_ok") and not agent.delivered_ok:
            from core.reputation import record_failure

            failed_ids = getattr(agent, "_failed_ids", set()) | {hire.get("agent_id", agent.id)}
            record_failure(hire.get("agent_id", ""), job_id=hire.get("job_id", ""),
                           capability=hire.get("capability", ""))
            if len(failed_ids) <= 2:
                replacement = responder.hire_replacement(hire, failed_ids)
                if replacement is not None:
                    replacement._failed_ids = failed_ids
                    transcript.record(TurnLog(referee.room_turns, "LAVARD",
                                              f"retired {agent.id}; hired replacement "
                                              f"{replacement.id} for {node_key}", ev.text,
                                              method="crew_replaced"))
                    agent = replacement
                    continue
            transcript.record(TurnLog(referee.room_turns, "LAVARD",
                                      f"no replacement available for {node_key} — escalating",
                                      ev.text, method="crew_exhausted"))
            transcript.nodes_completed.append(node_key + " (unresolved)")
            return

        if ev.kind == RESULT:
            # Route the work-production spend through the shared Router so a near-duplicate
            # deliverable is served from cache instead of paid for again (HIGH-1). The meter
            # charges the TRUE routed/cached cost, not a flat rate.
            topic = ev.topic or node_key
            _, cost = router.ask_costed(f"work:{hire['capability']}:{topic}",
                                        lambda t=ev.text: t,
                                        tier=classify_step(f"{hire['capability']} {topic}"),
                                        agent_id=agent.id)
            blackboard.add(agent.id, ev.topic or node_key, ev.text)
            referee.charge(cost)
            # Feed the reputation graph: a real hire delivered a node in the room.
            if hire.get("agent_id"):
                from core.reputation import record_delivery

                record_delivery(hire["agent_id"], job_id=hire.get("job_id", ""),
                                capability=hire.get("capability", ""), latency_ms=300,
                                cost_usd=float(getattr(agent, "charged_usd", 0.0)
                                               or hire.get("amount_usd", 0.0)))
            transcript.record(TurnLog(referee.room_turns, agent.id,
                                      f"produced result for {node_key}", ev.text, cost_usd=cost))
            transcript.nodes_completed.append(node_key)
            return

        # QUESTION or STALL -> first-responder loop
        label = "stalled" if ev.kind == STALL else "asked a question"
        transcript.record(TurnLog(referee.room_turns, agent.id, label, ev.text))
        if referee.is_duplicate_question(agent.id, ev.topic):
            transcript.record(TurnLog(referee.room_turns, "LAVARD",
                                      "loop/duplicate detected — breaking", ev.topic))
            transcript.nodes_completed.append(node_key + " (blocked)")
            return
        note = " [degraded: near budget, leaning on memory]" if referee.degraded else ""
        answer, method, cost = responder.resolve(agent.id, ev)
        referee.charge(cost)  # may raise budget_exceeded
        transcript.record(TurnLog(referee.room_turns, "LAVARD",
                                  f"unstuck {agent.id} via {method}{note}", answer,
                                  method=method, cost_usd=cost))


def _mark(store, job_id: str, status: str) -> None:
    from datetime import datetime, timezone
    with store._connect() as c:  # noqa: SLF001
        c.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                  (status, datetime.now(timezone.utc).isoformat(), job_id))
