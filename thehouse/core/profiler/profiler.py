"""The Profiler (spec §5.1): decides how — and whether — an ASP can be aggregated.

Order of checks:
1. Side-effect screening from the tool description/name. Side-effectful → non_aggregatable,
   NO probe call is ever made (spec rule 4).
2. Two-question compound probe:
   - freeform text containing both answers → A_llm
   - structured object keyed by parameter → B_native (schema has an array param) or B_fanout
   - error / only one answer → manual_review
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from thehouse.core.models import ASPEntry, ASPMode
from thehouse.core.storage.db import audit


class ToolCaller(Protocol):
    """Minimal dispatch interface the Profiler probes through (real impl: Phase 6 Dispatcher)."""

    async def call(
        self, entry: ASPEntry, arguments: dict[str, Any]
    ) -> "ToolCallResult": ...


@dataclass
class ToolCallResult:
    text: str | None = None                 # freeform content[].text
    structured: dict[str, Any] | None = None  # structuredContent
    is_error: bool = False


# Verbs that indicate state-changing behaviour. Matched on word boundaries against
# tool name + description, lowercased.
SIDE_EFFECT_VERBS = (
    "write", "execute", "send", "transfer", "swap", "trade", "buy", "sell", "order",
    "mint", "burn", "sign", "broadcast", "delete", "remove", "create", "update",
    "post", "publish", "submit", "stake", "unstake", "withdraw", "deposit", "approve",
    "pay", "refund", "cancel",
)

PROBE_Q1 = "What is 2 plus 2?"
PROBE_Q2 = "What color is a clear daytime sky?"
PROBE_A1 = re.compile(r"\b(4|four)\b", re.IGNORECASE)
PROBE_A2 = re.compile(r"\bblue\b", re.IGNORECASE)

PROBE_COMPOUND = (
    "Please answer the following questions and number your responses to match exactly:\n\n"
    f"1) {PROBE_Q1}\n2) {PROBE_Q2}"
)


def has_side_effects(entry: ASPEntry) -> bool:
    text = f"{entry.tool_name} {entry.description}".lower()
    return any(re.search(rf"\b{verb}\b", text) for verb in SIDE_EFFECT_VERBS)


def find_array_param(tool_schema: dict[str, Any]) -> str | None:
    """Return the name of an array-typed input parameter, if the tool declares one."""
    props = (tool_schema.get("inputSchema") or tool_schema).get("properties", {})
    for name, spec in props.items():
        if isinstance(spec, dict) and spec.get("type") == "array":
            return name
    return None


class Profiler:
    def __init__(self, caller: ToolCaller, engine=None):
        self.caller = caller
        self.engine = engine

    async def profile(self, entry: ASPEntry) -> ASPEntry:
        """Classify the ASP's mode in place and return it."""
        entry.mode = await self._classify(entry)
        if entry.mode == ASPMode.B_NATIVE and not entry.batch_param:
            entry.batch_param = find_array_param(entry.tool_schema)
        if self.engine is not None:
            await audit(
                "asp_profiled",
                {"asp_id": entry.asp_id, "mode": entry.mode.value},
                engine=self.engine,
            )
        return entry

    async def _classify(self, entry: ASPEntry) -> ASPMode:
        if has_side_effects(entry):
            return ASPMode.NON_AGGREGATABLE

        # A declared outputSchema means the server MUST return structured results that
        # conform to it (MCP spec 2025-06-18) — a deterministic API, Mode B. Probing it
        # with a natural-language compound question would only produce a garbage error.
        if entry.tool_schema.get("outputSchema"):
            return (
                ASPMode.B_NATIVE
                if find_array_param(entry.tool_schema)
                else ASPMode.B_FANOUT
            )

        # Compound natural-language probe on the primary string param.
        try:
            result = await self.caller.call(entry, _probe_arguments(entry))
        except Exception:
            return ASPMode.MANUAL_REVIEW

        if result.is_error:
            return ASPMode.MANUAL_REVIEW

        if result.text and not result.structured:
            got_a1 = bool(PROBE_A1.search(result.text))
            got_a2 = bool(PROBE_A2.search(result.text))
            if got_a1 and got_a2:
                return ASPMode.A_LLM
            return ASPMode.MANUAL_REVIEW

        if result.structured is not None:
            if find_array_param(entry.tool_schema):
                return ASPMode.B_NATIVE
            return ASPMode.B_FANOUT

        return ASPMode.MANUAL_REVIEW


def _probe_arguments(entry: ASPEntry) -> dict[str, Any]:
    """Put the compound probe into the tool's first string parameter (default 'query')."""
    props = (entry.tool_schema.get("inputSchema") or entry.tool_schema).get("properties", {})
    for name, spec in props.items():
        if isinstance(spec, dict) and spec.get("type") == "string":
            return {name: PROBE_COMPOUND}
    return {"query": PROBE_COMPOUND}
