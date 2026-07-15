"""The real onchainos CLI adapter must fail LOUDLY (never fabricate) when the CLI/creds are
absent or the output shape is unexpected."""

import pytest

from onchain.onchainos_cli import OnchainOsCli, OnchainOsError, _require


def test_preflight_raises_when_binary_missing():
    cli = OnchainOsCli(binary="definitely-not-a-real-binary-xyz")
    assert cli.available() is False
    with pytest.raises(OnchainOsError, match="not found"):
        cli.preflight()


def test_require_raises_actionable_on_shape_drift():
    with pytest.raises(OnchainOsError, match="expected"):
        _require({"foo": 1}, ("agentId", "name"), "agent search")


def test_require_passes_when_keys_present():
    _require({"agentId": "x", "name": "y"}, ("agentId", "name"), "agent search")  # no raise


def test_live_marketplace_backend_raises_without_cli(monkeypatch):
    # With no onchainos binary, the live backend must raise (loud), not return fake candidates.
    from onchain.marketplace import OnchainOsMarketplace
    monkeypatch.setenv("LAVARD_ONCHAINOS_BIN", "definitely-not-a-real-binary-xyz")
    import onchain.onchainos_cli as mod
    mod._cli = None  # reset singleton so it picks up the patched binary
    with pytest.raises(OnchainOsError):
        OnchainOsMarketplace().search_candidates("security")
    mod._cli = None


# --- LIVE-VERIFIED mapping: the real `agent search` payload (captured 2026-07-12) maps correctly.
_REAL_AGENT = {
    "agentId": "3811", "name": "CA X-Ray", "feedbackRate": 100.0, "securityRate": 5.0,
    "soldCount": 5, "serviceMinPrice": 0.2, "chainIndex": 196,
    "communicationAddress": "0x2F348309301Bcfd0b9914d2Ae268F64912C4BF97",
    "services": [
        {"serviceId": "26339", "serviceName": "合约安全初筛", "serviceType": "A2A", "feeAmount": 0.2},
        {"serviceId": "26341", "serviceName": "合约深度风险报告", "serviceType": "A2A", "feeAmount": 1.0},
    ],
}


def test_marketplace_maps_real_search_payload():
    from onchain.marketplace import OnchainOsMarketplace
    listing = OnchainOsMarketplace._to_listing(_REAL_AGENT, "security")
    assert listing.agent_id == "3811"
    assert listing.name == "CA X-Ray"
    assert listing.mode == "a2a"                       # serviceType A2A
    assert listing.price_usd == 0.2                    # serviceMinPrice
    assert listing.reputation.score == 100.0           # feedbackRate
    assert listing.reputation.jobs_completed == 5      # soldCount
    assert listing.identity.wallets[0].chain == "xlayer"   # chainIndex 196
    assert listing.identity.wallets[0].address.startswith("0x2F34")


def test_marketplace_price_falls_back_to_min_fee():
    agent = dict(_REAL_AGENT); agent["serviceMinPrice"] = None
    from onchain.marketplace import OnchainOsMarketplace
    listing = OnchainOsMarketplace._to_listing(agent, "security")
    assert listing.price_usd == 0.2                    # min of service feeAmounts (0.2, 1.0)


def test_run_takes_last_json_line_over_account_init(monkeypatch):
    # The CLI emits an account-init line before the result; run() must return the result's data.
    from onchain.onchainos_cli import OnchainOsCli
    cli = OnchainOsCli()
    monkeypatch.setattr(cli, "preflight", lambda: None)
    two_lines = ('{"ok":true,"data":{"accountId":"a87","isNew":true}}\n'
                 '{"ok":true,"data":{"list":[{"agentId":"1"}],"total":30}}')
    monkeypatch.setattr(cli, "_run", lambda args: two_lines)
    out = cli.run(["agent", "search"])
    assert out["total"] == 30 and out["list"][0]["agentId"] == "1"
