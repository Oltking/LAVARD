"""API edge security: key auth, rate limiting, and prod fail-fast."""

import time

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

from api.security import RateLimiter, install_security
from core.config import Settings, get_settings


def _settings(**over):
    base = dict(
        database_url="sqlite:///./x.db", memory_url="sqlite:///./m.db", redis_url=None,
        model_endpoint=None, model_api_key=None, model_trivial="m", model_routine="m",
        model_complex="m", model_critical="m", job_budget_usd=25.0, room_turn_limit=40,
        agent_turn_limit=8, auto_spend_ceiling_usd=50.0, audit_key="k", use_thehouse=False,
        profile="dev", api_key="", cors_origins="", rate_limit_per_minute=120,
    )
    base.update(over)
    return Settings(**base)


def _app_with(settings, monkeypatch):
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    monkeypatch.setattr("api.security.get_settings", lambda: settings)
    app = FastAPI()

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.get("/jobs/x")
    def secret():
        return {"data": "sensitive"}

    install_security(app)
    return TestClient(app)


def test_healthz_is_public_but_data_requires_key(monkeypatch):
    client = _app_with(_settings(api_key="s3cret"), monkeypatch)
    assert client.get("/healthz").status_code == 200
    assert client.get("/jobs/x").status_code == 401                       # no key
    assert client.get("/jobs/x", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/jobs/x", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/jobs/x", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_dev_without_key_is_open():
    # dev + no key → open (offline convenience); prod can't reach this (fail-fast)
    s = _settings(profile="dev", api_key="")
    assert s.validate_for_prod() == []


def test_prod_failfast_lists_problems():
    from core.config import _DEFAULT_AUDIT_KEY
    s = _settings(profile="prod", api_key="", audit_key=_DEFAULT_AUDIT_KEY,
                  database_url="sqlite:///./x.db")
    problems = s.validate_for_prod()
    assert any("AUDIT_KEY" in p for p in problems)   # fatal: security invariant
    assert any("API_KEY" in p for p in problems)     # fatal: open API
    assert not any("sqlite" in p for p in problems)  # sqlite is NOT fatal
    assert any("sqlite" in w for w in s.prod_warnings())  # …it's a non-blocking advisory
    # a properly configured prod passes (sqlite single-node is allowed, just warned)
    ok = _settings(profile="prod", api_key="k", audit_key="realsecret")
    assert ok.validate_for_prod() == []


def test_rate_limiter_blocks_over_budget():
    limiter = RateLimiter(per_minute=3)

    def req():
        scope = {"type": "http", "headers": [(b"x-api-key", b"c1")], "client": ("1.2.3.4", 0)}
        return Request(scope)

    now = time.time()
    assert all(limiter.allow(req(), now=now) for _ in range(3))
    assert limiter.allow(req(), now=now) is False          # 4th within the minute → blocked
    assert limiter.allow(req(), now=now + 61) is True      # window rolled over
