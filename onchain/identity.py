"""Onchain identity + reputation reads — raw material for the Vetter (§4.1, feeds Phase 3).

Interface + backends:
- `OnchainDataClient` (Protocol) — identity, tx history, contract interactions, funder tracing,
  holder clusters, platform reputation. Mirrors docs/vendor/okxai/onchain-data.md.
- `MockOnchainData` — deterministic fake chain state, including deliberately OPAQUE origins
  (fresh wallet / mixer) so the Vetter's honest-limits behaviour (§4.1) is exercisable. Default.
- `OnchainOsData` — real backend stub (OnchainOS Skills: okx-agentic-wallet, okx-dex-token holder
  clusters, X Layer explorer). Not active until Q-API-1 is resolved.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from onchain.schemas import AgentIdentity, ReputationSignals, WalletRef


@dataclass
class FunderEdge:
    from_address: str
    to_address: str
    amount_usd: float
    opaque: bool = False        # True when the funder is a fresh wallet / mixer / privacy tool


@dataclass
class OnchainProfile:
    """Everything the Vetter needs to reason about one agent's provenance."""

    identity: AgentIdentity
    reputation: ReputationSignals
    contracts_touched: list[str] = field(default_factory=list)
    risky_contracts: list[str] = field(default_factory=list)
    funder_trace: list[FunderEdge] = field(default_factory=list)
    tx_count: int = 0


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


class OnchainDataClient(Protocol):
    def get_profile(self, agent_id: str) -> OnchainProfile: ...


# A few well-known "risky" contract addresses for the mock to flag (mixers, drainers).
_KNOWN_RISKY = {
    "0x" + "de1e" * 10: "mixer",
    "0x" + "dead" * 10: "known-drainer",
}


class MockOnchainData:
    """Deterministic fake onchain state. ~1/4 of agents get an opaque/mixer-funded origin."""

    def get_profile(self, agent_id: str) -> OnchainProfile:
        s = _h(agent_id)
        addr = "0x" + hashlib.sha1(agent_id.encode()).hexdigest()[:40]
        sol = hashlib.sha256(("sol" + agent_id).encode()).hexdigest()[:44]
        identity = AgentIdentity(
            agent_id=agent_id,
            display_name=agent_id,
            wallets=[WalletRef("x-layer", addr), WalletRef("solana", sol)],
            created_block=1_000_000 + (s % 5_000_000),
        )
        reputation = ReputationSignals(
            score=40 + (s % 60),
            jobs_completed=(s >> 4) % 500,
            disputes=(s >> 6) % 15,
            stake_okb=float(100 + (s % 1500)),
            first_seen_days=(s >> 8) % 900,
        )
        contracts = ["0x" + hashlib.sha1(f"{agent_id}:{i}".encode()).hexdigest()[:40]
                     for i in range((s % 4) + 1)]

        opaque_origin = (s % 4) == 0          # deterministic 25% opaque
        risky_addr = list(_KNOWN_RISKY)[s % len(_KNOWN_RISKY)]
        risky = [risky_addr] if opaque_origin else []
        funder = "0x" + hashlib.sha1(("funder" + agent_id).encode()).hexdigest()[:40]
        trace = [
            FunderEdge(
                from_address=(risky_addr if opaque_origin else funder),
                to_address=addr,
                amount_usd=round(50 + (s % 5000) / 10, 2),
                opaque=opaque_origin,
            )
        ]
        return OnchainProfile(
            identity=identity,
            reputation=reputation,
            contracts_touched=contracts,
            risky_contracts=risky,
            funder_trace=trace,
            tx_count=(s >> 2) % 3000,
        )


class OnchainOsData:  # pragma: no cover - exercised only with a live CLI + credentials
    """Real Vetter-forensics backend via the `onchainos` CLI: on-chain reputation
    (`agent reputation`) + wallet security scan (`wallet security-scan`).

    Field maps are `_require`-validated (fail loudly on shape drift). NOTE (honest limit): the
    funder-provenance trace maps to okx-dex-market address-tracker data whose exact shape is not
    yet confirmed — until it is wired, `funder_trace` is left empty, which the Vetter treats as a
    non-opaque origin. Confirm and wire the tracker before relying on opaque-origin detection in
    production (see docs/vendor/okxai/onchainos-cli.md 'Remaining to confirm at deploy')."""

    def get_profile(self, agent_id: str) -> OnchainProfile:
        from onchain.onchainos_cli import _require, get_cli

        cli = get_cli()
        # `get-agents` returns the same flat agent-object schema as `agent search` (LIVE-verified):
        # feedbackRate / soldCount / securityRate / communicationAddress / chainIndex. `_require`
        # guards the essentials and fails loudly if get-agents diverges.
        detail = cli.get_agents(agent_id)
        # get-agents nests as data.list[].agentList[] — flatten to the first matching agent object.
        agent = detail
        if isinstance(agent, dict) and "list" in agent:
            outer = agent["list"]
            agent = outer[0] if isinstance(outer, list) and outer else outer
        if isinstance(agent, dict) and "agentList" in agent:
            inner = agent["agentList"]
            agent = inner[0] if isinstance(inner, list) and inner else inner
        _require(agent, ("agentId", "feedbackRate", "soldCount"), "agent get-agents")

        from onchain.marketplace import _CHAIN_BY_INDEX
        reputation = ReputationSignals(
            score=float(agent.get("feedbackRate") or 0.0),
            jobs_completed=int(agent.get("soldCount") or 0),
            disputes=int(agent.get("disputeCount") or 0),   # detail-only field if present
            stake_okb=0.0,
            first_seen_days=int(agent.get("firstSeenDays") or 0))

        addr = agent.get("communicationAddress") or ""
        chain = _CHAIN_BY_INDEX.get(agent.get("chainIndex"), str(agent.get("chainIndex", "")))
        # A low on-chain securityRate (0-5) is itself a risk signal the Vetter should see.
        risky: list[str] = []
        if addr:
            scan = cli.security_scan(addr)
            risky += [c.get("address", "") for c in (scan.get("riskyContracts") or []) if c]
        if float(agent.get("securityRate") or 5.0) < 3.0:
            risky.append(f"low-security-rating:{agent.get('securityRate')}")

        identity = AgentIdentity(
            agent_id=str(agent["agentId"]), display_name=agent.get("name", agent_id),
            wallets=[WalletRef(chain, addr)] if addr else [])
        return OnchainProfile(
            identity=identity, reputation=reputation,
            risky_contracts=sorted({c for c in risky if c}),
            funder_trace=[],   # address-tracker trace shape still unconfirmed (see docstring)
            tx_count=int(agent.get("txCount") or 0))
