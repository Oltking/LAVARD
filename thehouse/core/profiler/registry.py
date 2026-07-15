"""ASP Registry operations (spec §5.1): upsert, fetch, list, price derivation."""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.config import settings
from thehouse.core.models import ASPEntry


class RegistryService:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def upsert(self, entry: ASPEntry) -> ASPEntry:
        from thehouse.core.storage.db import asp_registry

        # thehouse_price is definitionally target_fee × discount — always re-derive it from the
        # current fee so a price edit can never leave a stale discount detached from the fee
        # (audit fix #5). Priority/fan-out prices are computed at call time in pricing.py.
        if entry.original_price_per_call:
            entry.thehouse_price = round(
                entry.original_price_per_call * settings.discount_rate, 6
            )
        values = entry.model_dump()
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(asp_registry.c.asp_id).where(asp_registry.c.asp_id == entry.asp_id)
                )
            ).first()
            if existing:
                await conn.execute(
                    update(asp_registry)
                    .where(asp_registry.c.asp_id == entry.asp_id)
                    .values(**values)
                )
            else:
                await conn.execute(insert(asp_registry).values(**values))
        return entry

    async def get(self, asp_id: str) -> ASPEntry | None:
        from thehouse.core.storage.db import asp_registry

        async with self.engine.connect() as conn:
            row = (
                await conn.execute(select(asp_registry).where(asp_registry.c.asp_id == asp_id))
            ).mappings().first()
        return ASPEntry(**dict(row)) if row else None

    async def list_all(self, active_only: bool = False) -> list[ASPEntry]:
        from thehouse.core.storage.db import asp_registry

        stmt = select(asp_registry)
        if active_only:
            stmt = stmt.where(asp_registry.c.active.is_(True))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [ASPEntry(**dict(r)) for r in rows]

    async def set_mode(self, asp_id: str, mode: str) -> None:
        from thehouse.core.storage.db import asp_registry

        async with self.engine.begin() as conn:
            await conn.execute(
                update(asp_registry).where(asp_registry.c.asp_id == asp_id).values(mode=mode)
            )
