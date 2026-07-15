"""Runtime configuration — stdlib only, everything swappable via env, nothing vendor-locked.

Design rules honored here:
- No inference-vendor lock-in (spec §0.5, QUESTIONS.md Q-MODEL-1): the model backend is an
  OpenAI-compatible endpoint whose URL/key/model-names come from env. If unset, the planner
  falls back to a deterministic heuristic so the demo still runs offline.
- Persistence default is stdlib sqlite3 (zero infra). Set LAVARD_DATABASE_URL to a
  postgresql+... URL to use the SQLAlchemy/Postgres production backend (core/db.py).
- Budget/turn defaults come from QUESTIONS.md Q-BUDGET-1 (interim, owner to confirm).

Reads a local .env (KEY=VALUE lines) if present, then process env (env wins).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"LAVARD_{name}", default)


# Dev fallback so the offline demo runs; production MUST set LAVARD_AUDIT_KEY to a secret held
# outside the database (an attacker with DB access but not this key cannot forge the sealed head).
_DEFAULT_AUDIT_KEY = "lavard-dev-audit-key-do-not-use-in-prod"


@dataclass(frozen=True)
class Settings:
    # persistence
    database_url: str
    memory_url: str          # portable memory store (persists across jobs, owner-scoped)
    redis_url: str | None
    # model backend (OpenAI-compatible; provider-agnostic)
    model_endpoint: str | None
    model_api_key: str | None
    model_trivial: str
    model_routine: str
    model_complex: str
    model_critical: str
    # governance / referee defaults (Q-BUDGET-1)
    job_budget_usd: float
    room_turn_limit: int
    agent_turn_limit: int
    auto_spend_ceiling_usd: float   # per-action spend at/below this clears ask-once; above escalates
    # HMAC key sealing the audit-log head so tail-truncation can't be forged (Phase 8 hardening).
    audit_key: str
    # Route paid Agent-to-MCP calls through TheHouse's aggregator for the ~20% batch discount.
    use_thehouse: bool
    # Deployment profile: "dev" (open, offline) | "prod" (edge auth + fail-fast required).
    profile: str
    # API edge security. api_key gates every mutating/data endpoint; comma-separated allowed
    # CORS origins; per-caller request budget per minute (0 disables).
    api_key: str
    cors_origins: str
    rate_limit_per_minute: int

    @property
    def model_configured(self) -> bool:
        return bool(self.model_endpoint and self.model_api_key)

    @property
    def is_prod(self) -> bool:
        return self.profile == "prod"

    @property
    def audit_key_is_default(self) -> bool:
        return self.audit_key == _DEFAULT_AUDIT_KEY

    def validate_for_prod(self) -> list[str]:
        """FATAL prod misconfigurations (boot is refused if any). Security invariants only."""
        problems: list[str] = []
        if not self.is_prod:
            return problems
        if self.audit_key_is_default:
            problems.append("LAVARD_AUDIT_KEY is the built-in dev key — set a real secret in prod.")
        if not self.api_key:
            problems.append("LAVARD_API_KEY is unset — the API would be open; set an edge key.")
        return problems

    def prod_warnings(self) -> list[str]:
        """Non-fatal prod advisories (logged, not blocking). sqlite (WAL) is fine for a single
        node; Postgres is the documented path for multi-replica HA (see docs/persistence.md)."""
        warnings: list[str] = []
        if not self.is_prod:
            return warnings
        if self.database_url.startswith("sqlite"):
            warnings.append("LAVARD_DATABASE_URL is sqlite (single-node only) — use Postgres for HA.")
        return warnings


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        database_url=_get("DATABASE_URL", "sqlite:///./lavard.db"),
        memory_url=_get("MEMORY_URL", "sqlite:///./lavard_memory.db"),
        redis_url=_get("REDIS_URL"),
        model_endpoint=_get("MODEL_ENDPOINT"),
        model_api_key=_get("MODEL_API_KEY"),
        model_trivial=_get("MODEL_TRIVIAL", "gpt-4o-mini"),
        model_routine=_get("MODEL_ROUTINE", "gpt-4o-mini"),
        model_complex=_get("MODEL_COMPLEX", "gpt-4o"),
        model_critical=_get("MODEL_CRITICAL", "gpt-4o"),
        job_budget_usd=float(_get("JOB_BUDGET_USD", "25") or 25),
        room_turn_limit=int(_get("ROOM_TURN_LIMIT", "40") or 40),
        agent_turn_limit=int(_get("AGENT_TURN_LIMIT", "8") or 8),
        auto_spend_ceiling_usd=float(_get("AUTO_SPEND_CEILING_USD", "50") or 50),
        audit_key=_get("AUDIT_KEY", _DEFAULT_AUDIT_KEY) or _DEFAULT_AUDIT_KEY,
        use_thehouse=(_get("USE_THEHOUSE", "1") or "1").lower() not in ("0", "false", "no"),
        profile=(_get("PROFILE", "dev") or "dev").lower(),
        api_key=_get("API_KEY", "") or "",
        cors_origins=_get("CORS_ORIGINS", "") or "",
        rate_limit_per_minute=int(_get("RATE_LIMIT_PER_MINUTE", "120") or 120),
    )
