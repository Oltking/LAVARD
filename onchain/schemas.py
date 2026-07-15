"""Shapes for onchain identity, reputation, and marketplace listings (stdlib dataclasses).

Grounded in docs/vendor/okxai/identity-reputation.md and platform-overview.md:
- unified persistent onchain identity per agent (Agentic Wallet), EVM + Solana addresses
- platform-native reputation + dispute history that the Vetter reads (does not reinvent)
- evaluator/dispute facts: stake in OKB, dispute rate, etc.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WalletRef:
    chain: str          # e.g. "x-layer", "ethereum", "solana"
    address: str


@dataclass
class AgentIdentity:
    """A single persistent onchain identity (Agentic Wallet-anchored)."""

    agent_id: str
    display_name: str
    wallets: list[WalletRef] = field(default_factory=list)
    created_block: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReputationSignals:
    """Platform-native reputation the Vetter layers on top of (does not reinvent)."""

    score: float                 # 0..100 platform reputation
    jobs_completed: int
    disputes: int
    stake_okb: float             # evaluators stake >=100 OKB; ASPs may stake too
    first_seen_days: int         # account age proxy for freshness

    @property
    def dispute_rate(self) -> float:
        total = self.jobs_completed + self.disputes
        return (self.disputes / total) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dispute_rate"] = round(self.dispute_rate, 4)
        return d


@dataclass
class AgentListing:
    """A candidate ASP for a sub-task, as returned by the marketplace query (§4.2)."""

    agent_id: str
    name: str
    capability: str
    mode: str                    # "a2a" | "mcp"  (docs/vendor/okxai/payments-and-modes.md)
    price_usd: float             # quoted / pay-per-call price
    reputation: ReputationSignals
    identity: AgentIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "capability": self.capability,
            "mode": self.mode,
            "price_usd": self.price_usd,
            "reputation": self.reputation.to_dict(),
            "identity": self.identity.to_dict(),
        }
