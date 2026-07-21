#!/usr/bin/env bash
# LAVARD ASP provider entrypoint (Render worker).
# First boot: unpack the identity seed onto the persistent disk. Every boot:
# configure OpenClaw->Gemini, log in, bind provider, run the XMTP daemon.
set -uo pipefail

export HOME=/data
export PATH="/root/.local/bin:${PATH}"
CHAIN_INDEX=196
AGENT=5909
log(){ echo "[start $(date '+%H:%M:%S')] $*"; }

mkdir -p "$HOME/.onchainos" "$HOME/.okx-agent-task/xmtp" "$HOME/.openclaw"

# --- one-time identity seed (Render Secret File mounted at /etc/secrets/IDENTITY_SEED) ---
if [ ! -f "$HOME/.seeded" ]; then
  if [ -f /etc/secrets/IDENTITY_SEED ]; then
    log "seeding identity from IDENTITY_SEED..."
    base64 -d /etc/secrets/IDENTITY_SEED | tar xzf - -C "$HOME" && touch "$HOME/.seeded" \
      && log "seed unpacked" || log "SEED FAILED — check the secret file"
  else
    log "WARNING: no identity seed and disk is empty — agent has no wallet/XMTP identity yet"
  fi
fi

# --- OpenClaw -> Gemini config (from env) ---
: "${GEMINI_MODEL:=gemini-3.1-flash-lite}"
if [ -z "${GEMINI_API_KEY:-}" ]; then log "FATAL: GEMINI_API_KEY not set"; fi
envsubst < /app/deploy/openclaw.json.tmpl > "$HOME/.openclaw/openclaw.json"
log "openclaw configured for gemini/$GEMINI_MODEL"

# --- wallet session: refresh AK login (uses seeded session; env creds as fallback) ---
onchainos wallet login --force >/dev/null 2>&1 && log "wallet login ok" || log "wallet login returned non-zero (may already be valid)"
onchainos wallet status 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('[start] login=',d.get('loginType'),'account=',d.get('currentAccountName'))" 2>/dev/null || true

# --- bind provider + auto-respond ---
okx-a2a config provider --provider openclaw >/dev/null 2>&1 && log "provider=openclaw"
okx-a2a agent bypass on >/dev/null 2>&1 && log "bypass on"

# --- persist state back to the disk on exit (best-effort) ---
trap 'log "persisting state..."; sync' EXIT

# --- after the daemon comes up: load the listener + heartbeat, then keep beating ---
(
  sleep 25
  okx-a2a agent refresh --json 2>/dev/null | python3 -c "import sys,json;p=json.load(sys.stdin).get('payload',{});print('[start] listener agents=',p.get('agentCount'),'activeClients=',p.get('activeClients'))" 2>/dev/null || true
  while true; do
    onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1
    sleep 90
  done
) &

log "starting okx-a2a daemon (foreground)"
exec okx-a2a run
