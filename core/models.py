"""ORM models — identity, jobs, task graph. Postgres in prod, SQLite for local dev.

Phase 1 covers Job + TaskNode. Later phases add hires, audit log, playbook index, etc.,
each in its own migration/phase (see ARCHITECTURE.md module map).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    goal: Mapped[str] = mapped_column(Text)
    # lifecycle: received -> verified -> decomposed -> (hiring/running/... later phases)
    status: Mapped[str] = mapped_column(String(32), default="received")

    # verify-first intake (§4.6 / Phase 1)
    restated_goal: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    success_criteria: Mapped[list] = mapped_column(JSON, default=list)
    open_questions: Mapped[list] = mapped_column(JSON, default=list)

    # provenance of the plan: 'heuristic' (offline fallback) or 'llm'
    planner: Mapped[str] = mapped_column(String(16), default="heuristic")
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    nodes: Mapped[list["TaskNode"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="TaskNode.order_index"
    )


class TaskNode(Base):
    """A node in the goal's task DAG. `depends_on` holds the KEYS of prerequisite nodes."""

    __tablename__ = "task_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)

    key: Mapped[str] = mapped_column(String(16))  # stable within a job, e.g. "n1"
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    success_criteria: Mapped[list] = mapped_column(JSON, default=list)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # list[str] of node keys

    # what capability this node needs (drives marketplace query + necessity test, Phase 2/4)
    capability: Mapped[str] = mapped_column(String(80), default="general")
    # necessity test placeholder: does the goal genuinely need an external hire here?
    needs_hire: Mapped[bool] = mapped_column(Boolean, default=True)
    rationale: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(32), default="planned")
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped["Job"] = relationship(back_populates="nodes")
