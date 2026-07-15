"""A2A-pay settlement rail — the PROVEN on-chain money mover for TheHouse.

Validated live on X Layer mainnet (2026-07-15, tx 0xa65fd7203bb759aa82eb6dc904b2869e079fc00f8ab…):
`onchainos payment a2a-pay create → pay → status` settles USDT between two agent wallets via
EIP-3009. Unlike the raw x402 facilitator /verify+/settle (which returned code:-1 for us — seller-
gated), a2a-pay is the working agent-to-agent charge rail.

Two faces:
- SELLER (callers pay TheHouse): `create_charge` with recipient = TheHouse's wallet → the buyer
  pays that payment_id → `status` == "completed" credits the inbound settlement.
- BUYER (TheHouse pays a target): `pay(payment_id, raw_amount, currency, recipient)` settles and
  returns the on-chain tx_hash.

CRITICAL detail learned live: `pay --amount` is the RAW base-unit amount (the challenge's
`request.amount`, e.g. "10000" for 0.01 USDT), while `create --amount` is decimal ("0.01").

`DevA2aPay` is a deterministic in-memory stub for offline tests (no CLI, no chain).
`OnchainOsA2aPay` shells out to the real CLI in prod.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Charge:
    payment_id: str
    currency: str        # ERC-20 token contract address
    raw_amount: str      # base units (what `pay --amount` expects)
    pay_to: str


@dataclass
class PayResult:
    payment_id: str
    status: str          # "completed" | "settling" | "pending" | "failed"
    tx_hash: str | None = None

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    @property
    def terminal(self) -> bool:
        return self.status in ("completed", "failed")


class A2aPayRail(Protocol):
    async def create_charge(self, amount_decimal: str, symbol: str, recipient: str,
                            *, chain: str, description: str = "",
                            external_id: str = "") -> Charge: ...
    async def pay(self, payment_id: str, raw_amount: str, currency: str,
                  recipient_address: str) -> PayResult: ...
    async def status(self, payment_id: str) -> PayResult: ...


class DevA2aPay:
    """Deterministic in-memory a2a-pay for offline tests: create mints a payment_id, pay marks it
    completed with a synthetic tx_hash, status echoes it. No CLI, no chain, no creds."""

    def __init__(self, decimals: int = 6):
        self.decimals = decimals
        self._charges: dict[str, Charge] = {}
        self._results: dict[str, PayResult] = {}
        self._seq = 0

    def _to_raw(self, amount_decimal: str) -> str:
        return str(int(round(float(amount_decimal) * 10 ** self.decimals)))

    async def create_charge(self, amount_decimal, symbol, recipient, *, chain,
                            description="", external_id="") -> Charge:
        self._seq += 1
        pid = f"dev_a2a_{self._seq:06d}"
        charge = Charge(payment_id=pid, currency=f"0xDEV{symbol}",
                        raw_amount=self._to_raw(amount_decimal), pay_to=recipient)
        self._charges[pid] = charge
        self._results[pid] = PayResult(pid, "pending")
        return charge

    async def pay(self, payment_id, raw_amount, currency, recipient_address) -> PayResult:
        charge = self._charges.get(payment_id)
        if charge is None:                       # a charge created elsewhere (buyer-only flow)
            self._charges[payment_id] = Charge(payment_id, currency, str(raw_amount), recipient_address)
        if str(raw_amount) != self._charges[payment_id].raw_amount and charge is not None:
            return PayResult(payment_id, "failed")   # amount mismatch (mirrors the live check)
        res = PayResult(payment_id, "completed", tx_hash=f"0xdevtx{payment_id[-6:]}")
        self._results[payment_id] = res
        return res

    async def status(self, payment_id) -> PayResult:
        return self._results.get(payment_id, PayResult(payment_id, "pending"))


class OnchainOsA2aPay:
    """Prod rail: shells out to the real `onchainos payment a2a-pay` (proven live)."""

    def __init__(self, binary: str = "onchainos", timeout: float = 60.0):
        self.binary = binary
        self.timeout = timeout

    async def _run(self, args: list[str]) -> dict:
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        text = out.decode().strip()
        # The CLI returns either a bare object (create) or an {ok,data|error} envelope.
        obj = json.loads(text.splitlines()[-1]) if text else {}
        if isinstance(obj, dict) and obj.get("ok") is False:
            raise RuntimeError(f"a2a-pay {' '.join(args)}: {obj.get('error')}")
        return obj.get("data", obj) if isinstance(obj, dict) else obj

    async def create_charge(self, amount_decimal, symbol, recipient, *, chain,
                            description="", external_id="") -> Charge:
        args = ["payment", "a2a-pay", "create", "--type", "charge", "--amount", str(amount_decimal),
                "--chain", chain, "--symbol", symbol, "--recipient", recipient]
        if description:
            args += ["--description", description]
        if external_id:
            args += ["--external-id", external_id]
        data = await self._run(args)
        raw = next((d["value"] for d in data.get("deliveries", []) if d.get("type") == "raw"), None)
        req = json.loads(raw)["data"]["request"] if raw else {}
        return Charge(payment_id=data["payment_id"], currency=req.get("currency", ""),
                      raw_amount=str(req.get("amount", "")), pay_to=recipient)

    async def pay(self, payment_id, raw_amount, currency, recipient_address) -> PayResult:
        data = await self._run(["payment", "a2a-pay", "pay", "--payment-id", payment_id,
                                "--amount", str(raw_amount), "--currency", currency,
                                "--recipient-address", recipient_address])
        return PayResult(payment_id, data.get("status", "pending"), data.get("tx_hash"))

    async def status(self, payment_id) -> PayResult:
        data = await self._run(["payment", "a2a-pay", "status", "--payment-id", payment_id])
        return PayResult(payment_id, data.get("status", "pending"), data.get("tx_hash"))


def make_rail() -> A2aPayRail:
    """Profile-appropriate rail: real CLI in prod, deterministic stub in dev."""
    from thehouse.core.config import settings

    return OnchainOsA2aPay() if settings.profile == "prod" else DevA2aPay()
