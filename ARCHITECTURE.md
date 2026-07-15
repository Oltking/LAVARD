# LAVARD — Architecture

Target end-state per `LAVARD_BUILD_SPEC.md` §3. This file evolves each phase.

## Pipeline
```
USER goal
  → INTAKE    verify-first: restate goal, list assumptions, define success criteria
  → FOREMAN   decompose → task graph (DAG) → per node: marketplace query → VETTER →
              necessity test → dedup vs Portable Memory → hire via A2A escrow → in-room ID
  → ROOM      controller-mediated crew + shared blackboard;
              first-responder loop: LAVARD answers (memory) → poll room → find/hire;
              referee: turn/budget caps, loop-breaking, kill-switch
  → ROUTER    (under everything) semantic cache, cross-agent dedup, cheapest-accurate routing
  → SETTLE    escrow release on sign-off → reputation update
  → DISTILL   on close: redact → store facts (confidence+freshness) + reusable Playbook
  GOVERNANCE  cross-cutting: Action Review, permission tiers, immutable audit log, budget caps
```

## Key resolved decisions (from Phase 0 — see QUESTIONS.md)
- **Room transport = controller-mediated by default** (Q-ROOM-1). `RoomTransport` interface with
  `ControllerMediated` (default) + optional `XmtpDirectTag` backends. No agent traffic bypasses
  the controller's turn/budget meter.
- **LAVARD lists as an A2A orchestration service** (Q-LIST-1); consumes A2A (hire) + Agent-to-MCP
  (buy utilities). Payment SDK only needed if an Agent-to-MCP endpoint is exposed (Phase 9).
- **Onchain/payment/marketplace behind adapter interfaces** (`OnchainDataClient`,
  `PaymentsClient`, `MarketplaceClient`), mock/testnet-backed until live API signatures are
  verified from a browser (Q-API-1). No integration coded from memory.
- **No inference-vendor lock-in** (Q-MODEL-1): OpenAI-compatible client behind the Router; model
  tiers map to configurable model names via env.

## Phase 1 implementation notes
- **Stdlib-first core.** `core/` (schemas=dataclasses, config, store=sqlite3, intake, foreman,
  llm client with lazy httpx) imports **zero third-party packages**, so the demo + tests run with
  nothing installed. FastAPI (`api/`), the rich CLI (`cli/`), and the SQLAlchemy/Postgres backend
  (`core/models.py`, `core/db.py`) are the production layer on top, activated when deps are
  installed / `LAVARD_DATABASE_URL` points at Postgres. This was hardened after the build
  environment showed unreliable PyPI access — fewer hard deps in the critical path is also simply
  better. Table shapes in `core/store.py` (sqlite) and `core/models.py` (ORM) are kept in lockstep.
- **Planner:** `core/intake` + `core/foreman/decompose` use the model when configured, else a
  deterministic heuristic. Output is always a validated DAG (`validate_dag`).
- **Queue seam:** `core/queue.py` runs inline by default; Arq/Redis when `LAVARD_REDIS_URL` set.

## Phase 2 implementation notes
- **`onchain/` adapters behind interfaces.** `MarketplaceClient` (candidate ASP discovery) and
  `OnchainDataClient` (identity, reputation, contracts, funder tracing) each have a deterministic
  `Mock*` backend (default) and a real `OnchainOs*` stub that raises with a doc pointer until the
  live API is verified (Q-API-1). `onchain/factory.py` selects real only when `OKX_*` creds are
  set AND `LAVARD_OKX_LIVE=1` — so unwired = loud, never silently faked.
- **Foreman marketplace step** (`core/foreman/market.py`): `find_candidates(capability)` ranks on
  reputation ↑, dispute-rate ↓, price ↓ (Vetter trust score folds in at Phase 3).
- Mock onchain profiles deliberately include ~25% opaque/mixer-funded origins so Phase 3's
  honest-limits path is exercisable.

## Phase 3 implementation notes
- **`core/vetter`** turns an `OnchainProfile` into a `VetterVerdict` via a transparent additive
  model: base = platform reputation, then track-record / dispute / freshness / risky-contract /
  funder-opacity adjustments, each recorded as an `Evidence(signal, detail, effect)`. Hard
  overrides force `low` on risky contracts, score<45, or dispute-rate>20%.
- **Confidence ≠ trust.** An opaque/mixer funder origin lowers *confidence* and blocks a `high`
  rating, and is surfaced as an honest limit — never hidden. Every verdict carries the
  "this is a signal not a guarantee; TEE keys + fresh sub-wallets cap tracing" limit.

## Phase 4 implementation notes
- **`core/foreman/hire.py`**: per node → `necessity_test` → `find_candidates` → `vet_agent` each
  → pick best by `rank_score + trust_bonus` (low-trust bonus = -1000, i.e. never auto-hired) →
  `get_payments().open_escrow(...)` → assign `in_room_id` (`<node_key>::<AgentName>`) → persist to
  the `hires` table. All-low-trust ⇒ `escalated_low_trust` (spending is always-ask).
- **`sign_off(job_id)`** releases every open escrow (SETTLE) via the payments adapter and marks
  hires `released`. Escrow adapter (`onchain/payments.py`) enforces the OPEN→RELEASED/REFUNDED/
  DISPUTED state machine; the real Agent Payments Protocol backend is stubbed (Q-API-1/Q-LIST-2).

## Phase 5 implementation notes
- **`core/room`**: `run_room` iterates hired nodes; each agent turn passes through the `Referee`
  (per-agent + per-room turn caps, running budget meter with hard ceiling + `degraded` flag,
  duplicate-question/loop detection, store-backed kill-switch checked every turn boundary).
- **`FirstResponder.resolve`** is the three-branch loop: Portable Memory / blackboard →
  poll participants → hire a new specialist (marketplace + Vetter + escrow, charged to the meter).
- **Kill-switch** is `job_control.frozen` in the store, so `kill <job_id>` (or the API) trips it
  from another process and the room halts at the next turn boundary. `RefereeStop` unwinds the
  whole room immediately with a status (`frozen`/`budget_exceeded`/`turn_limit`).
- **Transport seam** (`core/room/transport.py`) encodes Q-ROOM-1: `ControllerMediated` (default,
  active) vs `XmtpDirectTag` (stub, disabled) — no agent traffic bypasses the controller's meter.
- `PortableMemory` here is a seedable stub; Phase 7 swaps in the Qdrant-backed store with no change
  to the controller above it.

## Phase 6 implementation notes
- **`core/router`**: `classify_step` → tier (trivial/routine/complex/critical) → `model_for(tier)`
  (configurable per-tier model names). `Router.ask` checks the `SemanticCache` first; a fresh
  near-duplicate is a `cache_hit`, a different agent's near-duplicate is a `dedup_collapse` — both
  logged as `saved`. Embeddings via `get_embedder()` (deterministic `LocalHashEmbedder` offline,
  OpenAI-compatible when configured); vector index in-memory now, Qdrant-swappable behind
  `VectorIndex`. Freshness (`max_age_s`) weights out stale answers.
- **`/evals/`**: `classification.json` + `python -m evals.run` gate the classifier at a 90% floor
  (currently 100%); `test_router` runs the eval in CI.

## Phase 7 implementation notes
- **`core/memory`** is a separate, persistent, owner-scoped store (`LAVARD_MEMORY_URL`, sqlite by
  default; Qdrant-swappable behind `MemoryStore`). `redact()` strips secrets/PII **at capture** so
  the store is never a leak vector. Facts carry `confidence` + `freshness_ts`; reads enforce a
  min-confidence and a max-age (stale facts, e.g. prices/onchain data, are weighted out).
- **DISTILL** (`distill_job`) writes, per finished specialist node, a redacted Fact (embedded on
  `title + capability`) plus one reusable Playbook (goal shape, roles hired, pitfalls, node
  skeleton). **REUSE** (`reuse.py`): `submit_goal` surfaces a matched Playbook; `hire_for_job`
  calls `memory_answer_for_node` before spending — a fresh, confident match ⇒ `skipped_memory`
  (no hire). Owner-scoping is a WHERE filter on every read.
- `core/room/knowledge.PortableMemory` (the Phase-5 stub) and this store share the same lookup
  contract; wiring the room's first-responder to this store is a Phase-8+ follow-up.

## Phase 8 implementation notes
- **`core/governance/review.py`** — every proposed action (LAVARD's own or a hired agent's) is an
  `Action(type, description, amount_usd, target, required)` routed through `review_action` before
  execution. Verdicts: `APPROVE` / `APPROVE_WITH_EDITS` (ask-once, auto-cleared) / `ESCALATE` /
  `DENY`, each carrying `will_execute`. Unnecessary actions (`required=False`) are denied;
  destructive-intent phrasing on any action forces `ESCALATE`.
- **Permission tiers** (`permissions.py`): `spend` / `grant_scope` / `destructive` default to
  **always-ask**; `hire` is **ask-once**; reads are **always-allow**. A spend at or under
  `auto_spend_ceiling_usd` is de-escalated to ask-once, above it escalates to the user. The
  Foreman calls `review_action` before opening any escrow — a blocked verdict yields
  `escalated_spend` instead of a hire.
- **Hash-chained audit log** (`store.append_audit` / `verify_audit`): each entry seals
  `SHA256(prev_hash + seq + kind + actor + summary + detail)`; `verify_audit` recomputes the whole
  chain, so any tampered row breaks verification. `audit()` is the thin write helper used across
  subsystems (`job_created`, `hire`, `action_review`, `payment_released`, `hire_skipped_memory`, …).
- **Per-job report** (`report.build_report`): rolls up hires, hired cost, memory-reuse count and
  estimated savings, and the verified audit log — surfaced via `report` (CLI) and
  `GET /jobs/{id}/report`.

## Phase 9 implementation notes
- **`mcp/server.py`** — LAVARD's exposed MCP surface: a pure-stdlib `Tool` registry (name +
  description + JSON-Schema input + handler) with a minimal JSON-RPC `dispatch` for `tools/list`
  and `tools/call`, mirroring the MCP wire shape. Handlers are thin adapters straight into
  `core/` (`submit_goal`, `get_job_status`, `hire_crew`, `run_room`, `get_job_report`,
  `approve_action`, `kill_switch`) — no business logic. Errors surface as JSON-RPC error objects,
  never crashes. The official `mcp` Python SDK can wrap this registry once verified (Q-API-1).
- **`mcp/skill.md`** — LAVARD's published OKX.AI skill file, using the verbatim
  `okx/agent-skills` frontmatter shape (name, routing `description`, `metadata.agent.install`) so
  routing behaves identically to any other skill. Declares the MCP server as its install/transport.
- **`mcp/listing.py`** — go-live packaging: `build_listing()` (A2A listing manifest — identity,
  mode, MCP tools, pricing ceiling, ≥100 OKB dispute stake), `readiness_review()` (7 gates that
  must all be green before publish), and `publish()` (guarded: BLOCKED if review fails, a **dry-run
  receipt** offline, and a loud `NotImplementedError` on the live path pending API verification).
  `is_live()` reports the true backend so the demo never fakes a live marketplace.
- Surfaced via `golive` (CLI) and `GET /golive` + `GET /mcp/tools` (API).

## Phase 10 implementation notes
- **Checkpointing** — a `room_checkpoint` row (job_id PK: `completed_nodes`, `spend_usd`,
  `room_turns`) is written after **every** completed node and on any `RefereeStop` (freeze / budget
  / turn-limit). A clean finish clears it. `save_checkpoint` / `get_checkpoint` / `clear_checkpoint`
  on the store.
- **Resume-after-crash** — `run_room(resume=True)` reloads the checkpoint: completed nodes are
  skipped (logged with method `resumed`), and the **budget meter carries forward** (`Referee
  resume_spend`) so the money cap is the cross-restart invariant. Turn limits are a per-run
  loop-guard and reset each run. `transcript.resumed_from` records what was reloaded.
- **Budget cap + graceful degradation** — `Referee.check_budget()` is a **pre-flight guard**: a
  resumed over-budget job halts *before* committing another hire (the earlier charge-then-raise
  path could overshoot by a full hire on resume). Near the ceiling (`degraded`, ≥80%) the
  controller leans on memory/cheaper routes.
- **Soak** — `tests/test_autonomy.py` interrupts a 4-node job every few turns across 50 restarts;
  it completes within budget and leaves no stale checkpoint.

## Post-audit hardening notes
A full self-audit (see the audit log in the build history) surfaced gaps between demonstrated and
wired-in behavior; all were fixed:
- **Router now mediates the room's spend (HIGH-1).** `run_room` builds one shared `Router`;
  `_run_node` charges the meter via `Router.ask_costed`, so a near-duplicate deliverable is served
  from the semantic cache instead of paid for again. `transcript.router_saved_usd` reports it.
- **Room first-responder reads real memory (HIGH-2).** `MemoryBackedKnowledge` implements the
  `lookup` contract against the persistent owner-scoped `MemoryStore` (demo seed is an overlay).
- **Audit log is truncation-evident (HIGH-3).** `audit_head` seals `(length, terminal-hash)` with
  an HMAC keyed by `LAVARD_AUDIT_KEY` (held outside the DB). `verify_audit` checks the chain *and*
  the sealed head, so deleting the most-recent entry — previously undetectable — now fails
  verification, and the head can't be forged without the key.
- **Budget can't be overshot by a hire (MED-1).** `Referee.affordable()` is checked before opening
  a mid-room escrow; an unaffordable hire is declined (graceful degradation), not committed.
- **Critical steps bypass the cache (MED-2).** Spending/vetting/sign-off always compute fresh.
- **Settlement routes to the funded payee (MED-3).** The escrow payee address is persisted on the
  hire and reused at sign-off (was previously reconstructed from `agent_id`).
- **Cross-restart durability (MED-4).** sqlite runs in WAL with a busy timeout; a test proves
  resume works from a cold store/settings view, not just an in-process re-call.
- **Redaction precision (LOW-1)**, **bounded router cache (LOW-2)**, and **honest report caveats
  (LOW-4)** — `estimated_savings_usd` is labeled a heuristic; realized `router_saved_usd` and the
  `audit_seal` state are surfaced alongside it.

## TheHouse blend (bundled aggregation broker)
- **`thehouse/`** — the full TheHouse product vendored in as a namespaced sub-package (its `core`/
  `onchain`/`gateway`/`directory` packages became `thehouse.core`/… to avoid colliding with
  LAVARD's own `core`/`onchain`). Imports rewritten wholesale; its 85-test suite runs under the same
  `pytest` (asyncio_mode=auto) alongside LAVARD's — `testpaths = ["tests", "thehouse/tests"]`.
- **`core/execution/`** — LAVARD's execution seam. `McpExecutor` is the interface the Foreman/Router
  use to actually make + pay for an Agent-to-MCP call. `TheHouseExecutor` submits the call to an
  in-process `thehouse.core.service.AggregatorService` (batch discount, ~20% off); `DirectExecutor`
  is the full-price OKX-x402 fallback. `build_thehouse_executor()` wires the aggregator from the same
  pieces TheHouse's own app uses. The `thehouse` import is **lazy** (inside functions) so LAVARD's
  stdlib core never pulls the async stack at import time. Gated by `LAVARD_USE_THEHOUSE`.
- Proven by `thehouse/tests/test_lavard_blend.py`: three concurrent LAVARD jobs → one aggregated
  target call → three answers, each charged 0.8 vs 1.0 list (20% saved).
- **Foreman wiring** (`core/foreman/hire.py`): `hire_for_job(job_id, executor=…)` branches per
  candidate — an **Agent-to-MCP** (`mode == "mcp"`) best-candidate is *executed* via the injected
  executor (pay-per-call through TheHouse) and recorded as `serviced_mcp` with the realized
  `saved_usd`; **A2A** candidates keep the escrow hire. No executor ⇒ MCP falls back to escrow
  (backward-compatible). A sync↔async bridge (`_run_async`) lets the stdlib Foreman drive the async
  executor. Per-job `build_report` surfaces `mcp_calls` + `thehouse_saved_usd`.

## Module map (`core/`)
| Module | Role | Phase |
|---|---|---|
| `core/intake` | verify-first goal intake | 1 |
| `core/foreman` | decompose, marketplace query, vetting-gate, hire, task graph | 1,2,4 |
| `core/vetter` | onchain forensics + confidence-scored risk verdict | 3 |
| `core/room` | controller-mediated bus, blackboard, first-responder loop, referee, kill-switch | 5 |
| `core/router` | semantic cache, cross-agent dedup, cheapest-accurate routing, cost log | 6 |
| `core/memory` | portable memory, playbooks, distill/reuse, redaction | 7 |
| `core/governance` | action review, permission tiers, audit log, reporting | 8 |
| `onchain/` | OnchainOS + Agentic Wallet + escrow/settlement adapters | 2,4 |
| `mcp/` | LAVARD's MCP surface (exposed + consumed) | 9 |
| `evals/` | per-step accuracy sets + runner | 6 |
| `infra/` | docker-compose (Postgres, Qdrant, Redis), CI, deploy | 1 |

## Data stores
- **Postgres** — identity, jobs, task graphs, hires, audit log, playbook index.
- **Qdrant** — `router_cache`, `lavard_facts`, `lavard_playbooks` (see docs/vendor/memory/qdrant.md).
- **Redis** — room message bus, locks, budget meters, kill-switch flag.

## Cross-cutting invariants
- Every proposed action (LAVARD's or a hired agent's) passes **Action Review** before execution.
- Spending / scope-grant / destructive actions default to **always-ask**.
- The **audit log is append-only**. The **kill-switch** freezes a room instantly (Redis flag
  checked at every turn boundary).
- Memory is **redacted at capture** and **owner-scoped** at query time (payload filter).
