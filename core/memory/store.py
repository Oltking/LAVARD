"""Portable Memory store — owner-scoped, persistent across jobs (§4.5).

Default backend is a dependency-free sqlite file (`LAVARD_MEMORY_URL`), separate from the job DB
because memory outlives any single job. Semantic search reuses the Router's embedder + cosine, so
the same in-memory embeddings work offline; a Qdrant backend (docs/vendor/memory/qdrant.md) drops
in behind this class unchanged. Owner-scoping is enforced by a WHERE filter on every read.
Facts carry confidence + freshness; reads can require a minimum confidence and a maximum age.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid

from core.config import get_settings
from core.memory.redact import redact
from core.memory.schemas import Fact, Playbook
from core.router.embedder import cosine, get_embedder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, topic TEXT, text TEXT, domain TEXT,
    confidence REAL DEFAULT 0.7, freshness_ts REAL, redacted_kinds TEXT DEFAULT '[]', vector TEXT
);
CREATE INDEX IF NOT EXISTS ix_facts_owner ON facts(owner_id);
CREATE TABLE IF NOT EXISTS playbooks (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, goal_shape TEXT, roles TEXT, pitfalls TEXT,
    node_titles TEXT, dag_edges TEXT DEFAULT '[]', crew TEXT DEFAULT '[]',
    uses INTEGER DEFAULT 0, vector TEXT
);
CREATE INDEX IF NOT EXISTS ix_pb_owner ON playbooks(owner_id);
"""


def _path(url: str) -> str:
    return url.split("sqlite:///", 1)[1] if "sqlite:///" in url else url


class MemoryStore:
    def __init__(self, memory_url: str | None = None) -> None:
        self.path = _path(memory_url or get_settings().memory_url)
        self.embedder = get_embedder()
        with self._c() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Additive, idempotent: bring pre-blueprint playbook tables up to schema (DAG + crew)."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(playbooks)").fetchall()}
        if "dag_edges" not in cols:
            c.execute("ALTER TABLE playbooks ADD COLUMN dag_edges TEXT DEFAULT '[]'")
        if "crew" not in cols:
            c.execute("ALTER TABLE playbooks ADD COLUMN crew TEXT DEFAULT '[]'")

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- facts ---
    def add_fact(self, owner_id: str, topic: str, text: str, domain: str = "general",
                 confidence: float = 0.75, freshness_ts: float | None = None,
                 embed_text: str | None = None) -> Fact:
        clean, kinds = redact(text)
        ts = freshness_ts if freshness_ts is not None else time.time()
        # Embed on a concise key (default topic+text) so short node-title queries match well.
        vec = self.embedder.embed(embed_text if embed_text is not None else f"{topic} {clean}")
        fact = Fact(uuid.uuid4().hex, owner_id, topic, clean, domain, confidence, ts, kinds)
        with self._c() as c:
            c.execute(
                "INSERT INTO facts (id, owner_id, topic, text, domain, confidence, freshness_ts, "
                "redacted_kinds, vector) VALUES (?,?,?,?,?,?,?,?,?)",
                (fact.id, owner_id, topic, clean, domain, confidence, ts,
                 json.dumps(kinds), json.dumps(vec)),
            )
        return fact

    def search_facts(self, owner_id: str, query: str, *, min_conf: float = 0.0,
                     max_age_s: float = math.inf, top_k: int = 5,
                     threshold: float = 0.6, now: float | None = None) -> list[tuple[Fact, float]]:
        now = now if now is not None else time.time()
        qv = self.embedder.embed(query)
        out: list[tuple[Fact, float]] = []
        with self._c() as c:
            rows = c.execute("SELECT * FROM facts WHERE owner_id=?", (owner_id,)).fetchall()
        for r in rows:
            if r["confidence"] < min_conf:
                continue
            if (now - r["freshness_ts"]) > max_age_s:
                continue
            sim = cosine(qv, json.loads(r["vector"]))
            if sim < threshold:
                continue
            out.append((self._fact(r), sim))
        out.sort(key=lambda t: t[1], reverse=True)
        return out[:top_k]

    def list_facts(self, owner_id: str) -> list[Fact]:
        with self._c() as c:
            rows = c.execute("SELECT * FROM facts WHERE owner_id=?", (owner_id,)).fetchall()
        return [self._fact(r) for r in rows]

    # --- playbooks ---
    def add_playbook(self, owner_id: str, goal_shape: str, roles: list[str],
                     pitfalls: list[str], node_titles: list[str],
                     dag_edges: list[list[str]] | None = None,
                     crew: list[dict] | None = None) -> Playbook:
        vec = self.embedder.embed(goal_shape)
        dag_edges = dag_edges or []
        crew = crew or []
        pb = Playbook(uuid.uuid4().hex, owner_id, goal_shape, roles, pitfalls, node_titles,
                      dag_edges, crew, 0)
        with self._c() as c:
            c.execute(
                "INSERT INTO playbooks (id, owner_id, goal_shape, roles, pitfalls, node_titles, "
                "dag_edges, crew, uses, vector) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pb.id, owner_id, goal_shape, json.dumps(roles), json.dumps(pitfalls),
                 json.dumps(node_titles), json.dumps(dag_edges), json.dumps(crew), 0,
                 json.dumps(vec)),
            )
        return pb

    def match_playbook(self, owner_id: str, goal: str, *, threshold: float = 0.6,
                       ) -> tuple[Playbook, float] | None:
        qv = self.embedder.embed(goal)
        best: tuple[Playbook, float] | None = None
        with self._c() as c:
            rows = c.execute("SELECT * FROM playbooks WHERE owner_id=?", (owner_id,)).fetchall()
        for r in rows:
            sim = cosine(qv, json.loads(r["vector"]))
            if sim >= threshold and (best is None or sim > best[1]):
                best = (self._playbook(r), sim)
        return best

    def summary(self) -> dict:
        """Global memory footprint for the OS overview (playbooks/blueprints + facts + reuse)."""
        with self._c() as c:
            pb = c.execute("SELECT COUNT(*) n, COALESCE(SUM(uses),0) uses FROM playbooks").fetchone()
            f = c.execute("SELECT COUNT(*) n FROM facts").fetchone()
        return {"blueprints": pb["n"] or 0, "blueprint_uses": pb["uses"] or 0,
                "facts": f["n"] or 0}

    def bump_use(self, playbook_id: str) -> None:
        with self._c() as c:
            c.execute("UPDATE playbooks SET uses = uses + 1 WHERE id=?", (playbook_id,))

    def list_playbooks(self, owner_id: str) -> list[Playbook]:
        with self._c() as c:
            rows = c.execute("SELECT * FROM playbooks WHERE owner_id=?", (owner_id,)).fetchall()
        return [self._playbook(r) for r in rows]

    @staticmethod
    def _fact(r: sqlite3.Row) -> Fact:
        return Fact(r["id"], r["owner_id"], r["topic"], r["text"], r["domain"],
                    r["confidence"], r["freshness_ts"], json.loads(r["redacted_kinds"] or "[]"))

    @staticmethod
    def _playbook(r: sqlite3.Row) -> Playbook:
        keys = r.keys()
        dag = json.loads(r["dag_edges"]) if "dag_edges" in keys and r["dag_edges"] else []
        crew = json.loads(r["crew"]) if "crew" in keys and r["crew"] else []
        return Playbook(r["id"], r["owner_id"], r["goal_shape"], json.loads(r["roles"]),
                        json.loads(r["pitfalls"]), json.loads(r["node_titles"]), dag, crew,
                        r["uses"])


def get_memory() -> MemoryStore:
    return MemoryStore()
