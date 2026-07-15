"""OKX x402 facilitator HTTP client — the real on-chain settlement rail.

This is the money backend we *submit to* rather than run ourselves: the facilitator batches
signed authorizations into a single X-Layer transaction, sponsors gas, and manages the escrow
state machine (see docs/xlayer_integration.md §2). We only sign payloads, POST them, and poll.

Surface (base `https://web3.okx.com`, prefix `/api/v6/pay/x402`):
  GET  /supported            → facilitator capabilities (schemes, networks, signer addresses)
  POST /verify               → validate a payment payload before settling
  POST /settle               → queue a verified auth for batch settlement (accepted != on-chain)
  GET  /settle/status?txHash → final on-chain outcome (pending | success | failed)

Auth (every request): OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-PASSPHRASE / OK-ACCESS-TIMESTAMP.
  sign = base64(HMAC-SHA256(secret, timestamp + method + requestPath + body))

CRITICAL semantics: a `settle` 200 with status "success" means *accepted for batching only* —
`transaction` is empty until the batch lands. Never treat acceptance as settled; drive the
settlement_pending → settled|failed transition off `/settle/status` (see the reconciliation loop).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from thehouse.core.config import settings


class FacilitatorError(RuntimeError):
    """CLI/HTTP/auth failure or a non-zero business code from the facilitator."""


def _iso_timestamp() -> str:
    # OKX expects millisecond ISO-8601 UTC, e.g. 2026-07-14T10:20:30.123Z
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def sign(secret: str, timestamp: str, method: str, request_path: str, body: str) -> str:
    """base64(HMAC-SHA256(secret, timestamp + method + requestPath + body))."""
    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@dataclass
class VerifyResult:
    is_valid: bool
    payer: str | None = None
    invalid_reason: str | None = None
    invalid_message: str | None = None


@dataclass
class SettleResult:
    """`/settle` acceptance. `transaction` is empty at intake; poll status for the real txHash."""
    success: bool
    payer: str | None = None
    transaction: str = ""            # empty until the batch lands on-chain
    network: str | None = None
    status: str = ""                 # "success" here == accepted for batching, NOT settled
    error_reason: str | None = None
    error_message: str | None = None

    @property
    def accepted(self) -> bool:
        return self.success


@dataclass
class SettlementStatus:
    """`/settle/status` — the actual on-chain outcome."""
    status: str                      # "pending" | "success" | "failed"
    transaction: str = ""
    payer: str | None = None
    network: str | None = None

    @property
    def landed(self) -> bool:
        return self.status == "success"

    @property
    def terminal(self) -> bool:
        return self.status in ("success", "failed")


class OnchainOSFacilitator:
    """Thin async client over the OKX x402 facilitator HTTP API.

    Inject `client` (an httpx.AsyncClient, possibly with an ASGITransport fake) for tests;
    otherwise one is built from settings. Credentials come from settings (OKX_* env)."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        passphrase: str | None = None,
        base_url: str | None = None,
        path_prefix: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.okx_api_key
        self.secret_key = secret_key if secret_key is not None else settings.okx_secret_key
        self.passphrase = passphrase if passphrase is not None else settings.okx_passphrase
        self.base_url = (base_url or settings.facilitator_base_url).rstrip("/")
        self.path_prefix = path_prefix or settings.facilitator_path_prefix
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls) -> "OnchainOSFacilitator | None":
        """Construct only when real credentials are present; else None (stay on Dev rails)."""
        if not (settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase):
            return None
        return cls()

    def credentialed(self) -> bool:
        return bool(self.api_key and self.secret_key and self.passphrase)

    async def _request(self, method: str, path: str, *, body: dict | None = None,
                       params: dict | None = None) -> dict:
        if not self.credentialed():
            raise FacilitatorError(
                "OKX facilitator credentials missing (OKX_API_KEY / OKX_SECRET_KEY / "
                "OKX_PASSPHRASE). Set them from the OKX Developer Portal."
            )
        request_path = self.path_prefix + path
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        # The signature must cover the exact query string that goes on the wire.
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            signed_path = f"{request_path}?{query}"
        else:
            signed_path = request_path
        ts = _iso_timestamp()
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign(self.secret_key, ts, method, signed_path, body_str),
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "OK-ACCESS-TIMESTAMP": ts,
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        try:
            resp = await client.request(
                method, request_path, params=params,
                content=body_str.encode() if body_str else None, headers=headers,
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code == 401:
            raise FacilitatorError(f"facilitator auth failed ({resp.status_code}): {resp.text[:300]}")
        try:
            envelope = resp.json()
        except ValueError as e:
            raise FacilitatorError(
                f"facilitator {method} {path} non-JSON ({resp.status_code}): {resp.text[:300]}"
            ) from e
        if str(envelope.get("code", "0")) != "0":
            raise FacilitatorError(
                f"facilitator {method} {path} code={envelope.get('code')}: {envelope.get('msg')}"
            )
        return envelope.get("data", {}) or {}

    async def supported(self) -> dict:
        return await self._request("GET", "/supported")

    async def verify(self, x402_version: int, payment_payload: dict,
                     payment_requirements: dict) -> VerifyResult:
        data = await self._request("POST", "/verify", body={
            "x402Version": x402_version,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        })
        return VerifyResult(
            is_valid=bool(data.get("isValid")),
            payer=data.get("payer"),
            invalid_reason=data.get("invalidReason"),
            invalid_message=data.get("invalidMessage"),
        )

    async def settle(self, x402_version: int, payment_payload: dict,
                     payment_requirements: dict) -> SettleResult:
        data = await self._request("POST", "/settle", body={
            "x402Version": x402_version,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        })
        return SettleResult(
            success=bool(data.get("success")),
            payer=data.get("payer"),
            transaction=data.get("transaction", "") or "",
            network=data.get("network"),
            status=data.get("status", "") or "",
            error_reason=data.get("errorReason"),
            error_message=data.get("errorMessage"),
        )

    async def settle_status(self, tx_hash: str) -> SettlementStatus:
        data = await self._request("GET", "/settle/status", params={"txHash": tx_hash})
        return SettlementStatus(
            status=data.get("status", "pending") or "pending",
            transaction=data.get("transaction", "") or "",
            payer=data.get("payer"),
            network=data.get("network"),
        )
