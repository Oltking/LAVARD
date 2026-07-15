"""Foreman marketplace step: query candidate ASPs for a capability and rank them (§4.2, Phase 2).

Selection blends fit, reputation, dispute history, and price (spec §4.2: "pick on a blend of fit,
Vetter score, price, and reputation"). The Vetter's trust score is folded in at Phase 3; here we
rank on the platform-native reputation the marketplace already exposes.
"""

from __future__ import annotations

from onchain import get_marketplace
from onchain.schemas import AgentListing


def rank_score(listing: AgentListing) -> float:
    """Higher = better. Reputation up, disputes down, price down."""
    rep = listing.reputation
    return rep.score - rep.dispute_rate * 50.0 - listing.price_usd * 0.5


def find_candidates(capability: str, limit: int = 5) -> list[AgentListing]:
    """Query the marketplace for a capability and return candidates best-first."""
    listings = get_marketplace().search_candidates(capability, limit=limit)
    return sorted(listings, key=rank_score, reverse=True)
