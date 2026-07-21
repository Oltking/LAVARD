# Deploying LAVARD (agent 5909) to Render — laptop-free

This moves the ASP provider off your Mac to an always-on Render worker, using
**OpenClaw + Google Gemini (free)** as the AI brain. When it's live, your laptop
can close for good.

## What it costs
- Render **Starter worker**: ~$7/month (always-on; the free tier sleeps and would
  put the agent offline — do not use free).
- Render **1 GB disk**: a few cents/month.
- Gemini API: **free tier** (`gemini-3.1-flash-lite`). Enough for review + light use.

## The one hard rule
Agent 5909's XMTP identity can run in **exactly one place**. Once Render is live,
**stop the Mac daemon** (`okx-a2a stop`). Never run both — they will conflict.

---

## Step by step

### 1. Get the free Gemini key (already have one)
`https://aistudio.google.com/apikey` -> Create API key. No card.

### 2. Package your identity (on the Mac, owner account logged in)
```
bash deploy/make-seed.sh
```
This writes `/tmp/lavard-seed.b64`. It contains secrets — do not commit/share.

### 3. Push this repo to GitHub (secrets are gitignored)
The `deploy/` folder ships; `.env` and the seed do NOT.

### 4. Create the Render service
- Render dashboard -> **New** -> **Blueprint** -> point at this repo.
  Render reads `deploy/render.yaml` and creates the worker + disk.
- Or **New -> Background Worker -> Docker**, Dockerfile `deploy/Dockerfile`.

### 5. Set the secrets in Render
Under the service -> **Environment**:
- `GEMINI_API_KEY` = your Gemini key
- (optional fallback) `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` from `.env`

Under **Secret Files** -> Add:
- Filename: `IDENTITY_SEED`
- Contents: paste all of `/tmp/lavard-seed.b64`

### 6. First deploy + watch logs
Expect to see, in order:
```
seed unpacked
openclaw configured for gemini/gemini-3.1-flash-lite
wallet login ok ... login= ak
provider=openclaw
bypass on
listener agents= 1 activeClients= 1
```
`activeClients= 1` is the green light — the agent is listening in the cloud.

### 7. Cut over from the Mac
Once Render shows `activeClients= 1`:
```
okx-a2a stop
pkill -f monitor_agent.sh
```
Now close the laptop.

### 8. Resubmit for OKX review (if not already under review)
```
onchainos agent activate --agent-id 5909 --chain xlayer --preferred-language en-US
```

---

## First-boot gotchas (we may need one debug pass)
- **`wallet login` non-zero**: the seeded session may have expired between
  packaging and deploy. Re-run `deploy/make-seed.sh` right before deploying, or
  rely on the `OKX_*` env fallback.
- **`activeClients= 0`**: the daemon didn't bind the identity — check the seed
  unpacked (`.seeded` present) and that `HOME=/data`.
- **Slow / empty AI replies**: confirm `GEMINI_API_KEY` is set and the model name
  is current (`gemini-3.1-flash-lite`).
