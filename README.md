# LAVARD

**The autonomous AI operating system for OKX AI.**
You state a goal. LAVARD plans the work, hires the best specialist agents, batches requests to cut
cost, runs them reliably, settles on-chain, and compounds what it learns — so the whole system gets
smarter and cheaper the more it runs.

Users never pick agents, manage crews, or think about batching. They say what they want; two
invisible engines do the rest:

- **LAVARD OS** — gets the best work done (plan → vet → hire → run → sign-off).
- **TheHouse** — gets it done cheaper (invisible request batching + shared answers on OKX AI).

Status: **278 tests passing**, five deep security audits, and a **real on-chain settlement proven on
X Layer mainnet** (`0xa65fd7203bb759aa82eb6dc904b2869e079fc00f8abbce74ec60f1d8a7f5e701`).

---

## Table of contents

1. [What it is](#1-what-it-is)
2. [The layered architecture](#2-the-layered-architecture)
3. [The end-to-end flow (a → z)](#3-the-end-to-end-flow-a--z)
4. [The conductor — one entrypoint](#4-the-conductor--one-entrypoint)
5. [TheHouse — the batching engine](#5-thehouse--the-batching-engine)
6. [The money model](#6-the-money-model)
7. [OKX AI / X Layer integration](#7-okx-ai--x-layer-integration)
8. [Intelligence features](#8-intelligence-features)
9. [Privacy & data governance](#9-privacy--data-governance)
10. [Security](#10-security)
11. [Trust & observability](#11-trust--observability)
12. [Repository layout](#12-repository-layout)
13. [Configuration](#13-configuration)
14. [Running it](#14-running-it)
15. [The frontend](#15-the-frontend)
16. [Audit history](#16-audit-history)
17. [Production readiness](#17-production-readiness)
18. [The real-money test runbook](#18-the-real-money-test-runbook)

---

## 1. What it is

LAVARD is an **orchestration Agent Service Provider (ASP)** built for **OKX AI** — the marketplace
where AI agents discover work, transact, and build reputation on-chain. A user hands LAVARD a
plain-language goal; LAVARD decomposes it, vets and hires specialist agents from the marketplace,
runs them as a controller-mediated "room," settles via on-chain escrow / x402, and distills
everything into portable memory it reuses.

**TheHouse** is a request-aggregation broker bundled into LAVARD as a namespaced sub-package. It
batches N callers' compatible requests to the same provider into one paid call and splits the
response back — each caller pays ~20% less. It is the "invisible engine" that makes LAVARD cheaper.

Two design constants run through everything:

- **`core/` is pure-stdlib** (dataclasses, `sqlite3`, no heavy deps) so the whole thing runs offline
  and deterministically with nothing installed. FastAPI / SQLAlchemy / pydantic are the optional
  production layer.
- **Honesty invariants** — no fabricated data, loud failures over silent drift, a discount is only
  ever quoted when backed by a real saving, side-effectful calls are never merged or cached.

---

## 2. The layered architecture

```
                         USER  (a goal, in plain language)
                           │
                           ▼
                     ┌─────────────┐
                     │  LAVARD OS  │  single interface — you state a goal
                     ├─────────────┤
   Planner           │ verify-first intake + task-graph decomposition
   Optimization Eng. │ cost / speed / quality / reputation, per preference
   Memory System     │ reusable workflow blueprints + facts
   Reputation Graph  │ multi-dimensional, execution-history-backed scoring
   Workflow+Security │ controller-mediated room · referee · governance
                     ├─────────────┤
   Agent Router      │ cheapest-accurate routing · semantic cache · dedup
                     ├─────────────┤
   TheHouse Broker   │ invisible request batching + shared answers
                     ├─────────────┤
   OKX AI Market     │ specialist ASPs · onchain identity + settlement (X Layer)
                     └─────────────┘
```

To the user there is only LAVARD. Every layer does one job and hands down to the next; the same
request flows from a goal to an on-chain settlement without the user leaving the front door.

---

## 3. The end-to-end flow (a → z)

A job's life, traced through the real code:

1. **Submit** — `core/service.py::submit_goal(goal, owner_id)` creates the job, audits
   `job_created`, and surfaces a matching **Playbook** if the owner ran a similar goal before
   (reuse-on-intake). Goal length is capped (`MAX_GOAL_CHARS = 8000`).
2. **Intake** — `core/intake` verifies the goal: restated goal, assumptions, success criteria, open
   questions.
3. **Decompose** — `core/foreman/decompose.py` turns the goal into a validated task-graph DAG (LLM
   when configured, else a deterministic keyword heuristic; cycles rejected via Kahn's algorithm).
   Each node carries a `capability` and a `needs_hire` flag (the necessity-test seed).
4. **Classify path** — `core/intake/router.py::classify_path` labels the job
   `direct_mcp | single_asp | orchestrate` (conservative: ambiguity → orchestrate) and persists it.
5. **Hire** — `core/foreman/hire.py::hire_for_job`, per node:
   - **necessity test** (coordination/self-serviceable nodes skipped),
   - **memory reuse** (Portable Memory already answers it → skip the hire),
   - **candidates + Vetter** (`core/vetter`) — trust + confidence + honest limits,
   - **Optimization Engine** ranks candidates under the preference,
   - **governance gate** (`core/governance`) — spend over the ceiling escalates to the user,
   - **execute**: Agent-to-MCP via TheHouse (`serviced_mcp`, records savings), else A2A escrow.
   - **Idempotent**: a node that already has an active hire is skipped (no double-escrow on retry).
6. **Room** — `core/room/controller.py::run_room` runs each node through the **Referee**
   (turn/budget/kill-switch/loop guards) with LAVARD as **first responder** (answer from memory →
   poll a peer → hire a new specialist). **Live-crew optimization** retires a genuinely failed agent
   mid-run and hires a replacement, carrying blackboard state forward. Checkpoints after every node
   → resume-after-crash. Idempotent: a completed room is a no-op on retry.
7. **Sign-off** — `sign_off(job_id)` releases every open escrow to the recorded payee address (money
   leaves escrow only on explicit approval). Idempotent.
8. **Distill** — `core/memory/distill.py` redacts and stores durable facts + a reusable Playbook
   (DAG shape, ideal crew, prompt patterns), and contributes the anonymized capability shape to the
   global insights model.
9. **Report** — `core/governance/report.py` returns the per-job report + the hash-chained,
   HMAC-sealed audit log with an integrity check.

Low-need goals short-circuit: a single deterministic tool call is serviced **agent-to-MCP** (one
paid call, no room); a single specialist deliverable is one **agent-to-ASP** hire.

---

## 4. The conductor — one entrypoint

`core/conductor.py::run_job(goal, owner_id, *, executor, demo, resume, auto_signoff, preference)`
drives the whole arc in one call and adapts to the classified path:

- `direct_mcp` → serviced pay-per-call, returns the answer (terminal, no room).
- `single_asp` / `orchestrate` → hire → room → **awaiting sign-off** (money never auto-released
  unless `auto_signoff=True`).

The path is a hint that adapts to reality: a `direct_mcp` goal whose best candidate turns out to be
an A2A agent falls through to escrow — "however it can logically work."

`JobRun` reports the **full money exposure** with a breakdown so nothing is hidden:
`spend_usd = committed_escrow_usd + coordination_usd + mcp_usd` (committed escrow includes both node
hires and mid-room helper hires).

Exposed as `POST /run` and the `lavard run` CLI, with a `--preference` of
`cheapest | fastest | smartest | balanced`.

---

## 5. TheHouse — the batching engine

TheHouse (`thehouse/`) batches callers' compatible requests to the same provider. Four modes:

- **A_llm** — numbered compound prompt to an LLM ASP, split by numeric anchor.
- **B_native** — pack array-param requests into one native multi-parameter call, split by key.
- **B_fanout** — parallel dispatch with a coordination fee (no discount).
- **non_aggregatable** — direct route at full price (never a loss leader).

**The window** (`thehouse/core/window`): one per target ASP. Fires on (1) reaching break-even size,
(2) a priority request, or (3) the timer expiring. The fire is lock-guarded and drains atomically;
a request that races in after the drain re-opens the window so it can't be orphaned.

**Pipeline** (`thehouse/core/pipeline.py`): fired window → compose/pack → dispatch → split → deliver
→ settle. Safety rails built in:

- **Privacy-preserving split isolation** — a caller never receives the compound blob; a segment that
  can't be isolated is re-dispatched individually.
- **Prompt-injection isolation** — a query that tries to steer the shared compound is pulled out and
  dispatched alone, so it can never poison a peer's answer.
- **Solo short-circuit** — a single-caller batch dispatches directly (no compose/split), removing
  the mis-split double-charge tail.
- **Merged-caller settlement** attributed to the real caller; an unidentifiable caller is never
  booked (keeps the ledgers balanced under load).

**Auto-protection** (nightly): an ASP with >30% below-break-even batches gets its window extended;
>60% is demoted to parallel route; repeated split failures trigger re-profiling / manual review.

---

## 6. The money model

**Conditional pricing (Model B):** the discount is earned only when a batch actually forms, so a
price is never quoted that isn't backed by a real saving, and a caller can never be charged more
than they authorized.

| Tier | Price | When |
|------|-------|------|
| **Batched** (≥ 2 payers share a call) | **−20%** (`thehouse_price`) | a real aggregation happened |
| **Solo** (batch didn't form) | **full − 0.1%** | still beats going direct |
| **Priority** | `original − $0.01` | fires immediately/solo on purpose |
| **Ceiling** (authorized up front) | ≤ full | settled the lower actual amount |

The tier is decided **at fire time**, by the true paying-caller count (fired requests + merged
members), not at intake.

**Two settlement directions, two rails** (the correct model, established during the audits):

- **Inbound** (caller → TheHouse) = the caller's signed **x402 authorization**, settled by TheHouse
  as the seller. Recorded as collected; reconciliation counts only `settled` money so an uncollected
  charge surfaces as drift.
- **Outbound** (TheHouse → target) = **a2a-pay** (EIP-3009 charge), the buyer rail proven live on
  mainnet (`thehouse/onchain/a2a_pay.py`, `settlement_rail.py`).

**LAVARD A2A escrow** (`onchain/`, via the `onchainos` CLI): `create-task → set-payment-mode →
confirm-accept` opens escrow; `complete` releases on sign-off; `reject` refunds.

---

## 7. OKX AI / X Layer integration

The real integration surface is the **`onchainos` CLI** (installable via
`npx skills add okx/onchainos-skills`, also runnable as an MCP server), not a hand-rolled REST API.
Full reference in [`docs/xlayer_integration.md`](docs/xlayer_integration.md).

**X Layer:** chain id `196` (CAIP-2 `eip155:196`), gas token OKB, USDT/USDC/USDG at 6 decimals,
zero-gas sponsored transfers, OP-Stack L2. Testnet is `eip155:1952`.

**Live-verified (2026-07-14/15):**

- Our `OnchainOSFacilitator` client's OK-ACCESS signing works on GET and POST against the real
  facilitator (`https://web3.okx.com/api/v6/pay/x402`).
- Schemes offered on X Layer: `exact`, `aggr_deferred`, **`upto`** (authorize-ceiling / settle-less,
  facilitator `0x40817a0d…`), `period`.
- **A real settlement completed on mainnet** — `a2a-pay create → pay → completed`, tx
  `0xa65fd7203bb759aa82eb6dc904b2869e079fc00f8abbce74ec60f1d8a7f5e701` (verifiable on OKLink).
- Finding that shaped the design: the raw facilitator `/verify`+`/settle` are seller/payTo-gated;
  **a2a-pay is the working agent-to-agent charge rail** for the buyer direction.

Identity is **ERC-8004**; disputes go to the "Internet Court" with a 5% arbitration bounty.

---

## 8. Intelligence features

- **Reputation Graph** (`core/reputation/graph.py`) — records every execution outcome (completion,
  latency, cost, recovery, reuse) and computes a multi-dimensional score, with a marketplace + trust
  cold-start prior.
- **Optimization Engine** (`core/reputation/optimizer.py`) — weighted, explainable candidate
  selection under `cheapest | fastest | smartest | balanced`; low-trust excluded; factors the live
  quoted price so "cheapest" works from day one.
- **Intelligence Exchange** (`core/router/exchange.py`) — an "AI CDN": many callers asking the same
  *public, read-only* question resolve to one upstream call with a shared answer (TTL-evicted).
  Personalized/mutating queries are never shared.
- **Reusable Workflow Blueprints** (`core/memory`) — a finished job's DAG + ideal crew, reused on
  intake to skip planning and pre-select proven crew. Owner-scoped; anonymized shape only for the
  global model.
- **Predictive next-tasks** (`core/predict.py`) — from history, likely follow-on tasks are
  pre-planned and crew-preselected. **Zero-spend** — nothing is hired until the user approves.
- **Global aggregate learning** (`core/insights.py`) — anonymized capability-co-occurrence learned
  across all users (no owner, no content) so predictions sharpen collectively.
- **Network-effects telemetry** (`core/os_overview.py`, `GET /os`) — memory / liquidity / reputation
  metrics from real activity.

---

## 9. Privacy & data governance

Three tiers, one rule each — full statement in [`docs/privacy.md`](docs/privacy.md):

- **Tier 1 — Private** (facts, blueprints, goals): owner-scoped everywhere; owner A's memory is
  never returned to owner B; captured through redaction.
- **Tier 2 — Aggregate learning** (global, zero user content): only capability-pair statistics; an
  `assert_aggregate_safe` guard rejects any record carrying user-identifying fields.
- **Tier 3 — Public knowledge** (the Intelligence Exchange): only public, non-personalized lookups
  are shared; the shared answer never carries who first asked.

Provider (agent) reputation is deliberately global — it rates public marketplace agents, not users.

---

## 10. Security

- **Redaction** (`core/memory/redact.py`) — scrubs API keys, hex/Ethereum private keys, AWS keys,
  bearer tokens, seed phrases (BIP-39-anchored / labeled, incl. `wallet:`), labeled passwords, and
  emails at capture, before anything is written to memory.
- **Prompt-injection isolation** — an injection-shaped query is pulled out of the shared compound so
  it can't poison a peer (`thehouse/core/composer/service.py::is_injection_risk`).
- **API edge** (`api/security.py`) — API-key auth (`X-API-Key` / `Bearer`), a per-caller
  fixed-window rate limiter (bounded memory), and CORS. Open only in dev with no key set.
- **Prod fail-fast** — the API refuses to boot in prod with the default audit key or no API key.
- **Always-ask spending** — money leaves escrow only on explicit sign-off; spend over the ceiling
  escalates.

---

## 11. Trust & observability

- **Hash-chained, HMAC-sealed audit log** — every action is tamper-evident. Appends use
  `BEGIN IMMEDIATE` + a unique `(job_id, seq)` index so the chain survives concurrent writes;
  `verify_audit` recomputes the chain and checks the sealed head (detects truncation).
- **Reconciliation** — `EconomicsEngine.reconcile_settlements` cross-checks collected-on-chain vs
  billed and flags drift; balanced under 600-concurrent load.
- **Alert seam** (`core/observability.py`) — structured alerts on `escrow_released`, `budget_halt`,
  `settlement_failed`, `settlement_drift`, `audit_verification_failed`; a broken sink can never break
  the money path.

---

## 12. Repository layout

```
core/                       LAVARD — pure-stdlib orchestration core
  conductor.py              one entrypoint: run_job(...)
  service.py                submit_goal / process_job / get_job
  intake/                   verify-first intake + path classifier (router.py)
  foreman/                  decompose, market, hire, sign-off
  vetter/                   confidence-scored trust verdicts
  room/                     controller-mediated room, referee, live-crew, agents
  reputation/               reputation graph + optimization engine
  memory/                   distill, redact, reuse, blueprints (Portable Memory)
  router/                   cheapest-accurate router, semantic cache, intelligence exchange
  governance/               action review, permission tiers, audit, report
  execution/                McpExecutor seam (TheHouse / direct)
  predict.py insights.py    predictive next-tasks + global aggregate learning
  observability.py privacy.py os_overview.py
  store.py                  stdlib sqlite store (audit chain, checkpoints, reputation)
onchain/                    OKX onchainos CLI adapter (marketplace, identity, escrow)
api/                        FastAPI surface + edge security
mcp/                        MCP tool surface + go-live listing
thehouse/                   TheHouse — request-aggregation broker (async SQLAlchemy)
  core/  window/ pipeline.py pricing.py composer/ splitter/ packer/ deduplicator/
         intake/ economics/ profiler/ storage/
  onchain/                  x402 facilitator, a2a-pay rail, settlement, payments
  gateway/                  402-gated MCP gateway
  migrations/               Alembic
docs/                       xlayer_integration.md, privacy.md, persistence.md, vendor/
tests/  thehouse/tests/     278 tests
cli.py demo.py              CLI + integrated demo
Dockerfile  .github/        container + CI
```

---

## 13. Configuration

All via environment (a local `.env` is read; process env wins).

**LAVARD** (`LAVARD_` prefix): `PROFILE` (dev|prod), `API_KEY`, `AUDIT_KEY`, `CORS_ORIGINS`,
`RATE_LIMIT_PER_MINUTE`, `DATABASE_URL`, `MEMORY_URL`, `REDIS_URL`, `USE_THEHOUSE`,
`AUTO_SPEND_CEILING_USD`, `JOB_BUDGET_USD`, `ROOM_TURN_LIMIT`, `AGENT_TURN_LIMIT`,
`MODEL_ENDPOINT`, `MODEL_API_KEY`, `MODEL_{TRIVIAL,ROUTINE,COMPLEX,CRITICAL}`. Live onchain requires
`OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` + `LAVARD_OKX_LIVE=1`.

**TheHouse** (`THEHOUSE_` prefix): `PROFILE`, `DATABASE_URL`, `REDIS_URL`, `discount_rate` (0.80),
`solo_discount_rate` (0.001), `min_batch_for_discount` (2), `settlement_rail`
(`a2a_pay` | `x402_facilitator`), `settlement_mode`, `settlement_token_symbol`, `settlement_chain`,
`facilitator_*`, `okx_api_key` / `okx_secret_key` / `okx_passphrase`, `internal_api_token`,
`rate_limit_per_minute`, `max_queue_depth`, `request_ttl_s`.

In prod: `PROFILE=prod`, a real `AUDIT_KEY`, an `API_KEY`, secrets from a manager (not `.env`),
and `alembic upgrade head` for TheHouse (see `docs/persistence.md`).

---

## 14. Running it

```bash
# deps (Python 3.11+; 3.14 used in dev)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install aiosqlite redis fakeredis greenlet alembic   # TheHouse dev deps

# run a goal end-to-end (offline, deterministic)
python cli.py run "research competitors then design a logo and build a landing page"
python cli.py run "get the current BTC price" --preference cheapest
python cli.py report <job_id>

# the integrated money + memory demo
python demo.py

# the API
uvicorn api.main:app --reload          # POST /run, /jobs, /os, /jobs/{id}/report ...

# tests
python -m pytest -q                    # 278 passing, fully offline

# container
docker build -t lavard . && docker run -p 8000:8000 lavard
```

Key endpoints: `POST /run`, `POST /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/hire`,
`POST /jobs/{id}/room`, `POST /jobs/{id}/signoff`, `GET /jobs/{id}/report`,
`GET /jobs/{id}/suggestions`, `GET /os`, `GET /memory`, `GET /healthz`.

---

## 15. The frontend

A self-contained landing/console (`scratchpad/lavard.html`, published as an Artifact). Its identity
is a **tightly-spaced horizontal ridged surface** (CSS repeating gradient with white highlight +
grey shadow + an SVG turbulence grain) used as the hero band *and* the literal stacked OS-layer
diagram. Warm-grey paper `#e9e7e1`, restrained deep-teal accent `#2f665e`, monospace for data,
light + dark themes with a toggle, a reduced-motion-safe hero run animation, and the real X Layer tx
linked to OKLink as a trust proof.

---

## 16. Audit history

Five deep audits, each on a different axis — each caught a real bug the test suite didn't:

| # | Axis | Headline finding (fixed) |
|---|------|--------------------------|
| 1 | Money accounting | invoiced-vs-collected conflation; conductor spend hid escrow |
| 2 | Concurrency / resources | **audit hash-chain corrupted under concurrent writes** |
| 3 | Idempotency / input | **retried hire → double escrow**; goal-length DoS; negative-price clamp |
| 4 | Security | redaction gaps; **cross-caller prompt injection** |
| 5 | Runtime / load | **reconciliation drift under real concurrency** (phantom settlement) |

The three scariest — inbound/outbound conflation, audit-chain corruption, and non-idempotent hire —
are the exact classes that sink payment systems in production. All fixed with regression coverage.

---

## 17. Production readiness

**Done:** offline-deterministic core; 278 tests; five audits; edge auth + rate limiting + CORS;
prod fail-fast; Docker + CI; Alembic (TheHouse) + `user_version` guard (LAVARD store);
observability; privacy-by-design; a **real on-chain settlement proven on mainnet**; TheHouse
settlement rewired onto the proven a2a-pay rail.

**Remaining before charging real users at scale:**

- Live PAID end-to-end through the full LAVARD job path (the primitives are proven; wire and run one
  tiny job with real settlement).
- Real inference provider wired and load-tested behind the Router.
- LAVARD Postgres backend for multi-replica HA (documented deferral — sqlite-WAL is fine
  single-node; see `docs/persistence.md`).
- Per-owner API authorization if multi-tenant job creation is exposed (currently single-tenant;
  documented in `docs/privacy.md`).
- Populate TheHouse's ASP registry from OKX discovery for real MCP savings.
- The deeper intake/dedup ordering fix behind the load-drift symptom (accounting invariant already
  holds).

---

## 18. The real-money test runbook

Do it on **X Layer testnet (chain 1952)** first, then a **tiny mainnet** amount. Run from a terminal
that can reach OKX (a funded, VPN'd environment if your network geo-blocks web3.okx.com). Keep
amounts tiny.

```bash
# prerequisites (once)
onchainos wallet status            # loggedIn: true (ak mode)
onchainos wallet balance           # fund the ak-session wallet shown here

# A2A escrow round-trip (proves LAVARD's escrow path)
onchainos agent search --query "text" --page-size 3
onchainos agent create-task --description "ping" --budget 0.10 --max-budget 0.10 \
  --currency USDT --payment-mode escrow --visibility 1 --provider <agentId>
onchainos agent set-payment-mode <jobId> --payment-mode escrow --token-symbol USDT --token-amount 0.10
onchainos agent confirm-accept <jobId>     # moves money into escrow
onchainos agent complete <jobId>           # releases on sign-off

# a2a-pay settlement (the proven rail) — note: `pay --amount` is RAW base units
onchainos payment a2a-pay create --type charge --amount 0.01 --chain xlayer --symbol USDT --recipient <addr>
onchainos payment a2a-pay pay --payment-id <id> --amount 10000 --currency <token> --recipient-address <addr>
onchainos payment a2a-pay status --payment-id <id>
```

Then set `LAVARD_OKX_LIVE=1`, `LAVARD_PROFILE=prod`, a real `LAVARD_API_KEY` and `LAVARD_AUDIT_KEY`,
and run one small `POST /run` with a tight budget, watching the audit log and settlement rows.

---

*Built for OKX AI · X Layer. A discount is only ever real; money leaves escrow only on your sign-off;
your work never trains on anyone else's data.*
