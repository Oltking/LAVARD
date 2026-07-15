"""Marketplace query — find candidate ASPs for a sub-task capability (§4.2, Phase 2).

Interface + backends:
- `MarketplaceClient` (Protocol) — the seam the Foreman calls.
- `MockMarketplace` — deterministic candidate ASPs with platform reputation, seeded by capability
  so demos/tests are reproducible. Default backend.
- `OnchainOsMarketplace` — real backend stub. The concrete discovery endpoint on OKX AI is not
  yet verified from a browser (QUESTIONS.md Q-API-1), so this raises with a doc pointer until the
  Agent/Task Marketplace API is confirmed. Invocation surface will be OnchainOS Skills
  (`npx skills add okx/onchainos-skills`) + Developer Portal API — see
  docs/vendor/okxai/platform-overview.md and skills-format.md.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from onchain.schemas import AgentIdentity, AgentListing, ReputationSignals, WalletRef

_ADJECTIVES = ["Swift", "Ledger", "Quantum", "Nimbus", "Aegis", "Cobalt", "Vertex", "Onyx"]
_NOUNS = ["Labs", "Works", "Collective", "Agents", "Foundry", "Guild", "Systems", "Node"]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest(), 16)


class MarketplaceClient(Protocol):
    def search_candidates(self, capability: str, limit: int = 5) -> list[AgentListing]: ...


class MockMarketplace:
    """Deterministic fake marketplace. Same capability -> same candidates every time."""

    def search_candidates(self, capability: str, limit: int = 5) -> list[AgentListing]:
        listings: list[AgentListing] = []
        for i in range(limit):
            s = _seed(capability, str(i))
            name = f"{_ADJECTIVES[s % len(_ADJECTIVES)]}{_NOUNS[(s >> 8) % len(_NOUNS)]}"
            agent_id = f"asp_{capability[:3]}_{s % 100000:05d}"
            addr = "0x" + hashlib.sha1(agent_id.encode()).hexdigest()[:40]
            sol = hashlib.sha256(agent_id.encode()).hexdigest()[:44]
            rep = ReputationSignals(
                score=50 + (s % 50),                  # 50..99
                jobs_completed=(s >> 3) % 400,
                disputes=(s >> 5) % 12,
                stake_okb=float(100 + (s % 900)),     # >=100 OKB
                first_seen_days=(s >> 7) % 720,
            )
            identity = AgentIdentity(
                agent_id=agent_id,
                display_name=name,
                wallets=[WalletRef("x-layer", addr), WalletRef("solana", sol)],
                created_block=1_000_000 + (s % 5_000_000),
            )
            mode = "mcp" if capability in {"data", "finance"} and i % 2 == 0 else "a2a"
            price = round(2 + (s % 4000) / 100, 2)    # $2.00 .. $42.00
            listings.append(
                AgentListing(
                    agent_id=agent_id,
                    name=name,
                    capability=capability,
                    mode=mode,
                    price_usd=price,
                    reputation=rep,
                    identity=identity,
                )
            )
        return listings


# OKX chainIndex → chain name (196 = X Layer, verified live; extend as needed).
_CHAIN_BY_INDEX = {196: "xlayer", 1: "ethereum", 501: "solana", 8453: "base", 56: "bsc",
                   137: "polygon", 42161: "arbitrum"}


class OnchainOsMarketplace:  # pragma: no cover - exercised only with a live CLI + credentials
    """Real OKX.AI marketplace backend via the `onchainos` CLI (okx-ai skill: `agent search`).

    Field map is LIVE-VERIFIED (2026-07-12) against a real `data.list` payload: results are flat —
    `agentId`, `name`, `feedbackRate` (0-100 rating), `soldCount`, `securityRate`,
    `communicationAddress`, `chainIndex`, and per-service `services[].serviceType`/`feeAmount`
    /`serviceMinPrice`. `_require` still guards the essential keys and fails loudly on drift.
    """

    def search_candidates(self, capability: str, limit: int = 5) -> list[AgentListing]:
        from onchain.onchainos_cli import _require, get_cli

        rows = get_cli().agent_search(capability, limit=limit)
        return [self._to_listing(r, capability) for r in rows]

    @staticmethod
    def _to_listing(r: dict, capability: str) -> AgentListing:
        from onchain.onchainos_cli import _require

        _require(r, ("agentId", "name"), "agent search")
        services = r.get("services") or []
        stypes = {str(s.get("serviceType", "")).upper() for s in services}
        # A2MCP-only providers list as "mcp"; anything offering A2A is treated as "a2a".
        mode = "mcp" if ("A2MCP" in stypes and "A2A" not in stypes) else "a2a"

        price = r.get("serviceMinPrice")
        if price is None:
            fees = [s.get("feeAmount") for s in services if s.get("feeAmount") is not None]
            price = min(fees) if fees else 0.0

        reputation = ReputationSignals(
            score=float(r.get("feedbackRate") or 0.0),
            jobs_completed=int(r.get("soldCount") or 0),
            disputes=0,                 # not in the search payload; detail via get-agents/feedback
            stake_okb=0.0,
            first_seen_days=0)

        addr = r.get("communicationAddress") or ""
        chain = _CHAIN_BY_INDEX.get(r.get("chainIndex"), str(r.get("chainIndex", "")))
        identity = AgentIdentity(
            agent_id=str(r["agentId"]), display_name=r["name"],
            wallets=[WalletRef(chain, addr)] if addr else [])
        return AgentListing(
            agent_id=str(r["agentId"]), name=r["name"], capability=capability,
            mode=mode, price_usd=float(price or 0.0), reputation=reputation, identity=identity)
