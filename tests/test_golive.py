from mcp import build_listing, call_tool, dispatch, is_live, list_tools, publish, readiness_review
from mcp.server import REGISTRY


def test_mcp_surface_lists_expected_tools():
    names = {t["name"] for t in list_tools()}
    assert {"submit_goal", "get_job_report", "approve_action", "kill_switch"} <= names
    for spec in list_tools():
        assert spec["inputSchema"]["type"] == "object"  # every tool is JSON-Schema described


def test_call_tool_submit_goal_roundtrip():
    view = call_tool("submit_goal", {"goal": "Research rollups then write a post"})
    assert view["id"] and view["nodes"]
    report = call_tool("get_job_report", {"job_id": view["id"]})
    assert "audit_log" in report


def test_call_tool_unknown_raises():
    try:
        call_tool("nope", {})
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_dispatch_tools_list_and_call():
    listed = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed["result"]["tools"] and "error" not in listed

    called = dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "submit_goal", "arguments": {"goal": "Audit a contract"}}})
    assert called["result"]["content"]["id"]


def test_dispatch_errors_are_captured_not_raised():
    bad_method = dispatch({"jsonrpc": "2.0", "id": 3, "method": "does/notexist"})
    assert bad_method["error"]["code"] == -32601

    bad_call = dispatch({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "get_job_status", "arguments": {"job_id": "missing"}}})
    assert "error" in bad_call and bad_call["error"]["code"] == -32000


def test_readiness_review_all_green():
    review = readiness_review()
    assert review["ready"] is True
    assert review["verdict"] == "READY-TO-LIST"
    assert review["blocking"] == []


def test_readiness_review_blocks_on_missing_gate():
    listing = build_listing()
    listing["dispute"]["bounty_deposit_pct"] = 0  # arbitration terms missing
    review = readiness_review(listing)
    assert review["ready"] is False and "arbitration" in review["blocking"]


def test_publish_is_dry_run_offline():
    assert is_live() is False
    result = publish()
    assert result["published"] is False and result["mode"] == "dry-run"
    assert result["listing"]["mode"] == "agent-to-agent"


def test_listing_mcp_tools_match_registry():
    assert set(build_listing()["mcp_tools"]) == set(REGISTRY)
