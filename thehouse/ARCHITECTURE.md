# TheHouse — Architecture

## Pipeline

```
CALLERS ──MCP/REST──▶ INTAKE ─▶ DEDUPLICATOR ─▶ BATCHING WINDOW ─▶ PROFILER CHECK
                                                                    ├─ Mode A → COMPOSER (numbered compound question)
                                                                    ├─ Mode B native → PACKER (array parameter)
                                                                    ├─ Mode B fan-out → PARALLEL DISPATCH
                                                                    └─ non-aggregatable → DIRECT ROUTE
                                                    ─▶ DISPATCHER (one paid call to target)
                                                    ─▶ SPLITTER (numeric anchors / keyed object)
                                                    ─▶ DELIVERY + ECONOMICS LEDGER + AUDIT
```

## Storage

| Store | Dev profile | Prod profile | Holds |
|---|---|---|---|
| SQL | SQLite (aiosqlite) | Postgres (asyncpg) | ASP registry, request log, economics ledger, audit log |
| KV | fakeredis (in-proc) | Redis 7 | per-ASP queues/windows, exact-match cache, locks |
| Vectors | qdrant-client local mode | Qdrant server | semantic dedup embeddings (Phase 8) |

The SQL layer is SQLAlchemy Core (async) with one schema for both profiles. All services take
`(engine, redis)` explicitly — no globals in business logic; module-level factories exist only
for app wiring.

## Packages

- `core/config.py` — env-driven settings (`THEHOUSE_*`), dev/prod profiles
- `core/models.py` — domain models: `CallerRequest`, `ASPEntry`, `BatchResult`, enums
  (`ASPMode`, `Transport`, `FireReason`, `SplitQuality`, `RequestStatus`)
- `core/storage/` — DB schema + engine factory, Redis factory
- `core/intake/` — validation, request_id stamping, registry lookup, enqueue
- `core/window/` — per-ASP queue (Phase 1: FIFO; Phase 4 adds fire logic)
- `core/{profiler,deduplicator,composer,packer,dispatcher,splitter,economics}/` — later phases
- `directory/` — ASP listing API + storefront (Phase 10)
- `gateway/` — TheHouse's own MCP server surface (spec calls this `mcp/`; renamed to avoid
  shadowing the MCP SDK package name)
- `onchain/` — Agentic Wallet / Payment SDK integration (Phase 11)
- `infra/` — docker-compose for prod-profile services

## Key protocol facts the design rests on (Phase 0)

- OKX.AI A2MCP services are public https endpoints gated by HTTP 402 (x402 / MPP —
  "OKX Agent Payments Protocol"); fees are fixed per service in USDT, registered on-chain
  (ERC-8004 identity on X Layer).
- Targets are either real MCP JSON-RPC servers (`tools/call`) or plain HTTP APIs — the
  registry records `transport` per ASP and the Dispatcher speaks both.
- Mode A ⇔ freeform `content[].text`; Mode B ⇔ `structuredContent` per `outputSchema`.
- TheHouse is an MCP server to callers and an MCP/HTTP client to targets; payment is a
  header-level concern (402 challenge → signed authorization → replay), invisible to the
  compose/split pipeline.

## Phase status

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Vendor docs harvested; pricing + MCP contract confirmed | ✅ |
| 1 | Core service: intake with request_id stamping, per-ASP queue, FastAPI | ✅ |
| 2 | ASP Registry + Profiler (mode + transport detection) | ✅ |
| 3 | Exact-match Deduplicator + TTL cache + in-window merge | ✅ |
| 4 | Batching Window fire logic (break-even / timer / priority) | ✅ |
| 5 | Composer (numbered compound question, sub-batch capping) | ✅ |
| 6 | Dispatcher + Splitter — Mode A end-to-end over real MCP JSON-RPC | ✅ |
| 7 | Mode B: native multi-parameter packer + parallel fan-out | ✅ |
| 8 | Semantic dedup (pluggable embedder; memory or Qdrant store) | ✅ |
| 9 | Economics Engine: ledger, auto-protection, weekly report | ✅ |
| 10 | Directory storefront (badges, slashed prices, live stats) | ✅ |
| 11 | Payments: x402 gateway (seller), payment hook (buyer), settlements | ✅ |
| 12 | Acceptance run (spec §10) + go-live checklist (`docs/GO_LIVE.md`) | ✅ code-complete; listing needs operator credentials |

## Payment flow (Phase 11)

```
caller ──tools/call──▶ gateway (402 challenge, thehouse price) ──signed replay──▶ pipeline
pipeline ──▶ dispatcher ──▶ target 402 ──▶ PaymentHook signs (Agentic Wallet) ──▶ replay ─▶ answer
settlements table: N inbound receipts − 1 outbound payment = margin in TheHouse wallet
```

Dev profile uses `DevSigner`/`DevPaymentVerifier` (deterministic, no chain); prod swaps in
`OnchainOSSigner` (`onchainos payment pay`, TEE) and facilitator verification — constructor
changes only.

## Pricing policy (owner decisions, 2026-07-07)

- Per-target pricing only: caller price = target's registered fee × 0.80. No global price.
- Compound calls cap at **2 questions** (`max_batch_size = 2`); `break_even_batch_size = 2`.
- Merging is **exact-string only** (trim-only fingerprint); semantic merge is opt-in, off.
- Priority = original − $0.01, fires immediately (floored at the discounted price).
- Payments collect up-front (402 gate); **no refunds** — partial splits deliver at the price
  paid and feed auto-protection instead.
- Fan-out routes: original × 1.05 coordination fee; direct routes: original price.
- Target fee changes propagate automatically: `onchain/sync.py` polls each target's
  registered fee (`agent service-list`), re-derives our prices, and pushes TheHouse's
  own listing fee (`agent update`). Dev profile uses static source + recording updater.
