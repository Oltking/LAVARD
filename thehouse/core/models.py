"""Domain models shared across TheHouse subsystems."""

from __future__ import annotations

import enum
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class ASPMode(str, enum.Enum):
    A_LLM = "A_llm"                    # LLM-backed: compound natural-language queries
    B_NATIVE = "B_native"              # deterministic API with native array/batch input
    B_FANOUT = "B_fanout"              # deterministic API, parallel fan-out only
    NON_AGGREGATABLE = "non_aggregatable"  # side-effectful: direct route, no discount
    MANUAL_REVIEW = "manual_review"    # profiler could not classify safely


# Modes whose calls are read-only and therefore safe to cache and merge. Side-effectful
# (non_aggregatable) and unclassified (manual_review) calls must each reach the target:
# two identical transfers are two distinct intents, never one answer served twice.
DEDUP_SAFE_MODES = frozenset({ASPMode.A_LLM, ASPMode.B_NATIVE, ASPMode.B_FANOUT})


class Transport(str, enum.Enum):
    MCP = "mcp"    # JSON-RPC tools/call endpoint
    HTTP = "http"  # plain HTTP API per Bazaar outputSchema.input


class FireReason(str, enum.Enum):
    BREAK_EVEN = "break_even"
    TIMER = "timer"
    PRIORITY = "priority"


class SplitQuality(str, enum.Enum):
    CLEAN = "clean"
    PARTIAL = "partial"
    FAILED = "failed"


class RequestStatus(str, enum.Enum):
    QUEUED = "queued"
    MERGED = "merged"          # semantic duplicate merged into another slot
    CACHED = "cached"          # served from exact-match cache
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    FAILED = "failed"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def now_ms() -> int:
    return int(time.time() * 1000)


class CallerRequest(BaseModel):
    """A single caller's request as accepted by intake (already validated)."""

    request_id: str = Field(default_factory=new_request_id)
    asp_id: str                          # target ASP registry id
    tool_name: str                       # target tool (e.g. news_ai.get_news)
    arguments: dict[str, Any]            # tool arguments as the caller would send them
    query: str | None = None             # extracted primary query string (Mode A)
    caller_id: str                       # payer identity (wallet addr; dev: explicit id)
    priority: bool = False
    received_at_ms: int = Field(default_factory=now_ms)
    status: RequestStatus = RequestStatus.QUEUED
    merged_into: str | None = None       # request_id of the slot this was merged into
    fingerprint: str | None = None       # exact-match dedup fingerprint
    result: str | None = None            # delivered answer (cached hits carry it immediately)


class ASPEntry(BaseModel):
    """One row of the ASP registry (spec §5.1)."""

    asp_id: str
    tool_name: str
    description: str = ""
    endpoint: str = ""
    transport: Transport = Transport.MCP
    tool_schema: dict[str, Any] = Field(default_factory=dict)
    mode: ASPMode = ASPMode.MANUAL_REVIEW
    original_price_per_call: float = 0.0
    thehouse_price: float = 0.0
    max_batch_size: int = 2
    window_timer_ms: int = 300
    cache_ttl_seconds: int = 30
    break_even_batch_size: int = 2
    batch_param: str | None = None       # Mode B native: the array parameter name
    active: bool = True


class BatchResult(BaseModel):
    """Outcome of one fired batch, input to the economics ledger (spec §5.6)."""

    asp_id: str
    batch_id: str
    batch_size: int
    window_open_ms: int
    window_fire_reason: FireReason
    target_cost_paid: float
    thehouse_revenue_collected: float
    gross_margin: float
    below_break_even: bool
    dedup_hits: int = 0
    priority_surcharges: float = 0.0
    split_quality: SplitQuality = SplitQuality.CLEAN
