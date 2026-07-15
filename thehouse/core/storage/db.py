"""Async database layer. SQLite in dev, Postgres in prod — same SQLAlchemy Core schema."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from thehouse.core.config import settings

metadata = MetaData()

asp_registry = Table(
    "asp_registry",
    metadata,
    Column("asp_id", String(64), primary_key=True),
    Column("tool_name", String(128), nullable=False),
    Column("description", Text, default=""),
    Column("endpoint", String(512), default=""),
    Column("transport", String(8), default="mcp"),
    Column("tool_schema", JSON, default=dict),
    Column("mode", String(24), default="manual_review"),
    Column("original_price_per_call", Float, default=0.0),
    Column("thehouse_price", Float, default=0.0),
    Column("max_batch_size", Integer, default=2),   # owner cap: 2 questions/compound (Q8)
    Column("window_timer_ms", Integer, default=300),
    Column("cache_ttl_seconds", Integer, default=30),
    Column("break_even_batch_size", Integer, default=2),
    Column("batch_param", String(64), nullable=True),
    Column("active", Boolean, default=True),
)

request_log = Table(
    "request_log",
    metadata,
    Column("request_id", String(64), primary_key=True),
    Column("asp_id", String(64), nullable=False),
    Column("tool_name", String(128), nullable=False),
    Column("arguments", JSON, default=dict),
    Column("query", Text, nullable=True),
    Column("caller_id", String(128), nullable=False),
    Column("priority", Boolean, default=False),
    Column("received_at_ms", Integer, nullable=False),
    Column("status", String(16), default="queued"),
    Column("merged_into", String(64), nullable=True),
    Column("batch_id", String(64), nullable=True),
    Column("result", Text, nullable=True),
    Column("charged", Float, nullable=True),
    # the TTL sweep scans (status, received_at_ms) every pass
    Index("ix_request_log_status_received", "status", "received_at_ms"),
)

economics_ledger = Table(
    "economics_ledger",
    metadata,
    Column("batch_id", String(64), primary_key=True),
    Column("asp_id", String(64), nullable=False),
    Column("batch_size", Integer, nullable=False),
    Column("window_open_ms", Integer, nullable=False),
    Column("window_fire_reason", String(16), nullable=False),
    Column("target_cost_paid", Float, nullable=False),
    Column("thehouse_revenue_collected", Float, nullable=False),
    Column("gross_margin", Float, nullable=False),
    Column("below_break_even", Boolean, default=False),
    Column("dedup_hits", Integer, default=0),
    Column("priority_surcharges", Float, default=0.0),
    Column("split_quality", String(8), default="clean"),
    Column("created_at_ms", Integer, nullable=False),
)

settlements = Table(
    "settlements",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts_ms", Integer, nullable=False),
    Column("direction", String(3), nullable=False),  # "in" (caller→TheHouse) | "out" (TheHouse→target)
    Column("counterparty", String(128), nullable=False),  # payer/payee wallet address
    Column("amount_usdt", Float, nullable=False),
    Column("scheme", String(24), default="exact"),
    Column("network", String(24), default="eip155:196"),
    Column("tx_ref", String(256), nullable=True),   # tx hash / PAYMENT-RESPONSE reference
    Column("request_id", String(64), nullable=True),
    Column("batch_id", String(64), nullable=True),
    # Deferred (aggr_deferred) settlement lifecycle. Immediate rails (exact/dev) are born
    # "settled"; batched authorizations are "accepted" at intake and advance to
    # "settled"/"failed" only when the facilitator's batch lands (polled via /settle/status).
    Column("settle_status", String(16), default="settled", nullable=False),
    Column("settle_txhash", String(128), nullable=True),
)

# One row per accepted inbound payment authorization (hash of the exact header bytes).
# A second presentation of the same authorization is a replay and is refused — real x402
# authorizations are unique per payment (nonce + signature), so identical bytes are never
# two legitimate purchases.
payment_keys = Table(
    "payment_keys",
    metadata,
    Column("replay_key", String(64), primary_key=True),
    Column("ts_ms", Integer, nullable=False),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts_ms", Integer, nullable=False),
    Column("event", String(64), nullable=False),
    Column("payload", Text, nullable=False),
)

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url)
    return _engine


async def init_db(engine: AsyncEngine | None = None) -> None:
    engine = engine or get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def reset_engine() -> None:
    """Dispose the module-level engine (tests)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def audit(event: str, payload: dict[str, Any], engine: AsyncEngine | None = None) -> None:
    from thehouse.core.models import now_ms

    engine = engine or get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(audit_log).values(
                ts_ms=now_ms(), event=event, payload=json.dumps(payload, default=str)
            )
        )


__all__ = [
    "metadata",
    "asp_registry",
    "request_log",
    "economics_ledger",
    "audit_log",
    "get_engine",
    "init_db",
    "reset_engine",
    "audit",
    "insert",
    "select",
    "update",
]
