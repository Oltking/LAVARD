"""Shared blackboard (§4.3): room-scoped knowledge visible to all agents, so no fact is
discovered twice. Facts are keyed by topic; lookup is exact-topic then keyword-substring.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fact:
    author: str          # in_room_id or "LAVARD" or "memory"
    topic: str
    text: str


@dataclass
class Blackboard:
    facts: list[Fact] = field(default_factory=list)

    def add(self, author: str, topic: str, text: str) -> None:
        self.facts.append(Fact(author, topic, text))

    def find(self, topic: str) -> Fact | None:
        for f in self.facts:
            if f.topic == topic:
                return f
        t = topic.lower()
        for f in self.facts:
            if t and (t in f.topic.lower() or t in f.text.lower()):
                return f
        return None

    def topics(self) -> set[str]:
        return {f.topic for f in self.facts}
