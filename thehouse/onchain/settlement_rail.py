"""RailSettlement — moves TheHouse's money through the proven a2a-pay rail and records it.

Two faces (see thehouse/onchain/a2a_pay.py):
- `record_inbound` (SELLER): TheHouse creates an a2a charge (payTo = house wallet) for a caller's
  charged amount and writes an inbound settlement row (status "pending" until the caller pays). The
  payment_id lives in `tx_ref`; the on-chain hash lands in `settle_txhash` once confirmed.
- `pay_target` (BUYER): TheHouse pays a target's charge and records the outbound settlement with
  the real on-chain tx_hash (this is the flow proven live on mainnet).

`reconcile_pending` polls a2a-pay `status` to advance pending settlements → settled|failed with the
tx_hash, mirroring the facilitator poller. Alerts (logger) on failure so lost money is never silent.

Offline: `DevA2aPay` makes create/pay/status deterministic, so the whole path is testable with no
CLI, chain, or creds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import now_ms
from thehouse.core.storage.db import audit_log, settlements
from thehouse.onchain.a2a_pay import A2aPayRail, PayResult
from thehouse.onchain.payments import XLAYER_NETWORK

logger = logging.getLogger("thehouse.settlement_rail")
_TERMINAL = ("settled", "failed")


@dataclass
class ReconcilePass:
    checked: int = 0
    settled: int = 0
    failed: int = 0
    pending: int = 0


class RailSettlement:
    def __init__(self, engine: AsyncEngine, rail: A2aPayRail, house_wallet: str,
                 token_symbol: str = "USDT", chain: str = "xlayer"):
        self.engine = engine
        self.rail = rail
        self.house_wallet = house_wallet
        self.token_symbol = token_symbol
        self.chain = chain
        self._poller: asyncio.Task | None = None

    def start_poller(self, interval_s: float = 5.0) -> None:
        """Background loop advancing pending a2a settlements via status (mirrors the aggregator sweeper)."""
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.reconcile_pending()
                except Exception:
                    logger.exception("a2a settlement reconcile failed; continuing")

        self._poller = asyncio.create_task(loop())

    async def stop_poller(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
            self._poller = None

    async def _insert(self, *, direction, counterparty, amount, payment_id, tx_hash, status,
                      request_id=None, batch_id=None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(settlements).values(
                ts_ms=now_ms(), direction=direction, counterparty=counterparty,
                amount_usdt=round(amount, 6), scheme="a2a_pay", network=XLAYER_NETWORK,
                tx_ref=payment_id, request_id=request_id, batch_id=batch_id,
                settle_status=status, settle_txhash=tx_hash))

    async def record_inbound(self, caller_id: str, amount_decimal: float, request_id: str,
                             batch_id: str | None = None) -> str:
        """SELLER face: create the caller's charge (payTo = house) and record it pending.
        Returns the payment_id the caller must pay."""
        charge = await self.rail.create_charge(
            str(amount_decimal), self.token_symbol, self.house_wallet, chain=self.chain,
            external_id=request_id)
        await self._insert(direction="in", counterparty=caller_id, amount=amount_decimal,
                           payment_id=charge.payment_id, tx_hash=None, status="pending",
                           request_id=request_id, batch_id=batch_id)
        return charge.payment_id

    async def pay_target(self, target_wallet: str, payment_id: str, raw_amount: str, currency: str,
                         amount_decimal: float, request_id: str | None = None) -> PayResult:
        """BUYER face: pay a target's charge; record the outbound settlement with the tx_hash."""
        res = await self.rail.pay(payment_id, raw_amount, currency, target_wallet)
        status = "settled" if res.completed else ("failed" if res.status == "failed" else "pending")
        await self._insert(direction="out", counterparty=target_wallet, amount=amount_decimal,
                           payment_id=payment_id, tx_hash=res.tx_hash, status=status,
                           request_id=request_id)
        if res.status == "failed":
            logger.warning("ALERT settlement_failed (outbound) payment_id=%s target=%s amt=%.6f",
                           payment_id, target_wallet, amount_decimal)
        return res

    async def reconcile_pending(self) -> ReconcilePass:
        """Advance pending a2a settlements via `status` → settled|failed with the on-chain hash."""
        result = ReconcilePass()
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(settlements.c.id, settlements.c.tx_ref, settlements.c.direction,
                       settlements.c.counterparty, settlements.c.amount_usdt)
                .where(settlements.c.scheme == "a2a_pay")
                .where(settlements.c.settle_status.notin_(_TERMINAL))
                .where(settlements.c.tx_ref.isnot(None))
            )).mappings().all()

        for row in rows:
            result.checked += 1
            st = await self.rail.status(row["tx_ref"])
            if not st.terminal:
                result.pending += 1
                continue
            new_status = "settled" if st.completed else "failed"
            async with self.engine.begin() as conn:
                await conn.execute(update(settlements).where(settlements.c.id == row["id"])
                                   .values(settle_status=new_status, settle_txhash=st.tx_hash))
                if new_status == "settled":
                    result.settled += 1
                else:
                    result.failed += 1
                    logger.warning("ALERT settlement_failed payment_id=%s %s amt=%.6f",
                                   row["tx_ref"], row["counterparty"], row["amount_usdt"])
                    await conn.execute(insert(audit_log).values(
                        ts_ms=now_ms(), event="settlement_failed",
                        payload=f'{{"payment_id":"{row["tx_ref"]}","direction":"{row["direction"]}"}}'))
        return result


# One RailSettlement per engine, so the reconcile poller and the settlement writer share the same
# rail instance (in dev, the DevA2aPay in-memory state; audit LOW-6). In prod the shared state is
# the real backend, so this only matters for the deterministic dev path.
_rail_cache: "dict[int, RailSettlement]" = {}


def build_rail_settlement(engine: AsyncEngine) -> "RailSettlement | None":
    """Construct (or reuse) a RailSettlement when the a2a rail is selected; else None.

    DIRECTION MODEL (audit MEDIUM-4): a2a-pay is the BUYER rail — TheHouse paying targets
    (`pay_target`). The SELLER direction (callers paying TheHouse) settles the caller's signed
    x402 authorization and is recorded directly by the pipeline, NOT here. `record_inbound` remains
    for true a2a-seller flows where TheHouse creates a charge the caller then pays."""
    from thehouse.core.config import settings
    from thehouse.onchain.a2a_pay import make_rail

    if settings.settlement_rail != "a2a_pay":
        return None
    key = id(engine)
    if key not in _rail_cache:
        house = settings.facilitator_pay_to or "0xTHEHOUSE"
        _rail_cache[key] = RailSettlement(engine, make_rail(), house,
                                          settings.settlement_token_symbol, settings.settlement_chain)
    return _rail_cache[key]
