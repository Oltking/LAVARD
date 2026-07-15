"""A simulated target ASP exposed as a real MCP JSON-RPC server over HTTP (ASGI).

Phase 6's demo criterion needs one real MCP tool call on the wire; this app speaks the
actual protocol (tools/call, content blocks, structuredContent) so TheHouse's McpHttpCaller
is exercised end-to-end. Swapping to a live OKX.AI ASP is config-only (QUESTIONS.md Q12).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from thehouse.tests.sim_asps import PRICES, _NUMBERED, _answer_one


def build_sim_asp_app() -> FastAPI:
    app = FastAPI()
    app.state.calls = []  # every tools/call the "target" served, for assertions
    app.state.payments_received = []  # payment headers seen by the paid endpoint

    @app.post("/mcp/news_ai")
    async def news_ai(request: Request) -> dict[str, Any]:
        body = await request.json()
        app.state.calls.append(("news_ai", body))
        prompt = str(body["params"]["arguments"].get("query", ""))
        numbered = _NUMBERED.findall(prompt)
        if numbered:
            text = "\n".join(f"{n}) {_answer_one(q)}" for n, q in numbered)
        else:
            text = _answer_one(prompt)
        return _result(body, {"content": [{"type": "text", "text": text}], "isError": False})

    @app.post("/mcp/price_ai")
    async def price_ai(request: Request) -> dict[str, Any]:
        body = await request.json()
        app.state.calls.append(("price_ai", body))
        args = body["params"]["arguments"]
        symbols = args.get("symbols") or ([args["symbol"]] if "symbol" in args else [])
        payload = {s: PRICES.get(s) for s in symbols}
        return _result(
            body,
            {
                "content": [{"type": "text", "text": str(payload)}],
                "structuredContent": payload,
                "isError": False,
            },
        )

    @app.post("/mcp/paid_news_ai")
    async def paid_news_ai(request: Request):
        """Same LLM sim, but gated by an x402 challenge — exercises TheHouse's buyer face."""
        from fastapi import Response

        from thehouse.onchain.payments import PAYMENT_SIGNATURE_HEADER, build_challenge

        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            return Response(
                status_code=402,
                headers={"PAYMENT-REQUIRED": build_challenge(str(request.url), 1.0, "0xTARGET")},
            )
        app.state.payments_received.append(request.headers[PAYMENT_SIGNATURE_HEADER])
        return await news_ai(request)

    @app.post("/mcp/flaky_ai")
    async def flaky_ai(request: Request) -> dict[str, Any]:
        body = await request.json()
        app.state.calls.append(("flaky_ai", body))
        return {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000, "message": "boom"}}

    return app


def _result(body: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
