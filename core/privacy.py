"""Data-governance policy as code — the single source of truth for what may cross a boundary.

See docs/privacy.md for the full statement. This module names the tiers and provides the guards
other modules import, so the rule lives in one place instead of being re-derived ad hoc.

Rule: learn from behavior, never share content. User work is Tier 1 (private, owner-scoped). Only
non-identifying aggregate statistics are Tier 2 (global learning). Only genuinely public lookups
are Tier 3 (shared). Provider (agent) reputation is global because it is not user data.
"""

from __future__ import annotations

from enum import Enum

# Keys that must never appear in a Tier-2 aggregate record (they identify a user or their work).
_FORBIDDEN_IN_AGGREGATE = frozenset({
    "owner_id", "owner", "goal", "goal_shape", "node_titles", "title", "description",
    "deliverable", "result", "text", "crew", "agent_id", "caller_id", "job_id",
})


class Tier(str, Enum):
    PRIVATE = "private"        # owner-scoped; never shared across users
    AGGREGATE = "aggregate"    # global learning; anonymized, no user content
    PUBLIC = "public"          # public knowledge; shareable across callers


def assert_aggregate_safe(record: dict) -> dict:
    """Raise if a would-be Tier-2 aggregate record carries any user-identifying field. Use this at
    the boundary where anything is contributed to the global learning model."""
    leaked = _FORBIDDEN_IN_AGGREGATE & set(record)
    if leaked:
        raise ValueError(
            f"privacy violation: aggregate record must carry no user content, but has {sorted(leaked)}. "
            f"Only non-identifying statistics (e.g. capability counts) may be learned globally."
        )
    return record
