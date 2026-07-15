"""Permission tiers (§4.6): always-allow / ask-once / always-ask.

Spending, granting scope, and destructive actions default to **always-ask**. The policy is
configurable per owner, but the safe defaults are baked in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ALWAYS_ALLOW = "always-allow"
ASK_ONCE = "ask-once"
ALWAYS_ASK = "always-ask"

# action_type -> default tier
_DEFAULT_TIERS: dict[str, str] = {
    "read": ALWAYS_ALLOW,
    "plan": ALWAYS_ALLOW,
    "message": ALWAYS_ALLOW,
    "vet": ALWAYS_ALLOW,
    "hire": ASK_ONCE,
    "spend": ALWAYS_ASK,
    "grant_scope": ALWAYS_ASK,
    "destructive": ALWAYS_ASK,
}


@dataclass
class Action:
    type: str                 # one of the keys above (unknown types default to always-ask)
    description: str
    amount_usd: float = 0.0
    target: str = ""
    required: bool = True      # did the goal genuinely require this? (necessity)


@dataclass
class PermissionPolicy:
    """Per-owner overrides on top of the safe defaults."""

    overrides: dict[str, str] = field(default_factory=dict)
    # spends up to this auto-clear the 'spend' gate as ask-once instead of always-ask
    auto_spend_ceiling_usd: float = 0.0

    def tier_for(self, action: Action) -> str:
        if action.type in self.overrides:
            return self.overrides[action.type]
        base = _DEFAULT_TIERS.get(action.type, ALWAYS_ASK)  # unknown => safest
        if action.type == "spend" and 0 < action.amount_usd <= self.auto_spend_ceiling_usd:
            return ASK_ONCE
        return base


def classify_tier(action: Action, policy: PermissionPolicy | None = None) -> str:
    return (policy or PermissionPolicy()).tier_for(action)
