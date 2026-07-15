"""OKX x402 facilitator client (docs/xlayer_integration.md §4).

Covers: OK-ACCESS signing shape, the {code,msg,data} envelope, business-code failures, and the
CRITICAL deferred semantics — a settle 200 is *accepted*, not settled; only /settle/status lands.
"""

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from thehouse.onchain.facilitator import (
    FacilitatorError,
    OnchainOSFacilitator,
    sign,
)

CREDS = dict(api_key="ak", secret_key="sk", passphrase="pp")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://web3.okx.com")


def test_signature_matches_prehash_formula():
    ts = "2026-07-14T10:20:30.123Z"
    got = sign("sk", ts, "POST", "/api/v6/pay/x402/settle", '{"a":1}')
    want = base64.b64encode(
        hmac.new(b"sk", f"{ts}POST/api/v6/pay/x402/settle{{\"a\":1}}".encode(), hashlib.sha256).digest()
    ).decode()
    assert got == want


async def test_verify_sends_signed_headers_and_parses_envelope():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"code": "0", "msg": "success",
                                         "data": {"isValid": True, "payer": "0xA"}})

    fac = OnchainOSFacilitator(client=_client(handler), **CREDS)
    res = await fac.verify(2, {"accepted": {}}, {"scheme": "aggr_deferred"})

    assert res.is_valid and res.payer == "0xA"
    # every auth header present
    for h in ("ok-access-key", "ok-access-sign", "ok-access-passphrase", "ok-access-timestamp"):
        assert h in captured["headers"]
    # signature is reproducible from the exact wire body + timestamp it sent
    expect = sign("sk", captured["headers"]["ok-access-timestamp"], "POST",
                  captured["path"], captured["body"])
    assert captured["headers"]["ok-access-sign"] == expect


async def test_settle_acceptance_is_not_settlement():
    def handler(request):
        return httpx.Response(200, json={"code": "0", "msg": "success",
                                         "data": {"success": True, "status": "success",
                                                  "transaction": "", "payer": "0xA"}})

    fac = OnchainOSFacilitator(client=_client(handler), **CREDS)
    res = await fac.settle(2, {"accepted": {}}, {"scheme": "aggr_deferred"})
    assert res.accepted is True
    assert res.transaction == ""          # not on-chain yet — no txHash


async def test_settle_status_terminal_transitions():
    seq = iter(["pending", "success"])

    def handler(request):
        assert request.url.params.get("txHash") == "0xBATCH"
        return httpx.Response(200, json={"code": "0", "msg": "ok",
                                         "data": {"status": next(seq), "transaction": "0xBATCH"}})

    fac = OnchainOSFacilitator(client=_client(handler), **CREDS)
    first = await fac.settle_status("0xBATCH")
    assert first.status == "pending" and not first.terminal
    second = await fac.settle_status("0xBATCH")
    assert second.landed and second.terminal


async def test_business_code_raises():
    def handler(request):
        return httpx.Response(200, json={"code": "50111", "msg": "invalid signature", "data": {}})

    fac = OnchainOSFacilitator(client=_client(handler), **CREDS)
    with pytest.raises(FacilitatorError, match="50111"):
        await fac.supported()


async def test_missing_credentials_fail_fast():
    fac = OnchainOSFacilitator(client=_client(lambda r: httpx.Response(200)),
                               api_key="", secret_key="", passphrase="")
    assert fac.credentialed() is False
    with pytest.raises(FacilitatorError, match="credentials missing"):
        await fac.supported()


def test_from_settings_returns_none_without_creds():
    # dev profile has blank OKX_* → no facilitator constructed
    assert OnchainOSFacilitator.from_settings() is None
