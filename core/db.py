"""OPTIONAL SQLAlchemy/Postgres backend (spec §3 production store).

The default runtime path uses the dependency-free store in core/store.py. This module defines the
canonical ORM schema (core/models.py) and is used when LAVARD_DATABASE_URL points at Postgres and
SQLAlchemy is installed. Kept in lockstep with the sqlite store's table shapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings
from core.models import Base

_settings = get_settings()

# check_same_thread only matters for SQLite; harmless to gate on the scheme.
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """Create tables if absent. (Real migrations come with Alembic before go-live.)"""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
