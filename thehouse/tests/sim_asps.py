"""Simulated target ASPs used across the test suite (see QUESTIONS.md Q12).

SimLLMCaller mimics an LLM-backed Mode A agent: it reads a numbered compound question and
answers each line, numbered to match. SimPriceCaller mimics a deterministic Mode B feed.
"""

from __future__ import annotations

import re
from typing import Any

from thehouse.core.models import ASPEntry
from thehouse.core.profiler.profiler import ToolCallResult

# Canned knowledge for the simulated LLM agent.
_KNOWLEDGE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"2 plus 2|2\s*\+\s*2", re.I), "2 plus 2 equals 4."),
    (re.compile(r"sky", re.I), "A clear daytime sky is blue."),
    (re.compile(r"current date", re.I), "Sunday, July 5 2026."),
    (re.compile(r"president", re.I), "Donald Trump."),
    (re.compile(r"btc|bitcoin", re.I), "BTC is currently trading at approximately $107,432."),
    (re.compile(r"capital of france", re.I), "Paris."),
]


def _answer_one(question: str) -> str:
    for pattern, answer in _KNOWLEDGE:
        if pattern.search(question):
            return answer
    return "I don't have information on that."


_NUMBERED = re.compile(r"^\s*(\d+)\)\s*(.+)$", re.MULTILINE)


class SimLLMCaller:
    """Mode A: freeform text; follows the numeric wrapper instruction."""

    def __init__(self, follow_numbering: bool = True):
        self.follow_numbering = follow_numbering
        self.calls: list[dict[str, Any]] = []

    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        self.calls.append(arguments)
        prompt = next((v for v in arguments.values() if isinstance(v, str)), "")
        numbered = _NUMBERED.findall(prompt)
        if numbered and self.follow_numbering:
            lines = [f"{n}) {_answer_one(q)}" for n, q in numbered]
            return ToolCallResult(text="\n".join(lines))
        if numbered:  # misbehaving LLM: answers but drops the numbering
            return ToolCallResult(text=" ".join(_answer_one(q) for _, q in numbered))
        return ToolCallResult(text=_answer_one(prompt))


PRICES = {"BTC": 107432.50, "ETH": 3821.0, "SOL": 178.0}


class SimPriceCaller:
    """Mode B: deterministic keyed JSON; supports symbol or symbols[]."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        self.calls.append(arguments)
        if "symbols" in arguments:
            syms = arguments["symbols"]
            return ToolCallResult(structured={s: PRICES.get(s) for s in syms})
        sym = arguments.get("symbol") or arguments.get("query", "")
        if sym in PRICES:
            return ToolCallResult(structured={sym: PRICES[sym]})
        return ToolCallResult(structured={}, is_error=sym not in PRICES)


class SimBrokenCaller:
    """Always errors — profiler must flag manual_review."""

    async def call(self, entry: ASPEntry, arguments: dict[str, Any]) -> ToolCallResult:
        return ToolCallResult(is_error=True)


LLM_SCHEMA = {
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
}

_PRICE_OUTPUT = {
    "type": "object",
    "additionalProperties": {"type": "number"},
    "description": "map of symbol -> latest price",
}

PRICE_SCHEMA_NATIVE = {
    "inputSchema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "symbols": {"type": "array", "items": {"type": "string"}},
        },
    },
    "outputSchema": _PRICE_OUTPUT,
}

PRICE_SCHEMA_SINGLE = {
    "inputSchema": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
    "outputSchema": _PRICE_OUTPUT,
}
