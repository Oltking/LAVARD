"""A2A-pay settlement rail (the proven on-chain mover) — dev-stub end-to-end.

Mirrors the live mainnet flow (tx 0xa65fd720…): create charge → pay (raw amount) → status
completed with tx_hash. Verifies TheHouse's seller (inbound) and buyer (outbound) faces record
real settlements, and that reconciliation advances pending → settled via status.
"""

from sqlalchemy import select

from thehouse.core.storage.db import settlements
from thehouse.onchain.a2a_pay import DevA2aPay
from thehouse.onchain.settlement_rail import RailSettlement


async def test_dev_rail_create_pay_status():
    rail = DevA2aPay()
    charge = await rail.create_charge("0.01", "USDT", "0xSELLER", chain="xlayer")
    assert charge.raw_amount == "10000"                 # 0.01 * 1e6, matching the live check
    res = await rail.pay(charge.payment_id, charge.raw_amount, charge.currency, "0xSELLER")
    assert res.completed and res.tx_hash and res.tx_hash.startswith("0x")
    st = await rail.status(charge.payment_id)
    assert st.completed and st.tx_hash == res.tx_hash


async def test_amount_mismatch_fails_like_live():
    rail = DevA2aPay()
    charge = await rail.create_charge("0.01", "USDT", "0xSELLER", chain="xlayer")
    # paying a wrong raw amount fails (mirrors "amount mismatch" from the live CLI)
    res = await rail.pay(charge.payment_id, "99999", charge.currency, "0xSELLER")
    assert res.status == "failed"


async def test_seller_inbound_charge_recorded_pending(engine):
    rs = RailSettlement(engine, DevA2aPay(), house_wallet="0xTHEHOUSE")
    pid = await rs.record_inbound("0xCALLER", 0.8, request_id="r1", batch_id="b1")
    async with engine.connect() as conn:
        row = (await conn.execute(select(settlements).where(settlements.c.request_id == "r1"))).mappings().one()
    assert row["direction"] == "in" and row["counterparty"] == "0xCALLER"
    assert row["amount_usdt"] == 0.8 and row["scheme"] == "a2a_pay"
    assert row["tx_ref"] == pid and row["settle_status"] == "pending"


async def test_buyer_outbound_pay_records_txhash(engine):
    rail = DevA2aPay()
    rs = RailSettlement(engine, rail, house_wallet="0xTHEHOUSE")
    charge = await rail.create_charge("1.00", "USDT", "0xTARGET", chain="xlayer")   # target's charge
    res = await rs.pay_target("0xTARGET", charge.payment_id, charge.raw_amount, charge.currency,
                              1.0, request_id="r2")
    assert res.completed
    async with engine.connect() as conn:
        row = (await conn.execute(select(settlements).where(settlements.c.request_id == "r2"))).mappings().one()
    assert row["direction"] == "out" and row["settle_status"] == "settled"
    assert row["settle_txhash"] == res.tx_hash and row["counterparty"] == "0xTARGET"


async def test_reconcile_advances_paid_inbound_to_settled(engine):
    rail = DevA2aPay()
    rs = RailSettlement(engine, rail, house_wallet="0xTHEHOUSE")
    pid = await rs.record_inbound("0xCALLER", 0.8, request_id="r3")
    # caller pays the charge out-of-band
    await rail.pay(pid, "800000", "0xDEVUSDT", "0xTHEHOUSE")
    rec = await rs.reconcile_pending()
    assert rec.settled == 1
    async with engine.connect() as conn:
        row = (await conn.execute(select(settlements).where(settlements.c.request_id == "r3"))).mappings().one()
    assert row["settle_status"] == "settled" and row["settle_txhash"].startswith("0x")
