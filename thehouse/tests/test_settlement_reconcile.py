"""Deferred-settlement lifecycle: accepted -> settled|failed via /settle/status polling."""

from sqlalchemy import select

from thehouse.core.storage.db import audit_log, settlements
from thehouse.onchain.facilitator import SettlementStatus
from thehouse.onchain.settlement import SettlementReconciler


class ScriptedFacilitator:
    """Returns a scripted status per txHash on each poll."""

    def __init__(self, script: dict[str, list[str]]):
        self._script = {k: iter(v) for k, v in script.items()}

    async def settle_status(self, tx_hash: str) -> SettlementStatus:
        return SettlementStatus(status=next(self._script[tx_hash]), transaction=tx_hash)


async def test_accepted_row_is_not_terminal_until_batch_lands(engine):
    rec = SettlementReconciler(engine)
    sid = await rec.record_deferred("in", "0xCALLER", 0.8, request_id="r1", txhash="0xBATCH")

    fac = ScriptedFacilitator({"0xBATCH": ["pending", "success"]})

    p1 = await rec.poll_once(fac)
    assert p1.checked == 1 and p1.still_pending == 1 and p1.settled == 0
    async with engine.connect() as conn:
        st = (await conn.execute(
            select(settlements.c.settle_status).where(settlements.c.id == sid))).scalar()
    assert st == "accepted"

    p2 = await rec.poll_once(fac)
    assert p2.settled == 1
    async with engine.connect() as conn:
        row = (await conn.execute(
            select(settlements).where(settlements.c.id == sid))).mappings().one()
    assert row["settle_status"] == "settled" and row["tx_ref"] == "0xBATCH"


async def test_failed_batch_audits_and_never_silently_loses_money(engine):
    rec = SettlementReconciler(engine)
    await rec.record_deferred("out", "0xTARGET", 1.0, request_id="r2", txhash="0xBAD")
    fac = ScriptedFacilitator({"0xBAD": ["failed"]})

    p = await rec.poll_once(fac)
    assert p.failed == 1
    async with engine.connect() as conn:
        events = [r["event"] for r in (await conn.execute(select(audit_log))).mappings().all()]
    assert "settlement_failed" in events


async def test_rows_without_txhash_are_skipped(engine):
    rec = SettlementReconciler(engine)
    await rec.record_deferred("in", "0xC", 0.5, request_id="r3")  # no txhash yet
    fac = ScriptedFacilitator({})
    p = await rec.poll_once(fac)
    assert p.checked == 0  # nothing to poll until a batch txHash is attached
