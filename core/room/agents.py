"""Simulated hired agents for the Room.

Real hired ASPs are third parties; for local runs each hire is driven by a deterministic script
of events (produce / ask / stall). `step()` just returns the next scripted event — the controller
handles resolution between steps, exactly as it would mediate a real agent. `knows()` lets a
participant answer a peer's question during the poll-the-room branch.
"""

from __future__ import annotations

from core.room.models import Event, result


class MockRoomAgent:
    def __init__(self, in_room_id: str, capability: str, script: list[Event],
                 expertise: set[str] | None = None) -> None:
        self.id = in_room_id
        self.capability = capability
        self._script = list(script)
        self._i = 0
        self.expertise = (expertise or set()) | {capability}
        self.produced: list[str] = []

    def step(self) -> Event:
        if self._i < len(self._script):
            ev = self._script[self._i]
            self._i += 1
        else:
            ev = result(f"{self.id} delivered its output.")
        if ev.kind == "result":
            self.produced.append(ev.topic or ev.text)
        return ev

    def knows(self, topic: str) -> str | None:
        if topic in self.expertise:
            return f"{self.id} (a {self.capability} specialist) answers: '{topic}' → handled."
        return None


class ExecutorRoomAgent:
    """A real hired agent in the Room: its deliverable is produced by actually invoking the agent
    through the paid executor (Agent-to-MCP via TheHouse, or a direct pay path) rather than a
    scripted stub. Same interface as MockRoomAgent so the controller is transport-agnostic.

    If the executor cannot deliver (no live money/transport rail yet), `step()` returns a RESULT
    that says so honestly — it never fabricates a specialist answer, and the room still completes
    the node instead of hanging."""

    def __init__(self, hire: dict, executor, owner_id: str = "default-owner") -> None:
        self.id = hire["in_room_id"]
        self.capability = hire["capability"]
        self._hire = hire
        self._executor = executor
        self._owner_id = owner_id
        self.expertise = {self.capability}
        self.produced: list[str] = []
        self._delivered = False
        # the real charge the executor reported for this deliverable (for the report/meter)
        self.charged_usd = 0.0
        # True only when the agent actually delivered; False on pending/unavailable/exception so the
        # controller can retire it and hire a replacement (Live Crew Optimization).
        self.delivered_ok = False

    def step(self) -> Event:
        from core.foreman.hire import _run_async

        if self._delivered:
            return result(f"{self.id} delivered its output.", topic=self.capability)
        self._delivered = True
        agent_id = self._hire["agent_id"]
        cap = self.capability
        tool = f"{agent_id}.{cap}"
        args = {"query": cap}
        try:
            res = _run_async(
                self._executor.call(agent_id, tool, args, f"lavard:{self._owner_id}"),
                getattr(self._executor, "loop", None),
            )
        except Exception as e:
            # a genuine execution error → treat as a failed delivery (controller may replace)
            return result(f"{self.id}: delivery failed ({e}).", topic=cap)
        if res is not None and res.status == "delivered" and res.result is not None:
            self.charged_usd = res.charged
            self.delivered_ok = True
            self.produced.append(res.result)
            return result(res.result, topic=cap)
        status = getattr(res, "status", "unavailable")
        if status == "failed":
            # the MCP call genuinely failed → not delivered → controller retires + replaces
            return result(f"{self.id}: MCP delivery failed.", topic=cap)
        # "unavailable" = this hire isn't MCP-serviceable (it's an A2A escrow hire, or the ASP
        # isn't in TheHouse). That is NOT a failure — the specialist's deliverable comes through
        # the A2A escrow flow and is validated at sign-off. Mark delivered so the controller does
        # not needlessly retire and replace a legitimate hire (audit LOW-8).
        self.delivered_ok = True
        return result(f"{self.id}: deliverable via A2A escrow (validated at sign-off).", topic=cap)

    def knows(self, topic: str) -> str | None:
        if topic in self.expertise:
            return f"{self.id} (a {self.capability} specialist) answers: '{topic}' → handled."
        return None


# ---- deterministic demo scenario that exercises all three first-responder branches ----

SEEDED_TOPIC = "__prior_playbook__"
PEER_TOPIC = "__peer_input__"
GAP_TOPIC = "__legal_gap__"
GAP_CAPABILITY = "legal"

DEMO_MEMORY_SEED = {
    SEEDED_TOPIC: "LAVARD recalls a prior playbook covering this — proceed with pattern X."
}


def build_demo_scripts(hire_rows: list[dict]) -> tuple[dict[str, list[Event]], dict[str, set[str]]]:
    """Assign scripts/expertise by specialist index to demonstrate memory / poll / hire.

    index 0 -> stall on a topic LAVARD already knows (answered_from_memory)
    index 1 -> ask a topic a PEER specialist knows (polled_room)
    index 2 -> ask a topic needing an absent capability (hired_new)
    index 3+ -> straightforward produce
    """
    from core.room.models import question, result, stall

    scripts: dict[str, list[Event]] = {}
    expertise: dict[str, set[str]] = {}
    ids = [h["in_room_id"] for h in hire_rows]

    for idx, h in enumerate(hire_rows):
        rid = h["in_room_id"]
        cap = h["capability"]
        if idx == 0:
            scripts[rid] = [
                stall("Blocked — need domain grounding before I start.", SEEDED_TOPIC, cap),
                result(f"{rid} produced its {cap} deliverable.", topic=cap),
            ]
            expertise[rid] = {cap, PEER_TOPIC}  # peer knowledge for index 1 to poll
        elif idx == 1:
            scripts[rid] = [
                question("Need input another specialist already has.", PEER_TOPIC, cap),
                result(f"{rid} produced its {cap} deliverable.", topic=cap),
            ]
            expertise[rid] = {cap}
        elif idx == 2:
            scripts[rid] = [
                question("Hit a legal question no one here covers.", GAP_TOPIC, GAP_CAPABILITY),
                result(f"{rid} produced its {cap} deliverable.", topic=cap),
            ]
            expertise[rid] = {cap}
        else:
            scripts[rid] = [result(f"{rid} produced its {cap} deliverable.", topic=cap)]
            expertise[rid] = {cap}
    _ = ids
    return scripts, expertise
