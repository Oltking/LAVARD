# LAVARD — OKX.AI Genesis Hackathon submission pack

**Deadline: 2026-07-17, 23:59 UTC.** Prize pool $100,000.
Four steps to enter: (1) build an ASP ✓, (2) list on OKX.AI + go live, (3) X post + ≤90s demo,
(4) Google Form. Steps 2–4 need your wallet/accounts, so you run them; everything is prepared below.

---

## Step 2 — List LAVARD as an ASP (the eligibility gate)

The listing goes through the **`onchainos` CLI** (`agent pre-check → upload → create → activate`).
OKX's internal review/QA runs at `create`/`activate`. Run these from a terminal that can reach OKX
(VPN if your network geo-blocks web3.okx.com), with your ak-session wallet logged in
(`onchainos wallet status` → `loggedIn: true`).

```bash
# 0. sanity
onchainos wallet status

# 1. consent + uniqueness (first time a wallet registers)
onchainos agent pre-check --role asp --chain xlayer
#   if it returns consent.consentKey + terms, read them, then:
# onchainos agent pre-check --role asp --chain xlayer --consent-key <consentKey>

# 2. avatar (ASPs require one) — upload an image, use the returned URL
onchainos agent upload --help          # confirm the exact image flag
onchainos agent upload <path-to-logo.png>

# 3. create the ASP identity + its service (A2A orchestration, flat $2 coordination fee)
onchainos agent create \
  --role asp --chain xlayer \
  --name "LAVARD" \
  --description "The autonomous AI operating system for OKX AI. State a goal; LAVARD plans the work, hires the best specialist agents from the marketplace, batches compatible requests into one paid call to cut cost by up to ~20%, runs them under an accountable controller, and settles on-chain. One request in, finished work out." \
  --picture "https://static.okx.com/cdn/web3/wallet/marketplace/headimages/agent/avatar/855cea7c-d165-4d1b-9f02-d314385a6916.png" \
  --service '[{"serviceName":"AI Work Orchestration","serviceDescription":"State a goal in plain language. LAVARD decomposes it, vets and hires the best specialist agents on OKX AI, and batches compatible requests to the same provider into one paid call — saving each caller up to ~20% — then runs them under a budget-enforcing controller with live-crew failover and settles via on-chain escrow, releasing payment only on your sign-off. A flat $2 coordination fee; specialist work settles through the per-job budget.","serviceType":"A2A","fee":"2"}]'

# 4. activate (submits for approval / QA)
onchainos agent activate --agent-id <agentId> --chain xlayer --preferred-language en-US

# 5. confirm it went live
onchainos agent get-my-agents --role asp
```

Notes:
- **A2A** service ⇒ `serviceType:"A2A"`, `fee` **optional**, **no endpoint**. That matches LAVARD's
  agent-to-agent orchestration mode and needs no public HTTP endpoint to register.
- **The `fee` is NOT a fixed sticker price.** For A2A, the real, dynamic price is negotiated
  **per task** via the escrow flow (client sets `--budget`/`--max-budget`; LAVARD accepts within it;
  `set-payment-mode --token-amount` → `confirm-accept` → `complete`). LAVARD's conditional/−20%
  batch pricing is *internal* — how it gets the sub-work done cheaply (its margin), separate from
  what the client pays.
  - **Option A (fully dynamic):** omit `fee` from the service JSON; price is 100% the task budget.
  - **Option B (recommended, clean revenue story):** set a small flat `fee` as LAVARD's
    **coordination cut** — revenue = coordination fee + batching margin (fits "Revenue Rocket").
    e.g. `"fee": 2` (a $2 flat orchestration fee; specialist work settles via the job budget).
- **To remain eligible ("go live"), LAVARD must be able to accept and fulfill A2A tasks.** Keep a
  LAVARD instance running against the onchainos a2a session so a matched task can be accepted →
  delivered → completed. (Optional upgrade: also register an **A2MCP** service exposing the
  conductor at a deployed, x402-gated endpoint — that needs a public URL.)
- LAVARD's own readiness check is **READY-TO-LIST** (`GET /golive` / `python -c "from mcp import
  build_listing, readiness_review; print(readiness_review(build_listing()))"`).

---

## Step 3 — X post (#OKXAI) + ≤90-second demo

**Post copy** (edit the handle/links):

> Meet **LAVARD** 🟦 — the autonomous AI operating system for OKX AI.
>
> You state a goal. It plans the work, hires the best specialist agents, **batches requests to cut
> ~20% cost**, runs them under an accountable controller, and **settles on-chain**. One request in,
> finished work out.
>
> Real X Layer settlement, five security audits, 278 tests. Demo 👇
> #OKXAI

**90-second demo script** (screen-record the frontend + CLI):

| Time | Show | Say |
|------|------|-----|
| 0–10s | Frontend hero (the ridged surface + run animation) | "You state the goal. LAVARD does the rest." |
| 10–35s | `python cli.py run "build me a DeFi lending protocol"` — plan → optimize → **batch (−20%)** → run → settle, with the cost breakdown | "It decomposes the goal, hires the best agents, and batches the audit calls into one — 20% off." |
| 35–55s | The on-chain tx on OKLink (`0xa65fd720…`) | "This isn't a diagram — real USDT settled on X Layer mainnet." |
| 55–78s | Scroll the frontend: the OS layers + the three network effects | "Memory, liquidity, and reputation compound — it gets smarter and cheaper as it runs." |
| 78–90s | The pricing panel / 'always-ask sign-off' | "A discount only when it's real; money moves only on your sign-off. LAVARD — the coordination layer for AI work on OKX." |

Assets to record: the **frontend artifact**, `python demo.py`, `python cli.py run …`, and the
OKLink tx page.

---

## Step 4 — Google Form (by July 17, 23:59 UTC)

Prepared answers:

- **Project name:** LAVARD
- **One-liner:** The autonomous AI operating system for OKX AI — state a goal, it plans, hires,
  batches to cut cost, and settles on-chain.
- **Use case:** A general contractor for AI work. Anyone (or any agent) states a goal; LAVARD turns
  it into finished, paid-for work by orchestrating specialist agents on OKX AI — while an invisible
  batching engine (TheHouse) cuts ~20% off by merging compatible requests. Crypto and non-crypto
  work alike.
- **What makes it different:** conditional pricing (a discount only when a real batch forms),
  proven on-chain settlement, hash-chained tamper-evident audit, privacy-by-design, and network
  effects (memory/liquidity/reputation) that compound as a moat.
- **ASP agent id / listing:** `5909` (X Layer, tx 0x97e622bb166ed39153d81a8656702b79ef1a35f8f3a33cf640891851c5f73a1a)
- **Suggested categories:** Best Product · Software Utility · Revenue Rocket
- **X post link:** `<your post>`
- **Demo video link:** `<≤90s video>`
- **Repo / frontend:** README.md · frontend artifact URL

---

## Proof points to lead with (all real)

- **On-chain settlement:** X Layer mainnet tx `0xa65fd7203bb759aa82eb6dc904b2869e079fc00f8abbce74ec60f1d8a7f5e701`.
- **Cost model:** batched −20%, solo full−0.1%, ceiling-authorized, settled the lower actual amount.
- **Quality:** 278 tests, **five deep security audits** (accounting, concurrency, idempotency,
  security, runtime/load) — each caught and fixed a real bug.
- **Integration:** built on the real `onchainos` CLI + x402/a2a-pay on X Layer, ERC-8004 identity.
