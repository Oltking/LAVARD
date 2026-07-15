"""Persistence — stdlib sqlite3 by default (zero infra), mirroring the ORM schema in models.py.

Why two backends: `core/models.py` + `core/db.py` define the SQLAlchemy/Postgres production schema
(spec §3). This module is a dependency-free sqlite implementation of the same tables so the CLI
demo and tests run with nothing installed. `get_store()` picks the backend from LAVARD_DATABASE_URL:
a `sqlite://` URL uses this store; a `postgresql+...` URL routes to the SQLAlchemy backend
(wired once that dependency is installable in the environment).

The row shapes here are the source of truth for JobView assembly in core/service.py.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from core.config import get_settings
from core.schemas import PlannedNode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    owner_id TEXT NOT NULL DEFAULT 'default-owner',
    status TEXT NOT NULL DEFAULT 'received',
    restated_goal TEXT DEFAULT '',
    assumptions TEXT DEFAULT '[]',
    success_criteria TEXT DEFAULT '[]',
    open_questions TEXT DEFAULT '[]',
    planner TEXT DEFAULT 'heuristic',
    budget_usd REAL DEFAULT 0,
    path_mode TEXT DEFAULT '',
    path_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    success_criteria TEXT DEFAULT '[]',
    depends_on TEXT DEFAULT '[]',
    capability TEXT DEFAULT 'general',
    needs_hire INTEGER DEFAULT 1,
    rationale TEXT DEFAULT '',
    status TEXT DEFAULT 'planned',
    order_index INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_task_nodes_job ON task_nodes(job_id);
CREATE TABLE IF NOT EXISTS hires (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_name TEXT DEFAULT '',
    in_room_id TEXT NOT NULL,
    capability TEXT DEFAULT '',
    amount_usd REAL DEFAULT 0,
    trust TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    escrow_id TEXT DEFAULT '',
    payee TEXT DEFAULT '',
    status TEXT DEFAULT 'hired',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hires_job ON hires(job_id);
CREATE TABLE IF NOT EXISTS job_control (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    frozen INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT DEFAULT '',
    data TEXT DEFAULT '{}',
    prev_hash TEXT DEFAULT '',
    hash TEXT NOT NULL
);
-- UNIQUE so a concurrent duplicate seq fails LOUDLY instead of silently corrupting the chain.
CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_jobseq ON audit_log(job_id, seq);
CREATE TABLE IF NOT EXISTS audit_head (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    length INTEGER NOT NULL,
    head_hash TEXT NOT NULL,
    head_sig TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS room_checkpoint (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    completed_nodes TEXT DEFAULT '[]',
    spend_usd REAL DEFAULT 0,
    room_turns INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_executions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    job_id TEXT,
    capability TEXT DEFAULT '',
    status TEXT DEFAULT 'delivered',   -- delivered | failed | recovered
    latency_ms INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    reused INTEGER DEFAULT 0,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_exec ON agent_executions(agent_id);
CREATE TABLE IF NOT EXISTS insights_cooccurrence (
    cap_a TEXT NOT NULL, cap_b TEXT NOT NULL, n INTEGER DEFAULT 0,
    PRIMARY KEY (cap_a, cap_b)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_path(database_url: str) -> str:
    # sqlite:///./lavard.db -> ./lavard.db ; sqlite:///:memory: -> :memory:
    return database_url.split("sqlite:///", 1)[1] if "sqlite:///" in database_url else database_url


class SqliteJobStore:
    def __init__(self, database_url: str) -> None:
        self.path = _sqlite_path(database_url)
        self.init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL + a busy timeout let a resumed/second process read-and-write concurrently without
        # immediate "database is locked" (Phase 10 hardening). No-op for :memory:.
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init(self) -> None:
        with self._connect() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    # Bump when a new additive migration step is added below; `_migrate` fast-paths when the DB
    # is already stamped at the current version. This is the stdlib-store equivalent of Alembic's
    # version table (TheHouse's SQLAlchemy schema is managed by Alembic — see thehouse/migrations).
    _SCHEMA_VERSION = 2

    @classmethod
    def _migrate(cls, c: sqlite3.Connection) -> None:
        """Additive, idempotent migrations for DBs created before a column existed.

        Guarded by PRAGMA user_version so an up-to-date DB skips the checks entirely and every
        schema change is an explicit, ordered, forward-only step."""
        current = c.execute("PRAGMA user_version").fetchone()[0]
        if current >= cls._SCHEMA_VERSION:
            return
        hire_cols = {r["name"] for r in c.execute("PRAGMA table_info(hires)").fetchall()}
        if "payee" not in hire_cols:
            c.execute("ALTER TABLE hires ADD COLUMN payee TEXT DEFAULT ''")
        job_cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
        if "path_mode" not in job_cols:  # v2: conductor path decision
            c.execute("ALTER TABLE jobs ADD COLUMN path_mode TEXT DEFAULT ''")
            c.execute("ALTER TABLE jobs ADD COLUMN path_reason TEXT DEFAULT ''")
        c.execute(f"PRAGMA user_version = {cls._SCHEMA_VERSION}")

    def create_job(self, goal: str, owner_id: str = "default-owner") -> str:
        job_id = uuid.uuid4().hex
        ts = _now()
        with self._connect() as c:
            c.execute(
                "INSERT INTO jobs (id, goal, owner_id, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, goal, owner_id, "received", ts, ts),
            )
        return job_id

    def save_intake(
        self,
        job_id: str,
        *,
        restated_goal: str,
        assumptions: list[str],
        success_criteria: list[str],
        open_questions: list[str],
        status: str = "verified",
    ) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET restated_goal=?, assumptions=?, success_criteria=?, "
                "open_questions=?, status=?, updated_at=? WHERE id=?",
                (
                    restated_goal,
                    json.dumps(assumptions),
                    json.dumps(success_criteria),
                    json.dumps(open_questions),
                    status,
                    _now(),
                    job_id,
                ),
            )

    def save_plan(self, job_id: str, planner: str, nodes: list[PlannedNode]) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM task_nodes WHERE job_id=?", (job_id,))
            for i, n in enumerate(nodes):
                c.execute(
                    "INSERT INTO task_nodes (id, job_id, key, title, description, "
                    "success_criteria, depends_on, capability, needs_hire, rationale, "
                    "status, order_index) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        job_id,
                        n.key,
                        n.title,
                        n.description,
                        json.dumps(n.success_criteria),
                        json.dumps(n.depends_on),
                        n.capability,
                        int(n.needs_hire),
                        n.rationale,
                        "planned",
                        i,
                    ),
                )
            c.execute(
                "UPDATE jobs SET planner=?, status=?, updated_at=? WHERE id=?",
                (planner, "decomposed", _now(), job_id),
            )

    # --- reputation graph: per-agent execution outcomes (Optimization Engine input) ---
    def record_execution(self, agent_id: str, *, job_id: str = "", capability: str = "",
                         status: str = "delivered", latency_ms: int = 0, cost_usd: float = 0.0,
                         reused: bool = False) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT INTO agent_executions (id, agent_id, job_id, capability, status, "
                "latency_ms, cost_usd, reused, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, agent_id, job_id, capability, status,
                 int(latency_ms), float(cost_usd), int(reused), _now()))

    def get_agent_stats(self, agent_id: str) -> dict:
        """Aggregate an agent's execution history for the reputation graph."""
        with self._connect() as c:
            row = c.execute(
                "SELECT COUNT(*) n, "
                "SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered, "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed, "
                "SUM(CASE WHEN status='recovered' THEN 1 ELSE 0 END) recovered, "
                "AVG(latency_ms) avg_latency, AVG(cost_usd) avg_cost, "
                "SUM(reused) reused FROM agent_executions WHERE agent_id=?",
                (agent_id,)).fetchone()
        n = row["n"] or 0
        return {
            "samples": n,
            "delivered": row["delivered"] or 0,
            "failed": row["failed"] or 0,
            "recovered": row["recovered"] or 0,
            "avg_latency_ms": float(row["avg_latency"] or 0.0),
            "avg_cost_usd": float(row["avg_cost"] or 0.0),
            "reused": row["reused"] or 0,
        }

    # --- global anonymized aggregate learning (Tier 2): capability co-occurrence ONLY ---
    # This table intentionally holds no owner_id, no goal text, and no deliverables — only
    # capability-pair counts, from which no individual user's workflow can be reconstructed.
    def bump_cooccurrence(self, capabilities: list[str]) -> None:
        caps = sorted({c for c in capabilities if c and c != "coordination"})
        if len(caps) < 2:
            return
        with self._connect() as c:
            for i, a in enumerate(caps):
                for b in caps[i + 1:]:
                    for x, y in ((a, b), (b, a)):
                        c.execute(
                            "INSERT INTO insights_cooccurrence (cap_a, cap_b, n) VALUES (?,?,1) "
                            "ON CONFLICT(cap_a, cap_b) DO UPDATE SET n = n + 1", (x, y))

    def get_cooccurrence(self, capability: str) -> dict[str, int]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT cap_b, n FROM insights_cooccurrence WHERE cap_a=?", (capability,)).fetchall()
        return {r["cap_b"]: r["n"] for r in rows}

    def network_metrics(self) -> dict:
        """Cross-job aggregate for the three network effects (memory / liquidity / reputation)."""
        with self._connect() as c:
            reuse = c.execute(
                "SELECT COUNT(*) n FROM audit_log WHERE kind IN "
                "('hire_skipped_memory','crew_reused')").fetchone()["n"] or 0
            mcp_rows = c.execute("SELECT data FROM audit_log WHERE kind='mcp_executed'").fetchall()
            room_rows = c.execute("SELECT data FROM audit_log WHERE kind LIKE 'room_%'").fetchall()
            optim = c.execute(
                "SELECT COUNT(*) n FROM audit_log WHERE kind='optimizer_ranked'").fetchone()["n"] or 0
            rep = c.execute(
                "SELECT COUNT(DISTINCT agent_id) agents, COUNT(*) execs, "
                "SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered "
                "FROM agent_executions").fetchone()
            jobs = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] or 0
        thehouse_saved = sum(json.loads(r["data"] or "{}").get("saved", 0.0) for r in mcp_rows)
        router_saved = sum(json.loads(r["data"] or "{}").get("router_saved_usd", 0.0)
                           for r in room_rows)
        return {
            "jobs": jobs,
            "memory_reuse_events": reuse,
            "optimizer_selections": optim,
            "thehouse_saved_usd": round(thehouse_saved, 4),
            "router_saved_usd": round(router_saved, 4),
            "agents_scored": rep["agents"] or 0,
            "executions_recorded": rep["execs"] or 0,
            "executions_delivered": rep["delivered"] or 0,
        }

    def save_path_decision(self, job_id: str, mode: str, reason: str) -> None:
        """Persist the conductor's cheapest-sufficient path decision (one source of truth)."""
        with self._connect() as c:
            c.execute("UPDATE jobs SET path_mode=?, path_reason=?, updated_at=? WHERE id=?",
                      (mode, reason, _now(), job_id))

    # --- hires (Phase 4) ---
    def create_hire(
        self,
        job_id: str,
        *,
        node_key: str,
        agent_id: str,
        agent_name: str,
        in_room_id: str,
        capability: str,
        amount_usd: float,
        trust: str,
        confidence: float,
        escrow_id: str,
        payee: str = "",
        status: str = "hired",
    ) -> str:
        hire_id = uuid.uuid4().hex
        with self._connect() as c:
            c.execute(
                "INSERT INTO hires (id, job_id, node_key, agent_id, agent_name, in_room_id, "
                "capability, amount_usd, trust, confidence, escrow_id, payee, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (hire_id, job_id, node_key, agent_id, agent_name, in_room_id, capability,
                 amount_usd, trust, confidence, escrow_id, payee, status, _now()),
            )
        return hire_id

    def set_hire_status(self, hire_id: str, status: str) -> None:
        with self._connect() as c:
            c.execute("UPDATE hires SET status=? WHERE id=?", (status, hire_id))

    def get_hire(self, hire_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM hires WHERE id=?", (hire_id,)).fetchone()
        return dict(row) if row else None

    def get_hires(self, job_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM hires WHERE job_id=? ORDER BY created_at", (job_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --- room control / kill-switch (Phase 5) ---
    def freeze_room(self, job_id: str) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT INTO job_control (job_id, frozen, updated_at) VALUES (?,1,?) "
                "ON CONFLICT(job_id) DO UPDATE SET frozen=1, updated_at=excluded.updated_at",
                (job_id, _now()),
            )

    def unfreeze_room(self, job_id: str) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT INTO job_control (job_id, frozen, updated_at) VALUES (?,0,?) "
                "ON CONFLICT(job_id) DO UPDATE SET frozen=0, updated_at=excluded.updated_at",
                (job_id, _now()),
            )

    def is_room_frozen(self, job_id: str) -> bool:
        with self._connect() as c:
            row = c.execute("SELECT frozen FROM job_control WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and row["frozen"])

    # --- room checkpointing / resume-after-crash (Phase 10) ---
    def save_checkpoint(self, job_id: str, completed_nodes: list[str], spend_usd: float,
                        room_turns: int) -> None:
        with self._connect() as c:
            c.execute(
                "INSERT INTO room_checkpoint "
                "(job_id, completed_nodes, spend_usd, room_turns, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET completed_nodes=excluded.completed_nodes, "
                "spend_usd=excluded.spend_usd, room_turns=excluded.room_turns, "
                "updated_at=excluded.updated_at",
                (job_id, json.dumps(completed_nodes), spend_usd, room_turns, _now()),
            )

    def get_checkpoint(self, job_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT completed_nodes, spend_usd, room_turns FROM room_checkpoint WHERE job_id=?",
                (job_id,)).fetchone()
        if row is None:
            return None
        return {"completed_nodes": json.loads(row["completed_nodes"]),
                "spend_usd": row["spend_usd"], "room_turns": row["room_turns"]}

    def clear_checkpoint(self, job_id: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM room_checkpoint WHERE job_id=?", (job_id,))

    # --- immutable (hash-chained) audit log (Phase 8) ---
    @staticmethod
    def _seal(length: int, head_hash: str) -> str:
        """HMAC over (length, terminal hash). Keyed by a secret held outside the DB, so an
        attacker who can edit rows still cannot forge a shorter-but-consistent chain."""
        import hashlib
        import hmac

        key = get_settings().audit_key.encode()
        return hmac.new(key, f"{length}|{head_hash}".encode(), hashlib.sha256).hexdigest()

    def append_audit(self, job_id: str, kind: str, actor: str, detail: str = "",
                     data: dict | None = None) -> dict:
        import hashlib

        payload = json.dumps(data or {}, sort_keys=True)
        with self._connect() as c:
            # Acquire the write lock BEFORE reading the tail, so the read-compute-append is atomic.
            # Without this, concurrent writers to the same job read the same last seq and produce
            # DUPLICATE seqs, breaking the hash chain (audit finding: verify_audit would then fail).
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT seq, hash FROM audit_log WHERE job_id=? ORDER BY seq DESC LIMIT 1",
                (job_id,)).fetchone()
            seq = (row["seq"] + 1) if row else 0
            prev_hash = row["hash"] if row else ""
            ts = _now()
            h = hashlib.sha256(
                f"{prev_hash}|{seq}|{ts}|{kind}|{actor}|{detail}|{payload}".encode()).hexdigest()
            entry_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO audit_log (id, job_id, seq, ts, kind, actor, detail, data, "
                "prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (entry_id, job_id, seq, ts, kind, actor, detail, payload, prev_hash, h),
            )
            # Seal the new head (length + terminal hash) so truncation is detectable.
            length = seq + 1
            c.execute(
                "INSERT INTO audit_head (job_id, length, head_hash, head_sig, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET length=excluded.length, "
                "head_hash=excluded.head_hash, head_sig=excluded.head_sig, "
                "updated_at=excluded.updated_at",
                (job_id, length, h, self._seal(length, h), ts),
            )
        return {"seq": seq, "hash": h}

    def get_audit(self, job_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM audit_log WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"] or "{}")
            out.append(d)
        return out

    def verify_audit(self, job_id: str) -> bool:
        """Recompute the hash chain AND check it against the sealed head, so modification,
        reordering, middle-deletion, and tail-truncation are all detected."""
        import hashlib

        entries = self.get_audit(job_id)
        prev_hash = ""
        for i, r in enumerate(entries):
            if r["seq"] != i or r["prev_hash"] != prev_hash:
                return False
            payload = json.dumps(r["data"], sort_keys=True)
            h = hashlib.sha256(
                f"{prev_hash}|{r['seq']}|{r['ts']}|{r['kind']}|{r['actor']}|{r['detail']}|"
                f"{payload}".encode()).hexdigest()
            if h != r["hash"]:
                return False
            prev_hash = h

        with self._connect() as c:
            head = c.execute(
                "SELECT length, head_hash, head_sig FROM audit_head WHERE job_id=?",
                (job_id,)).fetchone()
        if head is None:
            return len(entries) == 0  # a job with no audit rows and no head is consistent
        # The seal must be authentic (unforgeable without the key) AND match the actual chain:
        # a truncated chain has fewer entries / a different terminal hash than the sealed head.
        if self._seal(head["length"], head["head_hash"]) != head["head_sig"]:
            return False
        return len(entries) == head["length"] and prev_hash == head["head_hash"]

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            nodes = c.execute(
                "SELECT * FROM task_nodes WHERE job_id=? ORDER BY order_index", (job_id,)
            ).fetchall()
        job = dict(row)
        for k in ("assumptions", "success_criteria", "open_questions"):
            job[k] = json.loads(job[k] or "[]")
        job["nodes"] = [
            {
                **dict(n),
                "success_criteria": json.loads(n["success_criteria"] or "[]"),
                "depends_on": json.loads(n["depends_on"] or "[]"),
                "needs_hire": bool(n["needs_hire"]),
            }
            for n in nodes
        ]
        return job


def get_store() -> SqliteJobStore:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        return SqliteJobStore(url)
    raise NotImplementedError(
        "Non-sqlite DATABASE_URL requires the SQLAlchemy/Postgres backend (core/db.py), "
        "wired once that dependency is installable. Use a sqlite:/// URL for the offline path."
    )
