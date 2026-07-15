"""API edge security for LAVARD: API-key auth, a per-caller rate limiter, and CORS wiring.

The LAVARD API can spend money (hire/run) and read owner memory, so it must never be open in
prod. Policy:
- `/healthz` is always public (liveness probe).
- If `LAVARD_API_KEY` is set, every other endpoint requires it (header `X-API-Key` or
  `Authorization: Bearer <key>`), in dev or prod.
- If it is unset, requests are allowed ONLY in dev profile; prod refuses to boot without it
  (see Settings.validate_for_prod), so an open prod API is impossible.

The rate limiter is an in-process fixed-window counter keyed by the API key (or client IP when
unauthenticated). Single-node adequate; a multi-replica deployment should back it with Redis — the
seam is `RateLimiter`, swap the store.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request

from core.config import get_settings

PUBLIC_PATHS = frozenset({"/healthz"})


def _presented_key(request: Request) -> str | None:
    key = request.headers.get("X-API-Key")
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _auth_error(request: Request) -> int | None:
    """Return an HTTP status if the request should be rejected, else None (allowed)."""
    if request.url.path in PUBLIC_PATHS:
        return None
    settings = get_settings()
    if not settings.api_key:
        # No key configured. Allowed only in dev; prod can't reach here (fail-fast at boot).
        return 503 if settings.is_prod else None
    presented = _presented_key(request)
    if not presented or not _const_eq(presented, settings.api_key):
        return 401
    return None


async def require_api_key(request: Request) -> None:
    """FastAPI dependency form (kept for explicit per-route use); middleware is the primary path."""
    status = _auth_error(request)
    if status is not None:
        raise HTTPException(status_code=status,
                            detail="missing or invalid API key" if status == 401
                            else "API not configured for secure serving")


def _const_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


class RateLimiter:
    """Fixed-window per-caller limiter. `identity()` picks the key (API key else client IP)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._last_prune = 0.0

    def identity(self, request: Request) -> str:
        return _presented_key(request) or (request.client.host if request.client else "anon")

    def allow(self, request: Request, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        now = now if now is not None else time.time()
        self._prune(now)
        ident = self.identity(request)
        window = self._hits[ident]
        cutoff = now - 60.0
        window[:] = [t for t in window if t > cutoff]   # drop entries older than the window
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True

    def _prune(self, now: float) -> None:
        """Drop identities with no recent hits so the map can't grow unbounded across many callers
        (a memory-leak / DoS vector on a public API). Sweeps at most once per window."""
        if now - self._last_prune < 60.0:
            return
        self._last_prune = now
        cutoff = now - 60.0
        stale = [k for k, ts in self._hits.items() if not ts or ts[-1] <= cutoff]
        for k in stale:
            del self._hits[k]


def install_security(app) -> None:
    """Wire CORS, the rate-limit middleware, and the global auth dependency onto the app."""
    from fastapi import Depends
    from starlette.middleware.cors import CORSMiddleware

    settings = get_settings()

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )

    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def _edge(request: Request, call_next):
        from starlette.responses import JSONResponse

        # auth first — an unauthenticated caller can't consume the rate budget of a valid key
        status = _auth_error(request)
        if status is not None:
            detail = "missing or invalid API key" if status == 401 else \
                ("API not configured for secure serving" if status == 503 else "forbidden")
            return JSONResponse(status_code=status, content={"detail": detail})
        if request.url.path not in PUBLIC_PATHS and not limiter.allow(request):
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)

    _ = Depends  # (require_api_key remains available for explicit per-route use)
