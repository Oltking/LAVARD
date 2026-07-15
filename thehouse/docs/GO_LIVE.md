# TheHouse — Go-Live Checklist (OKX.AI listing)

The codebase is feature-complete against the acceptance run (`python -m scripts.acceptance_run`).
Going live on OKX.AI requires steps that only an operator with credentials can perform:

## 1. Credentials & wallet (email path — confirmed with owner)

```bash
# 1. install the Onchain OS skills pack globally (provides the `onchainos` CLI + agent skills)
npx skills add okx/onchainos-skills --yes -g

# 2. create/log into the TEE Agentic Wallet with your email (OTP flow — no seed phrase;
#    keys live in the TEE, nothing to back up or leak)
onchainos wallet login your@email.com
#    → enter the one-time code sent to the email

# 3. confirm the session and note your wallet address
onchainos wallet status
```

- [ ] Wallet created via email OTP and `wallet status` shows a live session
- [ ] Fund the wallet with USDT on X Layer (identity ops are free — OKX covers gas;
      only outbound target payments need balance; start with a small test amount)
- [ ] Optional (API-level integrations): apply for OKX API credentials at the Developer
      Portal (`web3.okx.com/onchain-os/dev-portal`) and set `OKX_API_KEY` /
      `OKX_SECRET_KEY` / `OKX_PASSPHRASE` in `.env`

## 2. Deploy
- [ ] Deploy the stack (`infra/docker-compose.yml` for Postgres/Redis/Qdrant; build the
      app image with the repo `Dockerfile`; `THEHOUSE_PROFILE=prod`; one `uvicorn
      core.api:app` process serves the pages, REST, and the paid MCP gateway at `/mcp`
      behind one public **https** URL ≤512 chars — the endpoint registered on-chain is
      permanent).
- [ ] Set `THEHOUSE_INTERNAL_API_TOKEN` — in prod, `/v1/call`, `/v1/queue/*`, and `/desk`
      require the `X-Internal-Token` header; callers use the paid `/mcp` gateway.
- [ ] Switch the gateway to production payment components: `OnchainOSSigner` for outbound
      (replaces `DevSigner`) and facilitator-backed verification for inbound (replaces
      `DevPaymentVerifier`). Both are single constructor swaps in the app wiring.

## 3. Populate the registry
- [ ] Discover target ASPs: `onchainos agent search --query ...` +
      `agent service-list --agent-id N` → endpoints and registered fees.
- [ ] Onboard each target through the Profiler (mode + transport detection); verify
      Mode A targets honor the numeric wrapper before advertising a discount.

## 4. Register TheHouse as an ASP
- [ ] `onchainos agent pre-check --role asp` → consent.
- [ ] `agent create --role asp` — brand name, description, avatar (image file required),
      one A2MCP service per aggregated target with fee = target × 0.80 (see QUESTIONS.md Q1/Q4).
- [ ] Pass `validate-listing` QA + platform approval; `agent activate #id`; complete the
      post-create communication subflow (okx-a2a runtime).

## 5. Keep prices in sync automatically
- [ ] Wire `onchain/sync.py` in the prod app: `PriceSyncService(engine,
      OnchainOSFeeSource(agent_ids), OnchainOSListingUpdater(engine, thehouse_agent_id,
      endpoint)).start(interval_s=3600)`. `agent_ids` maps each registry asp_id to the
      target's OKX agent id (collected in step 3).
- [ ] When a target changes its registered fee, the sync re-derives
      `thehouse_price = fee × 0.80`, the 402 gate quotes it on the next call, the
      directory shows it, and one `agent update` pushes the new fee to TheHouse's own
      OKX listing. Every change lands in the audit log (`event = price_sync`).

## 6. Verify on the live listing
- [ ] Re-run the §10 acceptance flow against real targets with small amounts.
- [ ] Confirm settlements land in the TheHouse wallet (PAYMENT-RESPONSE tx hashes in the
      settlements table).
- [ ] Manually re-check OKX.AI review policy for aggregator/reseller services (QUESTIONS.md Q5)
      — the platform docs were unreachable from the build environment.
