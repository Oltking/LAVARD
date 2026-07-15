# QUESTIONS.md — builder → owner open questions

Format: each entry has status **RESOLVED** (with the decision we build on) or **OPEN** (blocking
or non-blocking). Phase 0 exit requires Q-ROOM-1 and Q-LIST-1 resolved. Update as live docs are
verified.

---

## RESOLVED (Phase 0)

### Q-ROOM-1 — Room transport (BLOCKING for §4.3; resolved) ✅
**Question:** Can hired third-party ASPs participate in live, multi-turn, cross-tagging
conversation, or is interaction request/response only?
**Finding (docs/vendor/messaging/xmtp.md, okxai/payments-and-modes.md):** OKX AI's native A2A
primitive is a **negotiation + escrow business transaction** (quote → scope → deliver →
sign-off), **not** a documented live multi-agent cross-tag room. **XMTP** offers real
wallet-identity group chat among agents, but arbitrary ASPs are **not guaranteed** to be
XMTP-reachable, and uncapped agent chat is a runaway-cost risk (spec §0.3).
**DECISION:** Room is **controller-mediated by default** — every message routes through LAVARD's
referee (turn/budget/loop caps + kill-switch). Implement behind a `RoomTransport` interface with
two backends: `ControllerMediated` (default) and `XmtpDirectTag` (optional, enabled only when
both agents are XMTP-reachable AND the controller stays in the loop metering each turn).
**Re-verify** at Phase 5 whether OKX ships a native live-room primitive; if so add it as a third
backend behind the same interface.

### Q-LIST-1 — LAVARD's listing mode (resolved) ✅
**Question:** Which mode does LAVARD list under — Agent-to-MCP or Agent-to-Agent?
**Finding:** LAVARD negotiates scope with the user, runs the room, and is accountable to
sign-off — that is exactly the **A2A** shape (escrow, negotiation, dispute path).
**DECISION:** LAVARD **lists as an Agent-to-Agent orchestration service**. It **consumes** both
modes internally: A2A escrow to hire specialist ASPs, and Agent-to-MCP pay-per-call to buy
standardized utilities. The **Payment SDK** becomes a hard dependency only if/when LAVARD also
exposes an **Agent-to-MCP** endpoint — deferred to **Phase 9 (go-live)**, and only then.
**Sub-question (OPEN, non-blocking) → Q-LIST-2.**

### Q-ASP-1 — "ASP" naming ambiguity (resolved, low stakes) ✅
One press piece called ASP "Arbitration Service Provider." Spec + okx.ai/tutorial use ASP =
**Agent** Service Provider; the dispute arbitrator is the **Evaluator** (stake ≥100 OKB, ≥5 per
case, majority rules). **We follow spec + tutorial** throughout.

---

## OPEN

### Q-LIST-2 — Is the Payment SDK required for an A2A-only listing? (non-blocking) ⏳
Docs confirm Payment SDK is required for **Agent-to-MCP**. Unconfirmed whether a pure **A2A**
listing must also integrate it (escrow may be handled by the Agent Payments Protocol flow).
**Impact:** only Phase 9. **Plan:** build the onchain adapter mode-agnostic; confirm from live
dev docs before go-live. Not blocking Phases 1–8.

### Q-API-1 — Marketplace / payments / identity integration surface ✅ (2026-07-12, mostly resolved)
**RESOLVED (surface + subcommands).** The integration is not hand-rolled REST — it is the
**`onchainos` CLI** (`npx skills add okx/onchainos-skills`, also `onchainos mcp`). Verified from
github.com/okx/onchainos-skills via `gh` + the operator-captured okx.ai/tutorial/asp page; captured
in **docs/vendor/okxai/onchainos-cli.md**. Verified subcommands now backing the live adapters:
- Marketplace/identity (`okx-ai`, ERC-8004): `onchainos agent search` / `service-list` /
  `reputation`; task `publish`/`accept`/`deliver`/`dispute`. → `OnchainOsMarketplace`, `OnchainOsData`.
- Payments (`okx-agent-payments-protocol`): `onchainos payment a2a-pay create|pay|status` (A2A
  escrow). → `AppPayments`.
Corrected guesses: dispute = **5% bounty deposit within 1 day** (not ≥100 OKB / ≥5 evaluators);
identity = **ERC-8004**; A2A needs no x402 (that's A2MCP-only), resolving **Q-LIST-2**.

**REMAINING (deploy-time, ⏳):** exact flag spellings + JSON output keys per subcommand — the live
adapters are built to the verified subcommand names and map fields through `_require`, which fails
loudly with the actual keys if the shape differs. Confirm with `onchainos <cmd> --help` against an
installed CLI + sandbox credentials, then adjust the field maps. No fabricated data can slip through.

### Q-MODEL-1 — LAVARD's own inference provider (non-blocking) ⏳
Spec §0.5: no inference-vendor lock-in. **Plan:** OpenAI-compatible client behind the Router;
model tiers (`trivial|routine|complex|critical`) map to configurable model names via env, no
hard-wired provider. Owner to supply endpoint/key at deploy. Default to latest Claude models
where an Anthropic-compatible endpoint is configured; fully swappable.

### Q-BUDGET-1 — Default budget caps & permission policy (non-blocking) ⏳
Need owner defaults for: per-job budget ceiling, per-room turn limits, and which actions are
`always-allow` vs `always-ask`. **Interim defaults (change in config):** per-job cap = $25;
per-room turns = 40; per-agent turns = 8; spend / scope-grant / destructive = **always-ask**.
Confirm with owner before Phase 8.
