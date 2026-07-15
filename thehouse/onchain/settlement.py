"""Deferred-settlement reconciliation (aggr_deferred).

The facilitator batches signed authorizations and settles them asynchronously: a `/settle`
200 only means *accepted for batching* (empty txHash). Money lands later, confirmed via
`/settle/status`. So a batched settlement row is born `accepted`, gets a batch txHash when the
facilitator assigns one, and only reaches `settled`/`failed` once the batch lands on-chain.

This module owns that lifecycle:
- `record_deferred` — write an inbound/outbound settlement row in the `accepted` state.
- `attach_txhash` — record the batch txHash the facilitator later reports for a row.
- `SettlementReconciler.poll_once` — advance every non-terminal row that has a txHash by
  polling `/settle/status`, flip it to `settled`/`failed`, and audit a `settlement_failed`
  event on failure so lost money is never silent.

"delivered" (service rendered) is deliberately decoupled from "settled" (money on-chain):
the gateway serves on a valid verify + replay-claim; this loop closes the money afterward.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import now_ms
from thehouse.core.storage.db import audit_log, settlements
from thehouse.onchain.payments import XLAYER_NETWORK

_TERMINAL = ("settled", "failed")
_POLL_INTERVAL_S = 5.0
logger = logging.getLogger("thehouse.settlement")


@dataclass
class ReconcilePass:
    checked: int = 0
    settled: int = 0
    failed: int = 0
    still_pending: int = 0


class SettlementReconciler:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self._poller: asyncio.Task | None = None

    def start_poller(self, facilitator: Any, interval_s: float = _POLL_INTERVAL_S) -> None:
        """Background loop that advances non-terminal deferred settlements off /settle/status.
        Analogous to the aggregator's window sweeper — one poll per interval, never blocking."""
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.poll_once(facilitator)
                except Exception:
                    logger.exception("settlement poll failed; continuing")

        self._poller = asyncio.create_task(loop())

    async def stop_poller(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
            self._poller = None

    async def record_deferred(
        self,
        direction: str,
        counterparty: str,
        amount_usdt: float,
        *,
        scheme: str = "aggr_deferred",
        request_id: str | None = None,
        batch_id: str | None = None,
        txhash: str | None = None,
    ) -> int:
        """Write a batched settlement row in the `accepted` state; returns its id."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                insert(settlements).values(
                    ts_ms=now_ms(),
                    direction=direction,
                    counterparty=counterparty,
                    amount_usdt=round(amount_usdt, 6),
                    scheme=scheme,
                    network=XLAYER_NETWORK,
                    tx_ref=None,
                    request_id=request_id,
                    batch_id=batch_id,
                    settle_status="accepted",
                    settle_txhash=txhash,
                )
            )
            return int(result.inserted_primary_key[0])

    async def attach_txhash(self, settlement_id: int, txhash: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(settlements)
                .where(settlements.c.id == settlement_id)
                .values(settle_txhash=txhash)
            )

    async def _audit(self, conn: Any, event: str, payload: dict) -> None:
        await conn.execute(
            insert(audit_log).values(
                ts_ms=now_ms(), event=event, payload=json.dumps(payload, separators=(",", ":"))
            )
        )

    async def poll_once(self, facilitator: Any) -> ReconcilePass:
        """Advance every non-terminal settlement that has a batch txHash."""
        result = ReconcilePass()
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(settlements.c.id, settlements.c.settle_txhash, settlements.c.amount_usdt,
                       settlements.c.counterparty, settlements.c.direction)
                .where(settlements.c.settle_status.notin_(_TERMINAL))
                .where(settlements.c.settle_txhash.isnot(None))
            )).mappings().all()

        for row in rows:
            result.checked += 1
            status = await facilitator.settle_status(row["settle_txhash"])
            if not status.terminal:
                result.still_pending += 1
                continue
            new_status = "settled" if status.landed else "failed"
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(settlements)
                    .where(settlements.c.id == row["id"])
                    .values(settle_status=new_status,
                            tx_ref=status.transaction or row["settle_txhash"])
                )
                if new_status == "settled":
                    result.settled += 1
                else:
                    result.failed += 1
                    logger.warning(
                        "ALERT settlement_failed id=%s txhash=%s %s %s %.6f",
                        row["id"], row["settle_txhash"], row["direction"],
                        row["counterparty"], row["amount_usdt"])
                    await self._audit(conn, "settlement_failed", {
                        "settlement_id": row["id"],
                        "txhash": row["settle_txhash"],
                        "direction": row["direction"],
                        "counterparty": row["counterparty"],
                        "amount_usdt": row["amount_usdt"],
                    })
        return result


def build_settlement_service(engine: AsyncEngine) -> "tuple[SettlementReconciler, Any] | None":
    """Assemble (reconciler, facilitator) when OKX credentials are configured; else None so the
    caller stays on the dev/immediate rails. The facilitator is the real settlement backend."""
    from thehouse.onchain.facilitator import OnchainOSFacilitator

    facilitator = OnchainOSFacilitator.from_settings()
    if facilitator is None:
        return None
    return SettlementReconciler(engine), facilitator
