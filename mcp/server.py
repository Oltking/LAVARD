"""LAVARD's MCP surface (Phase 9): the tools LAVARD exposes so it is callable from any
MCP host (Claude Code / Cursor / Codex / Hermes) exactly like any other OKX.AI ASP.

Design: pure-stdlib, dependency-free tool registry with a minimal JSON-RPC dispatch
(`tools/list`, `tools/call`) that mirrors the MCP wire shape. When the official Python `mcp`
SDK is present it can wrap this registry directly (each entry already carries a JSON-Schema
input); until then the registry is exercised offline and by the CLI/tests. Every tool returns
JSON-serializable data and delegates straight into `core/` — no business logic lives here.

See docs/vendor/mcp/mcp.md and docs/vendor/okxai/skills-format.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        """MCP `tools/list` shape (no handler)."""
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


# --- Tool handlers (thin adapters over core/) -------------------------------------------------

def _submit_goal(goal: str, owner: str = "default-owner") -> dict:
    from core.service import submit_goal
    return submit_goal(goal, owner_id=owner).to_dict()


def _get_job(job_id: str) -> dict:
    from core.service import get_job
    view = get_job(job_id)
    if view is None:
        raise ValueError(f"Unknown job {job_id}")
    return view.to_dict()


def _get_report(job_id: str) -> dict:
    from core.governance import build_report
    return build_report(job_id)


def _hire(job_id: str) -> list[dict]:
    from core.foreman import hire_for_job
    return [o.to_dict() for o in hire_for_job(job_id)]


def _run_room(job_id: str, demo: bool = True) -> dict:
    from core.room import run_room
    return run_room(job_id, demo=demo).to_dict()


def _approve_action(description: str, type: str = "spend", amount_usd: float = 0.0,
                    target: str = "") -> dict:
    from core.governance import Action, review_action
    return review_action(
        Action(type, description, amount_usd=amount_usd, target=target)).to_dict()


def _kill_switch(job_id: str) -> dict:
    from core.store import get_store
    get_store().freeze_room(job_id)
    return {"job_id": job_id, "frozen": True}


REGISTRY: dict[str, Tool] = {
    t.name: t for t in [
        Tool("submit_goal",
             "Submit a plain-language goal for LAVARD to verify, decompose, and orchestrate.",
             _obj({"goal": {"type": "string"},
                   "owner": {"type": "string", "default": "default-owner"}}, ["goal"]),
             _submit_goal),
        Tool("get_job_status",
             "Get a job's verified intake + task graph by id.",
             _obj({"job_id": {"type": "string"}}, ["job_id"]), _get_job),
        Tool("get_job_report",
             "Per-job report: hires, cost, memory reuse, savings, and the verified audit log.",
             _obj({"job_id": {"type": "string"}}, ["job_id"]), _get_report),
        Tool("hire_crew",
             "Necessity-test, vet, and hire specialist agents for a job via A2A escrow.",
             _obj({"job_id": {"type": "string"}}, ["job_id"]), _hire),
        Tool("run_room",
             "Run the controller-mediated room + first-responder loop + referee for a job.",
             _obj({"job_id": {"type": "string"}, "demo": {"type": "boolean", "default": True}},
                  ["job_id"]), _run_room),
        Tool("approve_action",
             "Get an Action Review verdict for a proposed action before it executes.",
             _obj({"description": {"type": "string"}, "type": {"type": "string", "default": "spend"},
                   "amount_usd": {"type": "number", "default": 0.0},
                   "target": {"type": "string", "default": ""}}, ["description"]), _approve_action),
        Tool("kill_switch",
             "Engage the global kill-switch: freeze a job's room at the next turn boundary.",
             _obj({"job_id": {"type": "string"}}, ["job_id"]), _kill_switch),
    ]
}


def list_tools() -> list[dict]:
    """MCP `tools/list` result."""
    return [t.spec() for t in REGISTRY.values()]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """MCP `tools/call` — dispatch by name."""
    tool = REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'")
    return tool.handler(**(arguments or {}))


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    """Minimal JSON-RPC 2.0 dispatch for `tools/list` and `tools/call`."""
    rpc_id = request.get("id")
    method = request.get("method")
    try:
        if method == "tools/list":
            result: Any = {"tools": list_tools()}
        elif method == "tools/call":
            params = request.get("params", {})
            result = {"content": call_tool(params.get("name"), params.get("arguments"))}
        else:
            return {"jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    except Exception as e:  # surface as a JSON-RPC error, never crash the server loop
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"}}
