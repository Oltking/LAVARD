# LAVARD data governance — privacy by design

One rule, three tiers. The guiding principle: **learn from behavior, never share content.**
A user's work is theirs; the system may get smarter from *patterns*, but no user's data is ever
handed to another user.

## Tier 1 — Private (per-user, never leaves the user)

Goals, restated intake, deliverables, durable **facts**, and reusable **workflow blueprints**.

- Owner-scoped everywhere: `match_playbook`, `blueprint_for_goal`, `preferred_crew_for_goal`,
  `search_facts`, `memory_answer_for_node` all filter by `owner_id`.
- Reused only for the SAME owner's future jobs. Owner A's blueprint is never returned to owner B —
  even anonymized, a blueprint is derived from a specific project.
- Captured through redaction (`core/memory/redact.py`) so secrets/PII never persist.

## Tier 2 — Aggregate learning (global, but zero user content)

The system learns collective *patterns* so predictions and planning improve for everyone.

- The unit of learning is a **statistic, not a record**: only capability-pair counts live in
  `insights_cooccurrence` (`core/insights.py`). No owner id, no goal text, no deliverables.
- No individual user's workflow can be reconstructed from it, and nothing is handed between users.
- `Playbook.anonymized()` exposes only the non-identifying capability shape (`roles`, `dag_edges`)
  — never titles, crew, owner, or goal.

## Tier 3 — Public knowledge (the Intelligence Exchange)

Sharing "current BTC price" or "today's ETH news" across callers is not sharing user data — the
query and answer contain nothing personal.

- `is_shareable` (`core/router/exchange.py`) shares ONLY public, read-only, non-personalized
  lookups. Anything mutating or personal (`my`, `audit`, `deploy`, `wallet`, `private`, …) bypasses
  the exchange and is computed in isolation.
- The shared answer never carries who first asked.

## A deliberate exception: provider reputation is global

The **Reputation Graph** aggregates execution outcomes across all jobs — but it rates *marketplace
agents* (public service providers), not users. An agent's track record improving everyone's
selection is provider data, not user data, and contains nothing about who commissioned the work.

## API access control (current boundary)

The HTTP API is **single-tenant**: one edge API key authenticates the operator (`LAVARD_API_KEY`).
Owner-scoping is enforced at the DATA layer (memory queries filter by `owner_id`), but the API does
not bind a request to an owner — `GET /memory?owner=X` can read any owner, and job endpoints act on
any job id. Today this is not cross-tenant exposure because the API only ever creates `default-owner`
data (`submit_goal`/`run_job` don't take an owner). **Before exposing per-owner job creation to
distinct end-users, add per-owner authorization** (bind the token → owner and scope every
job/memory/report/sign-off endpoint to it); otherwise `?owner=` becomes an IDOR.

## Invariants (enforced by tests)

- Owner A's facts/blueprints are never returned to owner B.
- The aggregate insights store contains only capabilities — never owner, goal, or content.
- A personalized query never populates the public shared cache.
- Prediction/suggestions never spend (Tier-1 stays always-ask).
