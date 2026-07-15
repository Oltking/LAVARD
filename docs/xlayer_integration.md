# X Layer + OnchainOS Integration Reference

> Deep-research capture (2026-07-14). Sources are OKX OnchainOS dev-docs, the OKX App
> whitepaper, x402.org, and ChainList. This is the authoritative map of **what the platform's
> backend handles for us** vs **what LAVARD/TheHouse must actually submit**.

## 1. The network (X Layer)

| Fact | Value |
|------|-------|
| Chain ID (EIP-155) | **196** (`0xC4`) |
| CAIP-2 id | **`eip155:196`** |
| `chainIndex` (OKX Wallet API) | **196** (OKX's own network index; usually == chainId for X Layer) |
| Gas token | **OKB** (21M fixed supply) |
| Architecture | Ethereum L2, **OP Stack** (op-node + op-reth via Engine API), settles to L1 via AggLayer. *Note: X Layer was originally a Polygon-CDK zkEVM; current OnchainOS docs describe the OP-Stack build — treat it as optimistic-rollup semantics.* |
| Finality | Optimistic: **7-day fraud-proof challenge window** for L1 withdrawals |
| Block time | ~1s, up to 20,000 TPS, 99.9% uptime (Conductor HA cluster) |
| Explorer | https://www.oklink.com/xlayer |
| Stablecoins | USDT / USDC / USDG, **6 decimals**, EIP-3009 transfer |
| Gas sponsorship | **Zero-gas** USDT/USDG transfers on X Layer via OnchainOS gas sponsorship |

**We do NOT run a node.** RPC is only needed for raw on-chain reads/withdrawals; our money path
goes through the OnchainOS facilitator API, not through direct `eth_sendRawTransaction`.

## 2. What the platform's backend does for us (so we don't)

The **x402 Facilitator / Agent Payments Protocol Broker** occupies the settlement slot. It handles:

- **On-chain settlement** — batches many signed authorizations into **one** L1/L2 transaction.
- **Gas** — sponsored; we never hold OKB or pay gas for USDT transfers.
- **TEE aggregation** — session-key re-signing inside a trusted enclave before broadcast.
- **Escrow state machine** — the OKX **Optimistic Escrow** six states: `Created → Submitted →
  Completed`, with a `Disputing → arbitrated` branch. Auto-release to receiver after the dispute
  window if nobody disputes.
- **Dispute / arbitration** — the "Internet Court" (OKX + MetaMask + Matter Labs) resolves.
- **Replay protection, nonce, session-cert validation.**

We just **submit authorizations and poll status.** A `settle` "success" means *accepted*, **not**
on-chain. Landing is confirmed later via `/settle/status`.

## 3. What WE must submit (our responsibility)

1. **OK-ACCESS-signed HTTP requests** to the facilitator (same HMAC auth as the onchainos CLI).
2. **Payment payloads** — session-key certificate (`sessionCert`, base64) + per-call signature,
   matching the seller's declared `paymentRequirements`.
3. **Status reconciliation** — poll `/settle/status` and reconcile against our internal ledger
   (this is exactly what `EconomicsEngine.reconcile_settlements` was built for).
4. Choosing the right **scheme/intent** per job (see §5).
5. Smart-contract deploys / withdrawal initiation — only if we ever touch raw on-chain (we don't for MVP).

## 3b. LIVE-VERIFIED (2026-07-14, VPN on, ak-authenticated)

Confirmed by calling the real endpoint with our own `OnchainOSFacilitator` (OK-ACCESS signing
works — the client is validated, not guessed):

- `onchainos wallet status` → `loggedIn:true, loginType:ak`. Wallet EVM addr
  `0x3ab00da761de97eadcc1ff28642895eaf592e1e8` (SOL `v3H7…`). **Balance 0.00 / no tokens — must be
  funded before any paid test.**
- `GET /api/v6/pay/x402/supported` (signed) returns these schemes:

  | network | schemes |
  |---------|---------|
  | **eip155:196** (X Layer mainnet) | `exact`, `exact`+permit2, `aggr_deferred`, **`upto`** (permit2, facilitator `0x40817a0d9043732d48823c05ab2ffb643ef8d90a`), `period` (facilitator `0xc1a8035cb8090419386c25fbfcbe0f5e54c26aaa`) |
  | **eip155:1952** (X Layer **testnet**) | same set — use for the first paid test with test tokens |

- **`upto` = the Model B mechanism** — authorize a ceiling, settle the actual ≤ amount. So
  `settlement_mode="deferred_below_ceiling"` is natively supported; `charge_at_fire` is NOT needed.
- **`period`** = recurring/subscription rail (maps to our pay-as-you-go method).
- **`aggr_deferred`** = TheHouse's batch-many-into-one-settlement scheme. Combine with `upto` for
  per-caller settle-below-ceiling inside a batch.

### Testnet live test (2026-07-14, wallet funded: 10 USD₮0/USDG/USDC_TEST + 0.2 gas)

What we PROVED works (our code, live against real OKX):
- `OnchainOSFacilitator` OK-ACCESS signing on **GET and POST** — every request reached the
  facilitator with HTTP 200 (auth accepted); envelope parsing + error handling correct.
- TEE x402 payload signing via `onchainos payment pay` for both `upto` and `exact` schemes.
- `payment a2a-pay create` returns a real payment_id + EIP-3009 charge challenge (testnet + mainnet).

What is BLOCKED by OKX **testnet** infrastructure (NOT our code):
- x402 facilitator `/verify` (and `/settle`) return `code:-1 "unknown error"` on eip155:1952 for
  BOTH `upto` and `exact` — the CLI itself warned "no RPC endpoint configured for chain 1952". A
  real validation failure would return a specific reason (session_cert_invalid, requirements_mismatch);
  a generic -1 with empty data = the facilitator's verify pipeline can't run on testnet.
- `a2a-pay` resolves token symbols to contract addresses that DON'T match the faucet tokens
  (USDT→0x779ded0c… but faucet USD₮0=0x9e29b3…; USDG→0x4ae46a50… but faucet=0xa78e2baa…), so an
  EIP-3009 settle would revert on balance.

CONCLUSION: our integration is validated as far as testnet permits; a settlement that actually
lands on-chain requires **mainnet (196)** with a tiny real USDT amount.

### ✅ FIRST REAL MAINNET SETTLEMENT (2026-07-15) — DONE

Funded 1 USD₮0 on X Layer mainnet (`0x779ded0c9e1022225f8e0630b35a9b54be713736`). Real on-chain
results:
- **Permit2 approval** (one-time, required before any x402 `upto` payment): `approve(Permit2
  0x000000000022D473030F116dDEE9F6B43aC78BA3, 0.1 USDT)` — tx `0x2003473aa91d68e1…`. Gas sponsored
  (Gas Station status READY; ~$0.0003 service charge).
- **A2A charge settled on-chain** — `a2a-pay create → pay → completed`, **tx
  `0xa65fd7203bb759aa82eb6dc904b2869e079fc00f8abbce74ec60f1d8a7f5e701`**. Self-pay 0.01 USDT
  (balance intact, gas sponsored). **The real money rail works end-to-end.**

Findings that change our integration surface:
1. **The working agent payment rail is `a2a-pay` (MPP / EIP-3009 charge), not the raw x402
   `/verify`+`/settle` facilitator endpoints** — those returned `code:-1 "unknown error"` for every
   body variant on BOTH testnet and mainnet. Likely they're seller-registration/payTo-gated (we
   called with payTo=burn, not our own registered seller address), or not a buyer-callable surface.
   Our `OnchainOSFacilitator` client itself is sound (signing/envelope/`/supported` all work) —
   revisit `/verify` only in a true seller context where payTo == our account.
2. **`a2a-pay pay --amount` is RAW base units** (the challenge's `request.amount`, e.g. "10000"),
   while `create --amount` is decimal ("0.01"). Fixed in `onchain/onchainos_cli.py::a2a_pay`.
3. On-chain **gas is sponsored** on X Layer for these ops (Gas Station), no native OKB needed.

### Settlement DIRECTION MODEL (corrected after the deep audit, 2026-07-15)

The rewire initially forced BOTH directions onto a2a-pay; the audit showed that's wrong. a2a-pay
is a create-then-pay flow where the **buyer** actively pays — so it fits only one direction:

- **OUTBOUND (TheHouse → target) = a2a-pay BUYER.** TheHouse pays the target's charge via
  `RailSettlement.pay_target` (proven live: tx 0xa65fd720…). This is the correct a2a use.
- **INBOUND (caller → TheHouse) = x402 authorization settle (SELLER).** The caller presents a
  signed x402 authorization at the gate; TheHouse settles it (facilitator settle in prod; recorded
  directly as collected in dev). It is NOT a2a-pay — a create-then-pay charge would leave the
  caller having to actively pay a charge nobody triggers (the HIGH-2 collection gap).

Reconciliation now counts only `settle_status='settled'` inbound as collected, so an invoiced-but-
uncollected charge surfaces as drift instead of falsely balancing (HIGH-1). `record_inbound`
remains as a primitive for a true a2a-seller flow (TheHouse creates a charge the caller pays), but
the default pipeline inbound is the x402-authorization settle.

## 4. x402 HTTP API (the concrete surface)

- Base URL: `https://web3.okx.com`  ·  path prefix: `/api/v6/pay/x402`
- Auth headers (every request): `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-PASSPHRASE`,
  `OK-ACCESS-TIMESTAMP` (ISO-8601), `Content-Type: application/json`.
  Sign = base64(HMAC-SHA256(secret, timestamp + method + requestPath + body)).
- Envelope: `{"code":"0","msg":"success","data":{...}}`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v6/pay/x402/supported` | GET | facilitator capabilities: schemes (`exact`, `aggr_deferred`), networks, signer addresses |
| `/api/v6/pay/x402/verify` | POST | validate a payment payload (session cert chain/expiry, sig, scope, nonce) → `isValid`, `invalidReason`, `payer` |
| `/api/v6/pay/x402/settle` | POST | queue a verified auth for batch settlement → `success`, `transaction`(empty at intake), `status:"success"` = *accepted only* |
| `/api/v6/pay/x402/settle/status` | GET `?txHash=` | final on-chain outcome → `status: pending\|success\|failed` |

**Schemes:** `exact` (one-time, price known upfront) vs **`aggr_deferred`** (batched micropayments;
session cert required in `paymentPayload.accepted.extra`; settle returns success immediately, txHash
later). TheHouse's batching model maps directly onto **`aggr_deferred`**.

Business error reasons: `session_cert_invalid`, `session_cert_expired`,
`session_key_signature_invalid`, `nonce_already_used`, `out_of_session_scope`,
`requirements_mismatch`. Auth errors: HTTP 401 codes 50103–50113.

## 5. The four payment methods → our mapping

| Method | Scheme/intent | LAVARD/TheHouse use |
|--------|---------------|---------------------|
| **One-time** | `exact` / `charge` | agent-to-MCP single call (low-need user, one tool) |
| **Batch** | `aggr_deferred` | **TheHouse** compound calls — dozens of paid sub-calls, one settlement |
| **Pay-as-you-go** | escrow deposit + off-chain deduct, settle at channel close | repeated calls to one ASP at fixed rate |
| **Escrow** | Optimistic Escrow six-state | **LAVARD A2A** hire with sign-off + dispute window |

## 6. Implications for our build

- Our current onchainos-CLI adapter covers the **A2A/escrow** path (`create-task → confirm-accept
  → complete`). The **x402 HTTP facilitator** is the missing **batch/one-time** money rail — this
  is TheHouse's real prod settlement backend (replaces the `None` prod verifier).
- Reconciliation must compare `charged` (our ledger) vs `/settle/status` batch outcome — already
  scaffolded in `EconomicsEngine.reconcile_settlements`.
- Because settlement is **deferred/async**, "delivered" ≠ "paid on-chain." Our state machine must
  keep a `settlement_pending → settled|failed` transition driven by status polling, never assume
  landing on a 200.
