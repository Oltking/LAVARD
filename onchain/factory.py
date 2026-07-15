"""Backend selection for onchain adapters.

Default = deterministic mocks (offline, reproducible). If OKX credentials are present AND
LAVARD_OKX_LIVE=1, the real OnchainOS backends are selected — these currently raise until the
live API is verified (QUESTIONS.md Q-API-1), which is deliberate: it makes "we haven't wired the
real API yet" loud rather than silently faking live data.
"""

from __future__ import annotations

import os

from onchain.identity import MockOnchainData, OnchainDataClient, OnchainOsData
from onchain.marketplace import MarketplaceClient, MockMarketplace, OnchainOsMarketplace
from onchain.payments import AppPayments, MockPayments, PaymentsClient


def _live() -> bool:
    has_creds = all(os.environ.get(k) for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
    return has_creds and os.environ.get("LAVARD_OKX_LIVE") == "1"


def get_marketplace() -> MarketplaceClient:
    return OnchainOsMarketplace() if _live() else MockMarketplace()


def get_onchain_data() -> OnchainDataClient:
    return OnchainOsData() if _live() else MockOnchainData()


def get_payments() -> PaymentsClient:
    return AppPayments() if _live() else MockPayments()
