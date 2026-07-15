"""DISPATCHER (spec §5.4): one tool call to the target ASP, exactly what any caller
would send. Timeout → one retry → dispatch failure (pipeline falls back to direct routing).

Transports:
- McpHttpCaller — JSON-RPC `tools/call` over Streamable HTTP (transport=mcp)
- PlainHttpCaller — POST JSON body to the endpoint (transport=http)
- tests inject in-process fakes implementing the same ToolCaller protocol.

Payment (the 402 sign-and-replay loop) attaches here in Phase 11 as a PaymentHook —
composition and splitting never see it.
"""

from __future__ import annotations

import itertools
from typing import Any, Awaitable, Callable, Protocol

import httpx

from thehouse.core.config import settings
from thehouse.core.models import ASPEntry, Transport
from thehouse.core.profiler.profiler import ToolCallResult

# Hook signature: given the outgoing request context, returns extra headers (e.g. the
# signed payment authorization header after a 402 challenge).
PaymentHook = Callable[[ASPEntry, httpx.Response], Awaitable[dict[str, str]]]


class ToolCaller(Protocol):
    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult: ...


class DispatchError(Exception):
    pass


class McpHttpCaller:
    """MCP JSON-RPC tools/call client (spec rev 2025-06-18, Streamable HTTP)."""

    _ids = itertools.count(1)

    def __init__(self, client: httpx.AsyncClient | None = None, payment_hook: PaymentHook | None = None):
        self.client = client or httpx.AsyncClient(timeout=settings.dispatch_timeout_ms / 1000)
        self.payment_hook = payment_hook

    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": entry.tool_name, "arguments": arguments},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = await self.client.post(entry.endpoint, json=payload, headers=headers)

        if resp.status_code == 402 and self.payment_hook is not None:
            headers |= await self.payment_hook(entry, resp)
            resp = await self.client.post(entry.endpoint, json=payload, headers=headers)

        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            return ToolCallResult(is_error=True, text=str(body["error"].get("message", "")))
        result = body.get("result", {})
        text = "\n".join(
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ) or None
        return ToolCallResult(
            text=text,
            structured=result.get("structuredContent"),
            is_error=bool(result.get("isError")),
        )


class PlainHttpCaller:
    """Plain HTTP API target (Bazaar input.type == "http"): POST arguments as JSON."""

    def __init__(self, client: httpx.AsyncClient | None = None, payment_hook: PaymentHook | None = None):
        self.client = client or httpx.AsyncClient(timeout=settings.dispatch_timeout_ms / 1000)
        self.payment_hook = payment_hook

    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        headers: dict[str, str] = {}
        resp = await self.client.post(entry.endpoint, json=arguments, headers=headers)
        if resp.status_code == 402 and self.payment_hook is not None:
            headers |= await self.payment_hook(entry, resp)
            resp = await self.client.post(entry.endpoint, json=arguments, headers=headers)
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            return ToolCallResult(text=resp.text)
        if isinstance(body, dict):
            return ToolCallResult(structured=body)
        return ToolCallResult(text=str(body))


class Dispatcher:
    """Routes to the right transport, with one retry on failure (spec §5.4)."""

    def __init__(self, callers: dict[Transport, ToolCaller]):
        self.callers = callers

    @classmethod
    def default(cls, payment_hook: PaymentHook | None = None) -> "Dispatcher":
        return cls(
            {
                Transport.MCP: McpHttpCaller(payment_hook=payment_hook),
                Transport.HTTP: PlainHttpCaller(payment_hook=payment_hook),
            }
        )

    async def dispatch(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        caller = self.callers.get(entry.transport)
        if caller is None:
            raise DispatchError(f"no caller for transport {entry.transport}")
        try:
            return await caller.call(entry, arguments)
        except Exception:
            try:  # one retry, then the failure propagates to the pipeline's fallback
                return await caller.call(entry, arguments)
            except Exception as e:
                raise DispatchError(f"dispatch to {entry.asp_id} failed after retry: {e}") from e
