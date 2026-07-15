"""Room event + transcript shapes (stdlib dataclasses).

Agents emit one of three events per turn: produce a result, ask a question, or stall. A stall past
the delay threshold is treated like a question ("how do I proceed?") — both trigger the
controller-as-first-responder loop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# event kinds
RESULT = "result"
QUESTION = "question"
STALL = "stall"

# first-responder resolution methods (the three branches of §4.3)
ANSWERED_FROM_MEMORY = "answered_from_memory"
POLLED_ROOM = "polled_room"
HIRED_NEW = "hired_new"
UNRESOLVED = "unresolved"


@dataclass
class Event:
    kind: str                      # RESULT | QUESTION | STALL
    text: str = ""
    topic: str = ""                # subject the question/result is about
    needed_capability: str = ""    # capability that could answer a question


def result(text: str, topic: str = "") -> Event:
    return Event(RESULT, text=text, topic=topic)


def question(text: str, topic: str, needed_capability: str = "") -> Event:
    return Event(QUESTION, text=text, topic=topic, needed_capability=needed_capability)


def stall(text: str, topic: str, needed_capability: str = "") -> Event:
    return Event(STALL, text=text, topic=topic, needed_capability=needed_capability)


@dataclass
class TurnLog:
    turn: int
    actor: str                     # in_room_id of the agent, or "LAVARD"
    action: str                    # human-readable
    detail: str = ""
    method: str = ""               # resolution method when applicable
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoomTranscript:
    job_id: str
    status: str = "running"        # completed | frozen | budget_exceeded | turn_limit
    turns: list[TurnLog] = field(default_factory=list)
    resolutions: dict[str, int] = field(default_factory=dict)  # method -> count
    hired_in_room: list[dict] = field(default_factory=list)
    spend_usd: float = 0.0
    router_saved_usd: float = 0.0  # cache/dedup savings the Router realized this run (HIGH-1)
    nodes_completed: list[str] = field(default_factory=list)
    resumed_from: dict[str, Any] | None = None  # set when resuming from a checkpoint (Phase 10)

    def record(self, log: TurnLog) -> None:
        self.turns.append(log)
        if log.method:
            self.resolutions[log.method] = self.resolutions.get(log.method, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "turns": [t.to_dict() for t in self.turns],
            "resolutions": self.resolutions,
            "hired_in_room": self.hired_in_room,
            "spend_usd": round(self.spend_usd, 2),
            "router_saved_usd": round(self.router_saved_usd, 4),
            "nodes_completed": self.nodes_completed,
            "resumed_from": self.resumed_from,
        }
