# TheHouse

A request aggregation broker for OKX.AI Agent Service Providers. Multiple agents call
TheHouse to reach a target ASP; TheHouse composes their requests into one natural compound
call, sends it once, splits the single response back to each caller, and keeps the spread.
Callers pay ~20% less; the target sees one normal call from one client.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                            # run the test suite (72 tests, no external services)
python -m scripts.acceptance_run  # full spec acceptance: 12 criteria, end to end
python -m scripts.demo_phase6     # 3 callers → 1 compound MCP call → 3 answers + ledger
python -m scripts.weekly_report   # economics report + nightly auto-protection pass
uvicorn core.api:app --reload     # run the service (dev profile: sqlite + fakeredis)
```

One process serves everything: the landing page (`/`), the directory storefront
(`/directory`), the seal (`/seal.svg`), the REST API (`/v1/*`), and the paid MCP gateway
(`POST /mcp`). In prod the gateway mounts only once a facilitator-backed payment verifier
is wired (see `docs/GO_LIVE.md` step 2), and the unpaid REST intake requires
`X-Internal-Token` (`THEHOUSE_INTERNAL_API_TOKEN`).

Production profile (`THEHOUSE_PROFILE=prod`) uses Postgres + Redis + Qdrant:

```bash
docker compose -f infra/docker-compose.yml up -d
pip install -e ".[dev,postgres,semantic]"
export THEHOUSE_PROFILE=prod
export THEHOUSE_DATABASE_URL=postgresql+asyncpg://thehouse:thehouse@localhost:5432/thehouse
```

## Surfaces

**MCP gateway** (what callers on OKX.AI use — `gateway/mcp_server.py`): standard MCP JSON-RPC.
`tools/list` mirrors every aggregated target; `tools/call` without payment returns HTTP 402
with an x402 challenge at the TheHouse price; a signed replay enters the aggregation pipeline
and returns the caller's answer. The payer wallet is the caller identity.

**REST API** (`core/api.py`):
- `POST /v1/call` — intake: `{asp_id, tool_name, arguments, caller_id, priority}` → `202 {request_id}`
  (prod: operator-only, `X-Internal-Token`)
- `GET /v1/result/{request_id}` — status, answer, charge, batch attribution
- `GET /v1/directory` (JSON) / `GET /directory` (storefront HTML)
- `GET /v1/queue/{asp_id}` (prod: operator-only), `GET /health`
- `GET /metrics` (Prometheus text) · `GET /desk` (operator dashboard; prod: operator-only)

**Web** (`directory/landing.py`, `directory/service.py`): the monochrome landing page at
`/`, the service directory at `/directory`, the seal at `/seal.svg` (standalone copies in
`assets/`).

**Price sync** (`onchain/sync.py`): target fee changes re-derive TheHouse prices and push
the updated fee to the OKX listing automatically; every change is audit-logged.

Going live on OKX.AI (credentials, wallet, on-chain listing): see `docs/GO_LIVE.md`.

## Production hardening

- **Replay protection**: an inbound payment authorization is spendable exactly once
  (byte-identical header = replay → 402). Refusals before service (rate limit, queue
  full) release the authorization so an honest retry can spend it.
- **Backpressure**: per-caller rate limit (`THEHOUSE_RATE_LIMIT_PER_MINUTE`, default 240)
  and per-target queue cap (`THEHOUSE_MAX_QUEUE_DEPTH`, default 500) — always refused
  *before* any charge.
- **Crash recovery**: on startup, requests logged as queued but missing from Redis are
  re-queued (paid work is never silently lost). Requests undelivered past
  `THEHOUSE_REQUEST_TTL_S` (default 600) fail loudly with an audited expiry.
- **Partial splits**: a caller whose segment parsed cleanly gets exactly their segment;
  only a caller whose segment could not be isolated receives the full compound response
  (at the price paid — no refund rail), and such responses are never cached.
- `Dockerfile` builds the app image; `.github/workflows/tests.yml` runs the suite +
  acceptance on every push.

## Non-goals & honest constraints

- TheHouse does not modify any target ASP's API or require their cooperation. If a target's
  response format breaks the Splitter, that ASP is re-profiled and degrades to direct routing.
- Mode A split quality depends on the target following the numeric instruction — any LLM-backed
  agent will; unpredictable ones are flagged `manual_review` and excluded from aggregation.
- Non-aggregatable ASPs (writes, transactions, side effects) get no discount — every advertised
  discount is backed by real cost savings, never a loss leader.
- Compound calls carry at most **2 questions** (owner decision) to protect response quality;
  overflow rolls into further 2-slot batches to the same target. Accuracy is never traded
  for margin.
- Requests merge only on **exact string equality** — nothing fuzzy in the money path.
  (An embedding-based semantic merger exists as an opt-in module, off by default.)
- **No refunds**: OKX.AI has no refund rail, so every charge is collected up-front at the
  402 gate and is final. Priority calls pay the original price − $0.01 and fire immediately.
- TheHouse is standalone: no dependency on any other project.

## Development

Tests run with zero external services (SQLite + fakeredis). See `ARCHITECTURE.md` for the
system design and build phase status.
