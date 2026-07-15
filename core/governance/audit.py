"""Thin helper over the store's append-only, hash-chained audit log (§4.6).

`audit(job_id, kind, actor, detail, data)` appends one tamper-evident entry. Every hire, verdict,
payment, room halt, and distill flows through here so the log is the single source of truth.
"""

from __future__ import annotations

from core.store import get_store


def audit(job_id: str, kind: str, actor: str, detail: str = "", data: dict | None = None) -> None:
    get_store().append_audit(job_id, kind, actor, detail, data or {})
