#!/usr/bin/env bash
# LAVARD ASP provider entrypoint (Render worker).
# First boot: unpack the identity seed onto the persistent disk. Every boot:
# configure OpenClaw->Gemini, log in, bind provider, run the XMTP daemon.
set -uo pipefail

export HOME=/data
export ONCHAINOS_HOME=/data/.onchainos   # pin so daemon children read the same session
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
  # Force a REAL AK auth. Plain `login` no-ops (returns ok, writes no session) when
  # it thinks it's already logged in — so clear BOTH the session and the account
  # marker first. XMTP identity lives in .okx-agent-task/xmtp, untouched by this.
  onchainos wallet logout >/dev/null 2>&1 || true
  rm -f "$ONCHAINOS_HOME/session.json" "$ONCHAINOS_HOME/wallets.json" 2>/dev/null
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
log "onchainos version -> $(onchainos --version 2>&1)"
log "login raw -> $(printf '%s' "$LOGIN_OUT" | head -c 240)"
log "ONCHAINOS_HOME=$ONCHAINOS_HOME  session.json? $([ -f "$ONCHAINOS_HOME/session.json" ] && echo yes || echo NO)"
log "home files -> $(ls "$ONCHAINOS_HOME" 2>&1 | tr '\n' ' ')"
log "my-asps -> $(onchainos agent get-my-agents --role asp 2>&1 | head -c 220)"

# --- bind provider + auto-respond ---
okx-a2a config provider --provider openclaw >/dev/null 2>&1 && log "provider=openclaw"
okx-a2a agent bypass on >/dev/null 2>&1 && log "bypass on"

# --- ensure the OpenClaw a2a plugin exists at runtime (build HOME=/root != runtime /data) ---
if ! openclaw plugins list 2>/dev/null | grep -qi "a2a"; then
  log "installing openclaw a2a plugin (runtime)..."
  openclaw plugins install @okxweb3/a2a-openclaw@latest --force --dangerously-force-unsafe-install >/dev/null 2>&1 || true
fi

# --- start the OpenClaw gateway (AI brain on :18789). Backgrounded; NEVER block the daemon. ---
start_gateway() {
  openclaw gateway run --force --auth none --allow-unconfigured >"$HOME/openclaw-gateway.log" 2>&1 &
  log "openclaw gateway starting pid=$!"
}
start_gateway

# --- persist state back to the disk on exit (best-effort) ---
trap 'log "persisting state..."; sync' EXIT

# --- after the daemon comes up: load the listener + heartbeat, then keep beating ---
(
  sleep 25
  log "gateway health -> $(openclaw health 2>&1 | head -c 180)"
  log "gateway log tail -> $(tail -n 4 "$HOME/openclaw-gateway.log" 2>/dev/null | tr '\n' ' ' | head -c 400)"
  okx-a2a agent refresh --json 2>/dev/null | python3 -c "import sys,json;p=json.load(sys.stdin).get('payload',{});print('[start] listener agents=',p.get('agentCount'),'activeClients=',p.get('activeClients'))" 2>/dev/null || true
  onchainos agent gate-check --role asp 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin).get('data',{});print('[start] gate-check ready=',d.get('ready'),'wallet=',(d.get('wallet') or {}).get('ok'),'identity=',(d.get('identity') or {}).get('ok'),'comm=',(d.get('communication') or {}).get('ok'))" 2>/dev/null || true
  while true; do
    sleep 60
    # self-heal: restart the OpenClaw gateway if it died (AI brain must stay up)
    if ! openclaw health >/dev/null 2>&1; then
      log "openclaw gateway down -> restarting"
      start_gateway
    fi
    # self-heal: only re-auth when the session has actually expired
    if ! onchainos agent get-my-agents --role asp 2>/dev/null | grep -q '"ok":true'; then
      log "session gone -> re-authenticating"
      onchainos wallet logout >/dev/null 2>&1 || true
      rm -f "$ONCHAINOS_HOME/session.json" "$ONCHAINOS_HOME/wallets.json" 2>/dev/null
      onchainos wallet login >/dev/null 2>&1
      okx-a2a agent refresh >/dev/null 2>&1
    fi
    onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1
  done
) &

log "starting okx-a2a daemon (foreground)"
exec okx-a2a run
