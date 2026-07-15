"""Onchain adapters: OnchainOS + Agentic Wallet + marketplace + settlement.

All external OKX calls sit behind interfaces here. Per QUESTIONS.md Q-API-1, the live API
signatures are not yet verified from a browser, so the default backends are deterministic
mocks/testnet; real backends (OnchainOS Skills / Developer Portal API) are stubbed with the doc
citation and activate when OKX credentials are configured. Vendor facts: docs/vendor/okxai/*.
"""

from onchain.factory import get_marketplace, get_onchain_data, get_payments  # noqa: F401
