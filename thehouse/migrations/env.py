"""Alembic environment for TheHouse.

Targets `thehouse.core.storage.db.metadata` so autogenerate tracks the real schema. Resolves
the DB URL from settings (THEHOUSE_DATABASE_URL) and drives the async engine via run_sync, so
the same migration history works for sqlite (dev) and Postgres (prod).
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from thehouse.core.config import settings
from thehouse.core.storage.db import metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # sqlite needs batch mode for ALTER
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": settings.database_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
