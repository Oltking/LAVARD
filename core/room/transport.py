"""Room transport seam (QUESTIONS.md Q-ROOM-1).

Decision from Phase 0: the Room is CONTROLLER-MEDIATED by default — every message routes through
LAVARD's referee. `ControllerMediated` is the only active backend. `XmtpDirectTag` is a stub for
the optional direct-tag mode (agents address each other by in-room ID over XMTP), enabled only
when both agents are XMTP-reachable AND the controller stays in the loop metering each turn — not
wired until that safety condition and OKX's native A2A story are verified (docs/vendor/messaging/
xmtp.md). No agent-to-agent traffic may bypass the controller's meter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Message:
    frm: str
    to: str            # "*" for broadcast
    text: str


class RoomTransport(Protocol):
    def send(self, frm: str, to: str, text: str) -> None: ...
    def broadcast(self, frm: str, text: str) -> None: ...
    def history(self) -> list[Message]: ...


@dataclass
class ControllerMediated:
    """Default backend: all messages are logged and pass through the controller."""

    _log: list[Message] = field(default_factory=list)

    def send(self, frm: str, to: str, text: str) -> None:
        self._log.append(Message(frm, to, text))

    def broadcast(self, frm: str, text: str) -> None:
        self._log.append(Message(frm, "*", text))

    def history(self) -> list[Message]:
        return list(self._log)


class XmtpDirectTag:  # pragma: no cover - not active (Q-ROOM-1)
    """Optional XMTP-backed direct-tag transport. Disabled by default."""

    def send(self, frm: str, to: str, text: str) -> None:
        raise NotImplementedError(
            "Direct-tag (XMTP) transport is disabled by default (QUESTIONS.md Q-ROOM-1). "
            "Enable only when both agents are XMTP-reachable and the controller meters each turn."
        )

    def broadcast(self, frm: str, text: str) -> None:
        raise NotImplementedError

    def history(self) -> list[Message]:
        return []
