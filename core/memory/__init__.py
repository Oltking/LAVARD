"""Portable Memory + Playbooks — the moat (§4.5).

distill-on-close (redacted at capture) + reuse-on-intake (seed Foreman from a known-good Playbook,
skip work memory already answers). Owner-scoped; facts carry confidence + freshness.
"""

from core.memory.redact import redact  # noqa: F401
from core.memory.store import MemoryStore, get_memory  # noqa: F401
from core.memory.distill import distill_job  # noqa: F401
from core.memory.reuse import (  # noqa: F401
    blueprint_for_goal,
    match_playbook,
    memory_answer_for_node,
    preferred_crew_for_goal,
    seed_facts_for_goal,
)
