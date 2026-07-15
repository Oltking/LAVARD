"""Action Review (§4.6): every proposed action (by LAVARD or a hired agent) is captured verbatim,
its expected outcome and downside noted, its necessity checked, and a verdict issued:
`approve | approve-with-edits | deny | escalate-to-user`.

Policy mapping: always-allow ⇒ approve; ask-once ⇒ approve-with-edits (granted, but logged);
always-ask ⇒ escalate-to-user (NOT executed until the user approves). Anything flagged
destructive escalates regardless. Not-required actions are denied (necessity, §4.1).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.governance.permissions import (
    ALWAYS_ALLOW,
    ALWAYS_ASK,
    ASK_ONCE,
    Action,
    PermissionPolicy,
    classify_tier,
)

_DESTRUCTIVE_HINTS = ("delete", "drop", "wipe", "revoke", "transfer all", "self-destruct",
                      "rm -rf", "burn", "liquidate")

APPROVE = "approve"
APPROVE_WITH_EDITS = "approve-with-edits"
DENY = "deny"
ESCALATE = "escalate-to-user"


@dataclass
class Verdict:
    action: str
    action_type: str
    tier: str
    verdict: str
    expected_outcome: str
    downside: str
    required: bool
    rationale: str

    @property
    def will_execute(self) -> bool:
        return self.verdict in (APPROVE, APPROVE_WITH_EDITS)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["will_execute"] = self.will_execute
        return d


class ActionReview:
    def __init__(self, policy: PermissionPolicy | None = None) -> None:
        self.policy = policy or PermissionPolicy()

    def review(self, action: Action) -> Verdict:
        tier = classify_tier(action, self.policy)
        looks_destructive = any(h in action.description.lower() for h in _DESTRUCTIVE_HINTS) \
            or action.type == "destructive"

        expected = _expected(action)
        downside = _downside(action, looks_destructive)

        if not action.required:
            verdict, rationale = DENY, "Not required by the goal (necessity test failed)."
        elif looks_destructive:
            verdict, rationale = ESCALATE, "Destructive/irreversible — must be user-approved."
        elif tier == ALWAYS_ALLOW:
            verdict, rationale = APPROVE, "Low-risk action under always-allow policy."
        elif tier == ASK_ONCE:
            verdict, rationale = APPROVE_WITH_EDITS, "Permitted under ask-once; logged for audit."
        else:  # ALWAYS_ASK
            verdict, rationale = ESCALATE, "Spending/scope/high-risk — always-ask: user must approve."

        return Verdict(action.description, action.type, tier, verdict, expected, downside,
                       action.required, rationale)


def _expected(a: Action) -> str:
    if a.type == "spend":
        return f"Release/commit ${a.amount_usd:.2f} to {a.target or 'a counterparty'}."
    if a.type == "hire":
        return f"Engage {a.target or 'a specialist'} under escrow."
    if a.type == "grant_scope":
        return f"Grant scope/permission: {a.target}."
    return a.description


def _downside(a: Action, destructive: bool) -> str:
    if destructive:
        return "Irreversible loss of assets/data/access if wrong."
    if a.type == "spend":
        return "Funds committed; recoverable only via dispute/refund."
    if a.type == "grant_scope":
        return "Expands blast radius of a hired agent."
    return "Low; bounded and reversible."


def review_action(action: Action, policy: PermissionPolicy | None = None) -> Verdict:
    return ActionReview(policy).review(action)
