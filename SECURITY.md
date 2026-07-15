# LAVARD — Security & Safety

## Money & control (the whole job is control)
- **Hard budget caps** at job and room level; a running meter in Redis; graceful degradation
  (lean on memory + cheaper routes) near the cap; **hard stop** at the ceiling.
- **Turn limits** per-agent and per-room; loop / duplicate-question detection.
- **Global kill-switch** — a Redis flag checked at every turn boundary; freezes the whole room
  instantly. Nothing spends after the flag is set.
- **Escrow, not prepay** — hired ASPs are paid via Agent Payments Protocol escrow, released only
  on sign-off (docs/vendor/okxai/payments-and-modes.md).

## Permissions & review
- **Action Review** on every proposed action: verbatim → expected outcome → downside/risk →
  is-it-required → verdict `approve | approve-with-edits | deny | escalate-to-user`.
- **Permission tiers:** `always-allow` / `ask-once` / `always-ask`. Spend, scope-grant, and
  destructive actions default to **always-ask**.
- **Immutable audit log** — append-only record of every hire, message, verdict, and payment.

## Secrets & data
- **Redact at capture** — secrets/credentials/PII are stripped *before* anything enters Portable
  Memory, so the store is never a leak vector.
- **Owner-scoped memory** — enforced by Qdrant payload filter (`owner_id`) at query time. Only
  redacted, need-to-know slices are ever exposed into a room of third-party agents.
- **Credentials** (`OKX_API_KEY`/`SECRET`/`PASSPHRASE`, inference keys, DB URLs) come from env /
  secret manager only — never committed, never logged, never sent to hired agents.
- **Agentic Wallet keys live in a TEE** — LAVARD can request signing but cannot exfiltrate keys.

## Vetter honesty
Provenance is **confidence-scored, never guaranteed**. Funder tracing hits real walls (TEE keys,
fresh sub-wallets, mixers); a low-confidence/opaque result is surfaced to the user as a signal,
not hidden. Verdict is always `trust: high|medium|low` + confidence + evidence chain.

## Reporting = trust surface
Per-job + rolling reports: what was done, who was hired, cost, memory reused, dollars saved.
Doubles as the audit/trust surface and the demo narrative.
