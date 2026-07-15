"""Prod seller-face verifier (FacilitatorPaymentVerifier) through the /mcp gateway.

The caller pays with a base64 x402 paymentPayload; the gateway validates it through the
facilitator's /verify (faked here) before rendering service, and a valid authorization is
spendable exactly once.
"""

import base64
import json

import httpx

from thehouse.core.dispatcher.service import Dispatcher, McpHttpCaller
from thehouse.core.models import Transport
from thehouse.core.service import AggregatorService
from thehouse.onchain.payments import (
    FacilitatorPaymentVerifier,
    SettlementLedger,
)
from thehouse.gateway.mcp_server import build_gateway_app
from thehouse.tests.conftest import seed_asp
from thehouse.tests.sim_asps import LLM_SCHEMA
from thehouse.tests.sim_mcp_server import build_sim_asp_app


class FakeFacilitator:
    """Stands in for OnchainOSFacilitator: accepts any payload whose amount covers the price."""

    def __init__(self):
        self.verify_calls = []

    async def verify(self, x402_version, payment_payload, payment_requirements):
        from thehouse.onchain.facilitator import VerifyResult

        self.verify_calls.append((payment_payload, payment_requirements))
        accepted = payment_payload.get("accepted", {})
        ok = int(accepted.get("amount", 0)) >= int(payment_requirements["amount"])
        return VerifyResult(is_valid=ok, payer=accepted.get("from", "0xCALLER"),
                            invalid_reason=None if ok else "requirements_mismatch")


def _payload(amount_base_units: str, payer: str = "0xCALLER") -> str:
    return base64.b64encode(json.dumps({
        "x402Version": 2,
        "accepted": {"scheme": "aggr_deferred", "amount": amount_base_units, "from": payer},
    }).encode()).decode()


async def _paid_call(client, header):
    return await client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "news_ai.get_news", "arguments": {"query": "current date"}},
    }, headers={"PAYMENT-SIGNATURE": header})


async def test_facilitator_verifier_gates_service(engine, redis):
    await seed_asp(engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
                   break_even_batch_size=1)
    sim = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_sim_asp_app()),
                            base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=sim)}))
    fac = FakeFacilitator()
    verifier = FacilitatorPaymentVerifier(fac, pay_to="0xTHEHOUSE")
    gw = build_gateway_app(agg, verifier, SettlementLedger(engine))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw),
                                 base_url="http://thehouse") as c:
        # price is 0.80 → 800000 base units; a covering payload passes verify and is served
        good = await _paid_call(c, _payload("1000000"))
        assert good.status_code == 200 and "result" in good.json()
        assert fac.verify_calls  # verify was actually consulted

        # underpaying → facilitator rejects → 402 challenge, no service
        low = await _paid_call(c, _payload("100000"))
        assert low.status_code == 402


async def test_valid_authorization_spendable_once(engine, redis):
    await seed_asp(engine, tool_schema=LLM_SCHEMA, endpoint="http://sim/mcp/news_ai",
                   break_even_batch_size=1)
    sim = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_sim_asp_app()),
                            base_url="http://sim")
    agg = AggregatorService(engine, redis, Dispatcher({Transport.MCP: McpHttpCaller(client=sim)}))
    gw = build_gateway_app(agg, FacilitatorPaymentVerifier(FakeFacilitator(), pay_to="0xTHEHOUSE"),
                           SettlementLedger(engine))
    header = _payload("1000000")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw),
                                 base_url="http://thehouse") as c:
        first = await _paid_call(c, header)
        assert first.status_code == 200
        # identical bytes = replay → refused
        replay = await _paid_call(c, header)
        assert replay.status_code == 402
