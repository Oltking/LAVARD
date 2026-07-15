"""TheHouse's own MCP server surface (spec §8 `mcp/`; see QUESTIONS.md Q13 for the name).

Callers speak standard MCP JSON-RPC over HTTP: `tools/list` mirrors every active target ASP
in the registry (same inputSchema the target advertises — callers switch by changing only
the endpoint); `tools/call` is gated by the OKX Agent Payments Protocol: no payment header →
HTTP 402 with an x402 challenge at the TheHouse price; valid payment → the request enters the
aggregation pipeline and the response returns when its batch settles.

The payer wallet from the payment receipt is the caller identity (QUESTIONS.md Q10);
in dev profile a `caller_id` argument is also accepted.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from fastapi import FastAPI, Request, Response

from thehouse.core.intake.service import QueueFullError, RateLimitedError
from thehouse.core.service import AggregatorService
from thehouse.onchain.payments import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    PaymentVerifier,
    SettlementLedger,
    build_challenge,
    replay_key,
    to_base_units,
)

RESULT_POLL_S = 0.02
RESULT_TIMEOUT_S = 5.0


def build_gateway_app(
    aggregator: AggregatorService,
    verifier: PaymentVerifier,
    ledger: SettlementLedger,
    wallet_address: str = "0xTHEHOUSE",
    dev_allow_unpaid: bool = False,
    mount_on: FastAPI | None = None,
) -> FastAPI:
    """Standalone gateway app — or, with `mount_on`, register POST /mcp directly on an
    existing app (core.api uses this so one deployment serves every surface)."""
    app = mount_on if mount_on is not None else FastAPI(title="TheHouse MCP Gateway")

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        body = await request.json()
        method = body.get("method")
        if method == "initialize":
            return _ok(body, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "TheHouse", "version": "0.1.0"},
            })
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "tools/list":
            return _ok(body, {"tools": await _tools(aggregator)})
        if method == "tools/call":
            return await _call(request, body)
        return _err(body, -32601, f"Method not found: {method}")

    async def _call(request: Request, body: dict[str, Any]) -> Response:
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = dict(params.get("arguments", {}))
        asp_id = tool_name.split(".", 1)[0]

        entry = await aggregator.registry.get(asp_id)
        if entry is None or not entry.active or tool_name != entry.tool_name:
            # validated BEFORE the payment is touched — a bad tool name must never
            # consume an authorization
            return _err(body, -32602, f"Unknown tool: {tool_name}")

        from thehouse.core.pricing import ceiling_price

        priority = bool(arguments.pop("priority", False))
        # Model B: authorize the CEILING (full price). The actual charge is the fire-time tier
        # (solo ~full, batched −20%), settled ≤ this ceiling by the pipeline. The caller can never
        # be charged more than they authorize here, and there is no refund rail.
        price = ceiling_price(entry, priority=priority)

        caller_id = arguments.pop("caller_id", None)
        payment = request.headers.get(PAYMENT_SIGNATURE_HEADER)
        key = None
        if payment:
            receipt = verifier.verify(payment, price)
            if inspect.isawaitable(receipt):
                receipt = await receipt
            if receipt is None:
                return _payment_required(request, price)
            # replay protection: an authorization is spendable exactly once — real x402
            # authorizations are unique per payment, so identical bytes are a replay
            key = replay_key(payment)
            if not await ledger.try_register_key(key):
                return _payment_required(request, price)
            caller_id = receipt.payer
        elif dev_allow_unpaid and caller_id:
            receipt = None  # dev convenience: explicit caller_id, no payment rail
        else:
            return _payment_required(request, price)

        try:
            req = await aggregator.submit(asp_id, tool_name, arguments, caller_id, priority)
        except (RateLimitedError, QueueFullError) as e:
            # refused before any service was rendered: release the authorization so the
            # caller's honest retry can spend it — nothing was charged
            if key is not None:
                await ledger.release_key(key)
            code = -32029 if isinstance(e, RateLimitedError) else -32030
            reason = (
                "rate limit exceeded — slow down and retry"
                if isinstance(e, RateLimitedError)
                else "target queue at capacity — retry shortly"
            )
            return _err(body, code, f"{reason} (payment not consumed)")
        except Exception:
            # any unexpected failure before service was rendered must not burn the
            # authorization — release it so the caller's retry can spend it
            if key is not None:
                await ledger.release_key(key)
            raise
        # Inbound settlement is recorded by the pipeline at fire time, at the ACTUAL tier price
        # (Model B deferred settlement) — not here at the ceiling. The authorization is claimed
        # (replay key) so it is spent exactly once.

        res = await _await_result(req.request_id)
        if res is None:
            # payment is already collected and final — hand the caller the handle it
            # needs to retrieve the answer once its batch settles
            return _err(
                body, -32000,
                f"aggregation timeout — request {req.request_id} still pending; "
                f"retrieve it at GET /v1/result/{req.request_id}",
            )
        if res["status"] == "failed":
            return _err(body, -32001, f"request {req.request_id} failed: {res['result']}")
        return _ok(body, {
            "content": [{"type": "text", "text": res["result"]}],
            "isError": False,
        })

    def _payment_required(request: Request, price: float) -> Response:
        challenge = build_challenge(str(request.url), price, wallet_address)
        return Response(
            status_code=402,
            headers={PAYMENT_REQUIRED_HEADER: challenge},
            content=f'{{"error":"payment required","amount":"{to_base_units(price)}"}}',
            media_type="application/json",
        )

    async def _await_result(request_id: str) -> dict[str, Any] | None:
        """Poll until the request resolves (delivered / cached / failed) or times out."""
        deadline = asyncio.get_event_loop().time() + RESULT_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            res = await aggregator.get_result(request_id)
            if res:
                if res["status"] in ("delivered", "cached") and res["result"] is not None:
                    return res
                if res["status"] == "failed":
                    return res  # fail fast — no point burning the full poll window
                if res.get("merged_into"):
                    if res["result"] is not None:
                        return res
                    owner = await aggregator.get_result(res["merged_into"])
                    if (
                        owner
                        and owner["status"] in ("delivered", "cached")
                        and owner["result"] is not None
                    ):
                        return owner
            await aggregator.sweep_once()
            await asyncio.sleep(RESULT_POLL_S)
        return None

    return app


async def _tools(aggregator: AggregatorService) -> list[dict[str, Any]]:
    tools = []
    for entry in await aggregator.registry.list_all(active_only=True):
        schema = entry.tool_schema.get("inputSchema") or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }
        tools.append(
            {
                "name": entry.tool_name,
                "description": entry.description
                or f"{entry.asp_id} via TheHouse — {entry.mode.value}",
                "inputSchema": schema,
            }
        )
    return tools


def _ok(body: dict[str, Any], result: dict[str, Any]) -> Response:
    import json

    return Response(
        content=json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}),
        media_type="application/json",
    )


def _err(body: dict[str, Any], code: int, message: str) -> Response:
    import json

    return Response(
        content=json.dumps(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": code, "message": message}}
        ),
        media_type="application/json",
    )
