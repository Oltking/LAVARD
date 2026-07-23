#!/usr/bin/env bash
# LAVARD ASP provider entrypoint (Render worker).
# First boot: unpack the identity seed onto the persistent disk. Every boot:
# configure OpenClaw->Gemini, log in, bind provider, run the XMTP daemon.
set -uo pipefail

export HOME=/data
export ONCHAINOS_HOME=/data/.onchainos   # pin so daemon children read the same session
# OpenClaw uses the image-baked plugin at /root/.openclaw (built with HOME=/root).
# Point it there so we don't re-install the plugin at runtime (which hangs).
export OPENCLAW_STATE_DIR=/root/.openclaw
export OPENCLAW_CONFIG_PATH=/root/.openclaw/openclaw.json
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

# --- OpenClaw -> Gemini config: MERGE into the plugin-registered config (don't clobber) ---
: "${GEMINI_MODEL:=gemini-3-flash}"
if [ -z "${GEMINI_API_KEY:-}" ]; then log "FATAL: GEMINI_API_KEY not set"; fi
python3 - <<'PY'
import json, os
p = os.environ["OPENCLAW_CONFIG_PATH"]
try:
    cfg = json.load(open(p))          # keep plugins.entries.okx-a2a from build
except Exception:
    cfg = {}
model = os.environ.get("GEMINI_MODEL", "gemini-3-flash")
cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = {"primary": f"gemini/{model}"}
m = cfg.setdefault("models", {}); m["mode"] = "merge"
m.setdefault("providers", {})["gemini"] = {
    "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "apiKey": os.environ["GEMINI_API_KEY"],
    "api": "openai-completions",
    "models": [{"id": model, "name": "Gemini"}],
}
# trust the okx-a2a plugin and unblock its hooks (needed to process agent runs)
plugins = cfg.setdefault("plugins", {})
plugins["allow"] = sorted(set(plugins.get("allow", []) + ["okx-a2a"]))
oa = plugins.setdefault("entries", {}).setdefault("okx-a2a", {})
oa["enabled"] = True
oa.setdefault("hooks", {})["allowConversationAccess"] = True
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(cfg, open(p, "w"), indent=2)
print("merged gemini into", p)
PY
log "openclaw configured for gemini/$GEMINI_MODEL (plugins preserved)"

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

# --- bind provider ---
okx-a2a config provider --provider openclaw >/dev/null 2>&1 && log "provider=openclaw"
okx-a2a agent bypass on >/dev/null 2>&1 && log "bypass on"

# --- HARD RESET of daemon RUNTIME state (never the identity) ---------------
# The persistent disk keeps stale locks/sockets + any straggler daemon across
# restarts, which makes the real XMTP listener refuse to start ("another daemon
# is already running"). Kill any straggler and wipe ONLY run/ + logs/. Identity
# (xmtp/, .onchainos session, keyring) is left completely untouched.
log "hard-reset: killing straggler daemons + clearing runtime locks"
pkill -f 'okx-a2a run' 2>/dev/null || true
pkill -f 'a2a-node'    2>/dev/null || true
sleep 2
rm -rf "$HOME/.okx-agent-task/run" "$HOME/.okx-agent-task/logs" 2>/dev/null
mkdir -p "$HOME/.okx-agent-task/run" "$HOME/.okx-agent-task/logs"

trap 'log "persisting state..."; sync' EXIT

# --- 1) THE single XMTP listener daemon (background) -----------------------
log "starting okx-a2a node daemon (single XMTP listener)"
okx-a2a run >"$HOME/okx-a2a-daemon.log" 2>&1 &
DAEMON_PID=$!
sleep 10
log "daemon log tail -> $(tail -n 6 "$HOME/okx-a2a-daemon.log" 2>/dev/null | tr '\n' ' ' | head -c 400)"

# If the listener died on startup there is nothing to answer calls — exit
# non-zero so Render restarts the container CLEAN instead of sitting half-alive.
if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
  log "FATAL: XMTP listener exited on startup -> forcing clean container restart"
  tail -n 20 "$HOME/okx-a2a-daemon.log" 2>/dev/null
  exit 1
fi

# --- 2) GATE on canonical readiness before going live ----------------------
# Do NOT start the brain until communication is ACTUALLY up. Run the vendor's
# canonical repair (doctor --fix) on a loop until ready:true. If it never gets
# there, exit non-zero -> Render restarts clean rather than lying about "live".
READY=0
for i in $(seq 1 12); do
  OUT="$(timeout 45 okx-a2a doctor --fix --json 2>/dev/null)"
  if printf '%s' "$OUT" | python3 -c "import sys,json
d=json.load(sys.stdin)
r=d.get('ready') or (d.get('data') or {}).get('ready') or (d.get('payload') or {}).get('ready')
sys.exit(0 if r else 1)" 2>/dev/null; then
    READY=1; log "A2A communication ready after doctor --fix (attempt $i/12)"; break
  fi
  log "not ready yet (attempt $i/12) -> $(printf '%s' "$OUT" | tr '\n' ' ' | head -c 160)"
  sleep 10
done
if [ "$READY" != "1" ]; then
  log "FATAL: A2A never reached ready:true after doctor --fix -> forcing clean restart"
  onchainos agent gate-check --role asp 2>/dev/null | head -c 500
  exit 1
fi

# --- self-heal loop: keep the listener alive + session fresh + heartbeat ----
(
  timeout 30 okx-a2a agent refresh --json 2>/dev/null | python3 -c "import sys,json;p=json.load(sys.stdin).get('payload',{});print('[start] listener agents=',p.get('agentCount'),'activeClients=',p.get('activeClients'))" 2>/dev/null || true
  while true; do
    sleep 60
    # (a) listener liveness — restart it in place (clean locks first) if it died
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
      log "listener down -> restarting it"
      pkill -f 'a2a-node' 2>/dev/null || true; sleep 2
      rm -rf "$HOME/.okx-agent-task/run"; mkdir -p "$HOME/.okx-agent-task/run"
      okx-a2a run >"$HOME/okx-a2a-daemon.log" 2>&1 &
      DAEMON_PID=$!
    fi
    # (b) session liveness — re-auth from env creds if the wallet session lapsed
    if ! timeout 30 onchainos agent get-my-agents --role asp 2>/dev/null | grep -q '"ok":true'; then
      log "session gone -> re-authenticating"
      timeout 30 onchainos wallet logout >/dev/null 2>&1 || true
      rm -f "$ONCHAINOS_HOME/session.json" "$ONCHAINOS_HOME/wallets.json" 2>/dev/null
      timeout 40 onchainos wallet login >/dev/null 2>&1
    fi
    timeout 30 onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1 || true
  done
) &

# --- 3) AI BRAIN = the OpenClaw gateway (foreground; Gemini). Connects to the
#     single listener above. This is the container's main process. ---
log "A2A ready — starting OpenClaw gateway (foreground, Gemini brain)"
exec openclaw gateway run --force --auth none --allow-unconfigured
