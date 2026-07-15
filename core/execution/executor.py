"""Paid Agent-to-MCP execution, with TheHouse aggregation as the cheap path.

`McpExecutor` is the seam the Foreman/Router call when a hired Agent-to-MCP service must actually
be invoked and paid for. Two backends:

- `TheHouseExecutor` — submits the call to an in-process TheHouse `AggregatorService`, which batches
  it with other callers hitting the same ASP in the window and returns each answer for ~20% less
  than the target's per-call price. This is the default when TheHouse is enabled (LAVARD_USE_THEHOUSE,
  default on) and an aggregator is wired.
- `DirectExecutor` — the fallback that pays the target ASP directly at full price (the OKX
  Agent-Payments x402 path; wired via onchain/onchainos_cli once the money path is live).

Keeping this behind one interface means the room/foreman don't care which path runs — they always
get an `ExecutionResult` with the answer and the true charge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ExecutionResult:
    request_id: str
    status: str
    result: str | None
    charged: float
    via: str                       # "thehouse" | "direct"
    batch_id: str | None = None
    list_price: float | None = None    # what a direct call would have cost, when known

    @property
    def saved(self) -> float:
        if self.list_price is None:
            return 0.0
        return round(max(0.0, self.list_price - self.charged), 6)


class McpExecutor(Protocol):
    async def call(self, asp_id: str, tool_name: str, arguments: dict[str, Any],
                   caller_id: str, priority: bool = False) -> ExecutionResult: ...


class TheHouseExecutor:
    """Route a paid MCP call through TheHouse's in-process aggregator for the batch discount."""

    via = "thehouse"

    def __init__(self, aggregator: Any) -> None:
        # `aggregator` is a thehouse.core.service.AggregatorService (kept untyped so LAVARD's
        # stdlib core doesn't hard-import TheHouse's async stack at module load).
        self.aggregator = aggregator
        # Capture the event loop we're built on (the API's main loop, where the aggregator's async
        # engine/redis + sweeper live) so the sync Foreman can marshal calls back onto it instead
        # of spinning a fresh loop that can't touch those loop-bound resources.
        import asyncio

        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    async def call(self, asp_id: str, tool_name: str, arguments: dict[str, Any],
                   caller_id: str, priority: bool = False,
                   timeout_s: float = 5.0) -> ExecutionResult:
        req = await self.aggregator.submit(asp_id, tool_name, arguments, caller_id, priority)
        # A batched call may not be delivered the instant we submit — it fires when the window
        # fills or its timer expires — so poll until the result is terminal (or we time out).
        res = await self._await_result(req.request_id, timeout_s)
        entry = await self.aggregator.intake.lookup_asp(asp_id)
        list_price = getattr(entry, "original_price_per_call", None)
        return ExecutionResult(
            request_id=req.request_id,
            status=res.get("status", getattr(req, "status", "unknown")),
            result=res.get("result"),
            charged=float(res.get("charged") or 0.0),
            via=self.via,
            batch_id=res.get("batch_id"),
            list_price=float(list_price) if list_price is not None else None,
        )

    async def _await_result(self, request_id: str, timeout_s: float) -> dict[str, Any]:
        import asyncio
        import time

        deadline = time.monotonic() + timeout_s
        res: dict[str, Any] = {}
        while True:
            res = await self.aggregator.get_result(request_id) or {}
            if res.get("status") in ("delivered", "failed") and res.get("result") is not None:
                return res
            if time.monotonic() > deadline:
                return res
            await asyncio.sleep(0.02)


class DirectExecutor:
    """Pay the target ASP directly at full price — the fallback when TheHouse is off/unavailable.

    The real money path is OKX Agent-Payments (x402 / `onchainos payment a2a-pay`). That rail is
    wired through an injected `payer` (an object exposing `a2a_pay_create/a2a_pay/a2a_pay_status`,
    e.g. `onchain.onchainos_cli.OnchainOsCli`); when none is configured or the CLI isn't
    credentialed, `call` returns a graceful `status="unavailable"` result instead of raising, so no
    caller ever gets an exception grenade just for holding this executor.

    NOTE: settling payment is wired; the arbitrary-ASP MCP tool *invocation* transport is the one
    remaining live piece (needs the ASP endpoint + 402 replay), so a real result is only returned
    once that transport is provided. Until then this reports `unavailable`, never a fake answer."""

    via = "direct"

    def __init__(self, payer: Any = None) -> None:
        self._payer = payer

    def _resolve_payer(self) -> Any | None:
        if self._payer is not None:
            return self._payer
        try:
            from onchain.onchainos_cli import get_cli

            cli = get_cli()
            if cli.available() and cli.credentialed():
                return cli
        except Exception:
            return None
        return None

    async def call(self, asp_id: str, tool_name: str, arguments: dict[str, Any],
                   caller_id: str, priority: bool = False) -> ExecutionResult:
        payer = self._resolve_payer()
        if payer is None:
            # No live money rail: degrade, don't explode. The Foreman treats a non-delivered
            # result as a signal to fall back to the A2A escrow hire path.
            return ExecutionResult(
                request_id="", status="unavailable", result=None, charged=0.0, via=self.via,
            )
        # Real direct settlement is available but the per-ASP MCP invocation transport is the
        # remaining live piece — surface that honestly rather than fabricate an answer.
        return ExecutionResult(
            request_id="", status="unavailable", result=None, charged=0.0, via=self.via,
        )


def build_thehouse_executor(engine: Any, redis: Any, dispatcher: Any = None,
                            semantic: Any = None) -> TheHouseExecutor:
    """Construct a TheHouse-backed executor from the same pieces TheHouse's own app uses."""
    from thehouse.core.dispatcher.service import Dispatcher
    from thehouse.core.service import AggregatorService

    aggregator = AggregatorService(engine, redis, dispatcher or Dispatcher.default(),
                                   semantic=semantic)
    return TheHouseExecutor(aggregator)


def get_executor(aggregator: Any = None) -> McpExecutor:
    """Pick the execution path. TheHouse (cheap) when enabled + wired; else direct (full price)."""
    from core.config import get_settings

    if get_settings().use_thehouse and aggregator is not None:
        return TheHouseExecutor(aggregator)
    return DirectExecutor()
