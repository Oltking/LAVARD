"""The background settlement poller advances deferred rows off /settle/status and stops cleanly."""

import asyncio

from sqlalchemy import select

from thehouse.core.storage.db import settlements
from thehouse.onchain.facilitator import SettlementStatus
from thehouse.onchain.settlement import SettlementReconciler, build_settlement_service


class OneShotFacilitator:
    async def settle_status(self, tx_hash: str) -> SettlementStatus:
        return SettlementStatus(status="success", transaction=tx_hash)


async def test_poller_advances_then_stops(engine):
    rec = SettlementReconciler(engine)
    sid = await rec.record_deferred("in", "0xC", 0.8, request_id="r1", txhash="0xB")

    rec.start_poller(OneShotFacilitator(), interval_s=0.01)
    # give the loop a few ticks to poll and flip the row
    for _ in range(50):
        await asyncio.sleep(0.01)
        async with engine.connect() as conn:
            st = (await conn.execute(
                select(settlements.c.settle_status).where(settlements.c.id == sid))).scalar()
        if st == "settled":
            break
    await rec.stop_poller()
    assert st == "settled"
    assert rec._poller is None


def test_build_settlement_service_none_without_creds():
    # dev profile → no OKX creds → no facilitator/reconciler
    assert build_settlement_service(engine=None) is None
