"""The Referee (§4.3, non-negotiable): per-agent + per-room turn limits, loop/duplicate-question
detection, a running budget meter with a hard ceiling + graceful degradation, and the global
kill-switch. Every turn boundary passes through here.

The kill-switch is backed by the store (`job_control.frozen`) so it can be tripped from another
process/CLI mid-run and is seen at the next turn boundary.
"""

from __future__ import annotations

from core.config import get_settings
from core.store import get_store


class RefereeStop(Exception):
    """Raised to freeze/stop the room. `reason` is one of the RoomTranscript statuses."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class Referee:
    def __init__(self, job_id: str, budget_usd: float | None = None,
                 room_turn_limit: int | None = None, agent_turn_limit: int | None = None,
                 resume_spend: float = 0.0, resume_turns: int = 0) -> None:
        s = get_settings()
        self.job_id = job_id
        self.budget_usd = budget_usd if budget_usd is not None else s.job_budget_usd
        self.room_turn_limit = room_turn_limit if room_turn_limit is not None else s.room_turn_limit
        self.agent_turn_limit = agent_turn_limit if agent_turn_limit is not None else s.agent_turn_limit
        # Resume-after-crash: carry the meter forward so caps hold across restarts (Phase 10).
        self.room_turns = resume_turns
        self.agent_turns: dict[str, int] = {}
        self.spend = resume_spend
        self._seen_questions: set[tuple[str, str]] = set()
        self._store = get_store()

    # --- turn accounting (call at every turn boundary) ---
    def turn(self, agent_id: str) -> None:
        if self._store.is_room_frozen(self.job_id):
            raise RefereeStop("frozen", "Kill-switch engaged — room frozen.")
        self.room_turns += 1
        self.agent_turns[agent_id] = self.agent_turns.get(agent_id, 0) + 1
        if self.room_turns > self.room_turn_limit:
            raise RefereeStop("turn_limit", f"Room turn limit {self.room_turn_limit} exceeded.")
        if self.agent_turns[agent_id] > self.agent_turn_limit:
            raise RefereeStop(
                "turn_limit", f"Agent {agent_id} turn limit {self.agent_turn_limit} exceeded.")

    # --- budget meter ---
    def check_budget(self) -> None:
        """Pre-flight guard: refuse to start new work when already at/over the ceiling.
        Prevents a resumed over-budget job from committing yet another hire (Phase 10)."""
        if self.spend >= self.budget_usd:
            raise RefereeStop("budget_exceeded",
                              f"Budget ${self.budget_usd:.2f} reached (spent ${self.spend:.2f}); "
                              f"refusing new work.")

    def affordable(self, amount: float) -> bool:
        """Would committing `amount` stay within the ceiling? Used to refuse a mid-room hire
        BEFORE opening escrow, so the cap can't be overshot by a full hire (audit finding MED-1)."""
        return (self.spend + amount) <= self.budget_usd

    def charge(self, amount: float) -> None:
        self.spend += amount
        if self.spend > self.budget_usd:
            raise RefereeStop("budget_exceeded",
                              f"Budget ${self.budget_usd:.2f} exceeded (spent ${self.spend:.2f}).")

    @property
    def degraded(self) -> bool:
        """Near the ceiling — the controller should lean on memory + cheaper routes."""
        return self.spend >= 0.8 * self.budget_usd

    # --- loop / duplicate-question detection ---
    def is_duplicate_question(self, agent_id: str, topic: str) -> bool:
        key = (agent_id, topic)
        if key in self._seen_questions:
            return True
        self._seen_questions.add(key)
        return False

    # --- kill-switch controls ---
    def freeze(self) -> None:
        self._store.freeze_room(self.job_id)

    def is_frozen(self) -> bool:
        return self._store.is_room_frozen(self.job_id)
