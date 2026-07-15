"""LAVARD execution seam — how a paid Agent-to-MCP call actually gets made.

The whole point of blending TheHouse in: when LAVARD hires an Agent-to-MCP (pay-per-call) ASP,
it routes the call through TheHouse's aggregator instead of paying the target directly, so the
call is batched with other callers and comes back ~20% cheaper. This is LAVARD's cheapest-execution
moat made real (it complements the Router's cache/dedup savings — see core/router)."""

from core.execution.executor import (
    ExecutionResult,
    McpExecutor,
    TheHouseExecutor,
    build_thehouse_executor,
    get_executor,
)

__all__ = [
    "ExecutionResult", "McpExecutor", "TheHouseExecutor",
    "build_thehouse_executor", "get_executor",
]
