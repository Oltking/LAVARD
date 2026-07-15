"""Onchain payments — the OKX Agent Payments Protocol (x402) faces of TheHouse.

Seller face (callers pay TheHouse): issue x402 v2 challenges (`PAYMENT-REQUIRED` header:
base64 JSON {x402Version, resource, accepts[]}) at the TheHouse price, verify the signed
authorization on replay, and record an inbound settlement. The caller's wallet address is
TheHouse's caller identity (QUESTIONS.md Q10).

Buyer face (TheHouse pays targets): a PaymentHook for the Dispatcher — on a target's 402,
sign the payload and return the authorization header for the replay, recording an outbound
settlement.

Verifier/signer are pluggable:
- DevPaymentVerifier / DevSigner — deterministic dev-profile implementations (no chain).
- OnchainOSSigner — shells out to `onchainos payment pay --payload` (TEE signing via the
  Agentic Wallet); used when THEHOUSE_PROFILE=prod and OKX credentials are configured.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import now_ms
from thehouse.core.storage.db import settlements

USDT_DECIMALS = 6
XLAYER_NETWORK = "eip155:196"
# USDT on X Layer — placeholder until go-live config; injected via settings/env in prod.
DEFAULT_ASSET = "0x1e4a5963abfd975d8c9021ce480b42188849d41d"

PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"


def to_base_units(amount_usdt: float) -> str:
    return str(int(round(amount_usdt * 10**USDT_DECIMALS)))


def from_base_units(amount: str | int) -> float:
    return int(amount) / 10**USDT_DECIMALS


def build_challenge(resource: str, amount_usdt: float, pay_to: str) -> str:
    """x402 v2 challenge for the PAYMENT-REQUIRED response header."""
    payload = {
        "x402Version": 2,
        "resource": {"url": resource},
        "accepts": [
            {
                "scheme": "exact",
                "network": XLAYER_NETWORK,
                "asset": DEFAULT_ASSET,
                "amount": to_base_units(amount_usdt),
                "payTo": pay_to,
                "extra": {"name": "USDT"},
            }
        ],
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


@dataclass
class PaymentReceipt:
    payer: str
    amount_usdt: float
    scheme: str = "exact"
    tx_ref: str | None = None


class PaymentVerifier(Protocol):
    def verify(self, authorization_header: str, expected_amount_usdt: float) -> PaymentReceipt | None: ...


class DevPaymentVerifier:
    """Dev profile: accepts `DEV-PAYMENT <payer> <base_units> [nonce]` if the amount covers
    the price. The optional nonce mirrors real x402 authorizations being unique per payment
    (the gateway treats byte-identical headers as replays). Real verification happens
    through the x402 facilitator at go-live."""

    def verify(self, authorization_header: str, expected_amount_usdt: float) -> PaymentReceipt | None:
        parts = authorization_header.split()
        if len(parts) not in (3, 4) or parts[0] != "DEV-PAYMENT":
            return None
        payer, raw = parts[1], parts[2]
        try:
            amount = from_base_units(raw)
        except ValueError:
            return None
        if amount + 1e-9 < expected_amount_usdt:
            return None
        return PaymentReceipt(payer=payer, amount_usdt=amount, tx_ref=f"dev:{payer}:{raw}")


class FacilitatorPaymentVerifier:
    """Prod inbound verifier: validate the caller's x402 payment payload through the OKX
    facilitator's `/verify` (session-cert chain/expiry, signature, scope, nonce) before we
    render any service. The caller's `PAYMENT-SIGNATURE` header is a base64 x402 paymentPayload;
    we reconstruct the seller's `paymentRequirements` from the price we're charging.

    Verify only asserts the authorization is spendable and covers the price — it does NOT move
    money. Settlement is queued separately (aggr_deferred batch) and confirmed via status
    polling, so a valid verify + our replay-key claim is the gate to serve."""

    def __init__(self, facilitator: Any, pay_to: str, *, asset: str = DEFAULT_ASSET,
                 network: str = XLAYER_NETWORK, scheme: str | None = None,
                 x402_version: int = 2) -> None:
        self.facilitator = facilitator
        self.pay_to = pay_to
        self.asset = asset
        self.network = network
        self.scheme = scheme  # None → use the caller's declared scheme in the payload
        self.x402_version = x402_version

    async def verify(self, authorization_header: str,
                     expected_amount_usdt: float) -> PaymentReceipt | None:
        try:
            payload = json.loads(base64.b64decode(authorization_header))
        except (ValueError, json.JSONDecodeError):
            return None
        accepted = payload.get("accepted") or payload.get("accepts", [{}])
        accepted = accepted[0] if isinstance(accepted, list) else accepted
        scheme = self.scheme or accepted.get("scheme", "aggr_deferred")
        requirements = {
            "scheme": scheme,
            "network": self.network,
            "asset": self.asset,
            "amount": to_base_units(expected_amount_usdt),
            "payTo": self.pay_to,
        }
        result = await self.facilitator.verify(self.x402_version, payload, requirements)
        if not result.is_valid:
            return None
        return PaymentReceipt(
            payer=result.payer or accepted.get("from", "unknown"),
            amount_usdt=expected_amount_usdt,
            scheme=scheme,
            tx_ref=None,  # deferred: no txHash until the batch settles
        )


def make_verifier() -> PaymentVerifier | None:
    """Profile-appropriate inbound payment verifier for the gateway.

    dev → DevPaymentVerifier. prod → FacilitatorPaymentVerifier when OKX credentials and a
    payTo address are configured; still None (gateway disabled) if prod is misconfigured, so
    the app never accepts dev credentials for real money."""
    from thehouse.core.config import settings

    from thehouse.onchain.facilitator import OnchainOSFacilitator

    if settings.profile != "prod":
        return DevPaymentVerifier()
    facilitator = OnchainOSFacilitator.from_settings()
    pay_to = settings.facilitator_pay_to
    if facilitator is None or not pay_to:
        return None
    return FacilitatorPaymentVerifier(facilitator, pay_to, scheme=settings.facilitator_scheme)


class Signer(Protocol):
    async def sign(self, raw_402_payload: str) -> tuple[str, str]:
        """→ (header_name, authorization_header)"""


class DevSigner:
    def __init__(self, wallet_address: str = "0xTHEHOUSE"):
        self.wallet_address = wallet_address

    async def sign(self, raw_402_payload: str) -> tuple[str, str]:
        decoded = json.loads(base64.b64decode(raw_402_payload))
        amount = decoded["accepts"][0]["amount"]
        return PAYMENT_SIGNATURE_HEADER, f"DEV-PAYMENT {self.wallet_address} {amount}"


class OnchainOSSigner:
    """Prod: `onchainos payment pay --payload '<raw_402>'` → {authorization_header,
    header_name, scheme, wallet} (TEE-signed via the Agentic Wallet)."""

    async def sign(self, raw_402_payload: str) -> tuple[str, str]:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "onchainos", "payment", "pay", "--payload", raw_402_payload,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"onchainos payment pay failed: {err.decode()[:500]}")
        result = json.loads(out)
        return result.get("header_name", PAYMENT_SIGNATURE_HEADER), result["authorization_header"]


def replay_key(authorization_header: str) -> str:
    import hashlib

    return hashlib.sha256(authorization_header.encode()).hexdigest()


class SettlementLedger:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def try_register_key(self, key: str) -> bool:
        """Atomically claim an inbound payment authorization. False = already seen (replay)."""
        from sqlalchemy.exc import IntegrityError

        from thehouse.core.storage.db import payment_keys

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(payment_keys).values(replay_key=key, ts_ms=now_ms()))
            return True
        except IntegrityError:
            return False

    async def release_key(self, key: str) -> None:
        """Un-claim an authorization whose request was refused before any service was
        rendered (rate limit / queue full) so an honest retry can spend it."""
        from sqlalchemy import delete

        from thehouse.core.storage.db import payment_keys

        async with self.engine.begin() as conn:
            await conn.execute(delete(payment_keys).where(payment_keys.c.replay_key == key))

    async def record(
        self,
        direction: str,
        counterparty: str,
        amount_usdt: float,
        tx_ref: str | None = None,
        request_id: str | None = None,
        batch_id: str | None = None,
        scheme: str = "exact",
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(settlements).values(
                    ts_ms=now_ms(),
                    direction=direction,
                    counterparty=counterparty,
                    amount_usdt=round(amount_usdt, 6),
                    scheme=scheme,
                    network=XLAYER_NETWORK,
                    tx_ref=tx_ref,
                    request_id=request_id,
                    batch_id=batch_id,
                )
            )


def make_payment_hook(signer: Signer, ledger: SettlementLedger):
    """Dispatcher PaymentHook: sign the target's 402 challenge and record the outbound
    settlement. Returns the headers dict for the replay."""

    async def hook(entry: Any, response: Any) -> dict[str, str]:
        raw = response.headers.get(PAYMENT_REQUIRED_HEADER)
        if raw is None:
            body = response.json()
            if "x402Version" not in body:
                return {}
            raw = base64.b64encode(json.dumps(body).encode()).decode()
        header_name, authorization = await signer.sign(raw)
        decoded = json.loads(base64.b64decode(raw))
        accept = decoded["accepts"][0]
        await ledger.record(
            direction="out",
            counterparty=accept.get("payTo", entry.asp_id),
            amount_usdt=from_base_units(accept["amount"]),
            tx_ref=f"x402:{accept.get('scheme', 'exact')}",
        )
        return {header_name: authorization}

    return hook
