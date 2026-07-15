"""FastAPI surface for LAVARD (Phase 1).

Endpoints:
  POST /jobs        {"goal": "..."}  -> verified intake + task graph (JobView)
  GET  /jobs/{id}                     -> JobView
  GET  /healthz

Run: uvicorn api.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import get_settings
from core.schemas import JobView
from core.service import get_job, submit_goal
from core.store import get_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: in prod, refuse to serve with the default audit key or no API key (open API).
    settings = get_settings()
    problems = settings.validate_for_prod()
    if problems:
        raise RuntimeError("LAVARD prod misconfiguration:\n  - " + "\n  - ".join(problems))
    for w in settings.prod_warnings():
        logging.getLogger("lavard").warning("prod advisory: %s", w)
    get_store()  # ensures the store (and its schema) is initialized
    app.state.executor = None
    app.state.aggregator = None
    app.state.settlement = None
    app.state.rail_settlement = None
    # Blend: build TheHouse's aggregator on the main loop and route paid Agent-to-MCP hires through
    # it for the ~20% batch discount. Guarded — any failure leaves MCP hires on the escrow path.
    if get_settings().use_thehouse:
        try:
            from core.execution import build_thehouse_executor
            from thehouse.core.storage.db import get_engine, init_db
            from thehouse.core.storage.redis_client import get_redis

            await init_db()
            executor = build_thehouse_executor(get_engine(), get_redis())
            await executor.aggregator.reconcile()   # re-queue paid work lost to a crash
            executor.aggregator.start_sweeper()
            app.state.aggregator = executor.aggregator
            app.state.executor = executor
            # Deferred x402 settlement: when OKX creds are configured, run the background poller
            # that advances aggr_deferred settlements (accepted -> settled|failed) off-chain-truth.
            from thehouse.onchain.settlement import build_settlement_service

            svc = build_settlement_service(get_engine())
            if svc is not None:
                reconciler, facilitator = svc
                reconciler.start_poller(facilitator)
                app.state.settlement = reconciler
            # a2a-pay rail reconciler (the PROVEN settlement rail): advance pending charges → settled.
            from thehouse.onchain.settlement_rail import build_rail_settlement

            rail = build_rail_settlement(get_engine())
            if rail is not None:
                rail.start_poller()
                app.state.rail_settlement = rail
        except Exception:
            logging.getLogger("lavard").exception(
                "TheHouse executor not wired; Agent-to-MCP hires fall back to escrow")
    yield
    if getattr(app.state, "rail_settlement", None) is not None:
        await app.state.rail_settlement.stop_poller()
    if getattr(app.state, "settlement", None) is not None:
        await app.state.settlement.stop_poller()
    if app.state.aggregator is not None:
        await app.state.aggregator.stop_sweeper()


app = FastAPI(
    title="LAVARD",
    version="0.1.0",
    summary="Orchestration ASP for OKX AI",
    lifespan=lifespan,
)

# Edge security: API-key auth (all endpoints except /healthz), per-caller rate limiting, CORS.
# Open only in dev with no key set; prod fails to boot without a key (validate_for_prod).
from api.security import install_security  # noqa: E402

install_security(app)


class SubmitGoalRequest(BaseModel):
    goal: str = Field(min_length=1, description="Plain-language goal for LAVARD to run.")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "lavard", "version": app.version}


@app.get("/os")
def read_os(request: Request) -> dict:
    """LAVARD OS overview + live network-effects telemetry (memory / liquidity / reputation)."""
    from core.os_overview import os_overview

    return os_overview(exchange=getattr(request.app.state, "exchange", None))


@app.post("/jobs", response_model=JobView, status_code=201)
def create_job(req: SubmitGoalRequest) -> JobView:
    try:
        return submit_goal(req.goal)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class RunJobRequest(BaseModel):
    goal: str = Field(min_length=1, description="Plain-language goal for LAVARD to run end-to-end.")
    demo: bool = False
    auto_signoff: bool = Field(
        default=False, description="Release escrow automatically (off = stop at sign-off).")
    preference: str = Field(
        default="balanced",
        description="Agent-selection objective: cheapest | fastest | smartest | balanced.")


@app.post("/run", status_code=201)
def run_job_endpoint(req: RunJobRequest, request: Request) -> dict:
    """One-shot conductor: submit -> classify -> hire -> room -> (await) sign-off, honoring the
    cheapest-sufficient path. Returns a JobRun with the answer/deliverables and the next action."""
    from core.conductor import run_job

    executor = getattr(request.app.state, "executor", None)
    try:
        return run_job(req.goal, executor=executor, demo=req.demo,
                       auto_signoff=req.auto_signoff, preference=req.preference).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/jobs/{job_id}", response_model=JobView)
def read_job(job_id: str) -> JobView:
    view = get_job(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return view


@app.get("/candidates/{capability}")
def read_candidates(capability: str, limit: int = 5) -> list[dict]:
    """Candidate ASPs for a capability, with onchain reputation, best-first (Phase 2)."""
    from core.foreman import find_candidates

    return [c.to_dict() for c in find_candidates(capability, limit=limit)]


@app.get("/vet/{agent_id}")
def read_vet(agent_id: str) -> dict:
    """Vetter verdict for an agent: trust + confidence + evidence + limits (Phase 3)."""
    from core.vetter import vet_agent

    return vet_agent(agent_id).to_dict()


@app.post("/jobs/{job_id}/hire")
def hire_job(job_id: str, request: Request) -> list[dict]:
    """Necessity-test, vet, and hire specialists (Phase 4). Agent-to-MCP candidates are serviced
    pay-per-call through TheHouse when the aggregator is wired (cheaper); A2A use escrow."""
    from core.foreman import hire_for_job

    executor = getattr(request.app.state, "executor", None)
    try:
        return [o.to_dict() for o in hire_for_job(job_id, executor=executor)]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/jobs/{job_id}/signoff")
def signoff_job(job_id: str) -> dict:
    """User sign-off: release every open escrow for the job (Phase 4)."""
    from core.foreman import sign_off

    return sign_off(job_id).to_dict()


@app.post("/jobs/{job_id}/room")
def run_job_room(job_id: str, request: Request, demo: bool = True, resume: bool = False) -> dict:
    """Run the controller-mediated Room + first-responder loop + referee (Phase 5).

    `resume=true` continues an interrupted job from its checkpoint (Phase 10). When not in demo
    mode, real hires deliver through the wired executor (ExecutorRoomAgent)."""
    from core.room import run_room

    executor = getattr(request.app.state, "executor", None)
    return run_room(job_id, demo=demo, resume=resume, executor=executor).to_dict()


@app.post("/jobs/{job_id}/kill")
def kill_job_room(job_id: str) -> dict:
    """Global kill-switch: freeze the room at the next turn boundary (Phase 5)."""
    from core.store import get_store

    get_store().freeze_room(job_id)
    return {"job_id": job_id, "frozen": True}


@app.post("/jobs/{job_id}/distill")
def distill_job_endpoint(job_id: str) -> dict:
    """On close: redact + store facts and a reusable Playbook into Portable Memory (Phase 7)."""
    from core.memory import distill_job

    try:
        return distill_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/jobs/{job_id}/suggestions")
def read_suggestions(job_id: str) -> dict:
    """Predicted next tasks after this job — prepared and crew-preselected, but NOT run.
    Money-safe: nothing is hired or spent until the user approves one."""
    from core.governance import audit
    from core.predict import predict_for_job

    suggestions = [s.to_dict() for s in predict_for_job(job_id)]
    # Suggestions are prepared, never executed: no hire, no escrow, no spend (always-ask preserved).
    audit(job_id, "suggestions_generated", "conductor",
          f"{len(suggestions)} next-task suggestion(s) prepared — zero spend",
          {"count": len(suggestions), "spent_usd": 0.0})
    return {"job_id": job_id, "suggestions": suggestions, "spent_usd": 0.0}


@app.get("/jobs/{job_id}/report")
def read_report(job_id: str) -> dict:
    """Per-job report + immutable audit log (Phase 8)."""
    from core.governance import build_report

    try:
        return build_report(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ReviewRequest(BaseModel):
    description: str
    type: str = "spend"
    amount_usd: float = 0.0
    target: str = ""


@app.post("/review")
def post_review(req: ReviewRequest) -> dict:
    """Action Review verdict for a proposed action (Phase 8)."""
    from core.governance import Action, review_action

    return review_action(
        Action(req.type, req.description, amount_usd=req.amount_usd, target=req.target)
    ).to_dict()


@app.get("/mcp/tools")
def read_mcp_tools() -> list[dict]:
    """LAVARD's exposed MCP tool surface (Phase 9)."""
    from mcp import list_tools

    return list_tools()


@app.get("/golive")
def read_golive() -> dict:
    """Listing manifest + internal readiness review + publish result (Phase 9)."""
    from mcp import build_listing, publish, readiness_review

    listing = build_listing()
    return {"listing": listing, "review": readiness_review(listing), "publish": publish()}


@app.get("/memory")
def read_memory(owner: str = "default-owner") -> dict:
    """An owner's Portable Memory: playbooks + facts (Phase 7)."""
    from core.memory import get_memory

    mem = get_memory()
    return {
        "playbooks": [p.to_dict() for p in mem.list_playbooks(owner)],
        "facts": [f.to_dict() for f in mem.list_facts(owner)],
    }
