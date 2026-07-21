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

# --- OpenClaw skill discovery: it reads the okx-ai protocol from a symlink.
#     Without this the brain won't know how to answer a task envelope. ---
if [ ! -d "$HOME/.openclaw/onchainos-skills/skills" ]; then
  git clone --depth 1 https://github.com/okx/onchainos-skills "$HOME/.openclaw/onchainos-skills" >/dev/null 2>&1 \
    && log "cloned onchainos-skills" || log "WARN: skills clone failed"
fi
mkdir -p "$HOME/.agents/skills"
[ -e "$HOME/.agents/skills/onchainos-skills" ] || ln -s "$HOME/.openclaw/onchainos-skills/skills" "$HOME/.agents/skills/onchainos-skills"
log "openclaw skills linked: $(ls "$HOME/.agents/skills/onchainos-skills" 2>/dev/null | tr '\n' ' ')"

# --- wallet session: AK login from env creds (authoritative; seeded session is
#     device-bound and cannot self-renew on a different host, so we re-mint here) ---
if [ -n "${OKX_API_KEY:-}" ] && [ -n "${OKX_SECRET_KEY:-}" ] && [ -n "${OKX_PASSPHRASE:-}" ]; then
  # drop any stale/expired seeded session so login does a clean fresh AK auth
  rm -f "$HOME/.onchainos/session.json" 2>/dev/null
  # version-proof: plain login first (AK from env), fall back to --force if supported
  LOGIN_OUT="$(onchainos wallet login 2>&1)"
  echo "$LOGIN_OUT" | grep -q '"ok":true' || LOGIN_OUT="$(onchainos wallet login --force 2>&1)"
  if echo "$LOGIN_OUT" | grep -q '"ok":true'; then
    log "AK login ok (from env creds)"
  else
    log "AK login FAILED -> $LOGIN_OUT"
    # surface geo/region status too (OKX blocks some datacenter regions)
    log "geoblock check -> $(onchainos wallet geoblock 2>&1 | head -c 200)"
    log "egress IP -> $(curl -s --max-time 8 https://api.ipify.org 2>/dev/null) ; country -> $(curl -s --max-time 8 https://ipapi.co/country 2>/dev/null)"
  fi
else
  log "FATAL: OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE not set — cannot authenticate"
fi
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
  onchainos agent gate-check --role asp 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin).get('data',{});print('[start] gate-check ready=',d.get('ready'),'wallet=',(d.get('wallet') or {}).get('ok'),'identity=',(d.get('identity') or {}).get('ok'),'comm=',(d.get('communication') or {}).get('ok'))" 2>/dev/null || true
  while true; do
    onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1
    sleep 90
  done
) &

log "starting okx-a2a daemon (foreground)"
exec okx-a2a run
