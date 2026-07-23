#!/usr/bin/env bash
# LAVARD ASP provider entrypoint (Render worker).
# STANDALONE okx-a2a daemon (per-call `codex` provider, NO gateway) with Codex
# pointed at free Gemini. Standalone => `okx-a2a agent refresh` works => the
# agent actually receives and answers calls.
set -uo pipefail

export HOME=/data
export ONCHAINOS_HOME=/data/.onchainos
export PATH="/root/.local/bin:${PATH}"
CHAIN_INDEX=196
AGENT=5909
log(){ echo "[start $(date '+%H:%M:%S')] $*"; }

mkdir -p "$HOME/.onchainos" "$HOME/.okx-agent-task/xmtp" "$HOME/.codex" "$HOME/.agents/skills"

# --- one-time identity seed (Render Secret File at /etc/secrets/IDENTITY_SEED) ---
if [ ! -f "$HOME/.seeded" ]; then
  if [ -f /etc/secrets/IDENTITY_SEED ]; then
    log "seeding identity from IDENTITY_SEED..."
    base64 -d /etc/secrets/IDENTITY_SEED | tar xzf - -C "$HOME" && touch "$HOME/.seeded" \
      && log "seed unpacked" || log "SEED FAILED"
  else
    log "WARNING: no identity seed and disk is empty"
  fi
fi

# --- Codex -> Gemini (free) config: OpenAI-compatible endpoint, no-approval sandbox ---
: "${GEMINI_MODEL:=gemini-3.1-flash-lite}"
if [ -z "${GEMINI_API_KEY:-}" ]; then log "FATAL: GEMINI_API_KEY not set"; fi
cat > "$HOME/.codex/config.toml" <<EOF
model = "${GEMINI_MODEL}"
model_provider = "gemini"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[model_providers.gemini]
name = "Gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
env_key = "GEMINI_API_KEY"
wire_api = "chat"
EOF
log "codex configured for gemini/${GEMINI_MODEL}"

# --- skill discovery (codex reads the okx-ai task protocol from ~/.agents/skills) ---
if [ ! -d "$HOME/.codex/onchainos-skills/skills" ]; then
  git clone --depth 1 https://github.com/okx/onchainos-skills "$HOME/.codex/onchainos-skills" >/dev/null 2>&1 \
    && log "cloned onchainos-skills" || log "WARN: skills clone failed"
fi
[ -e "$HOME/.agents/skills/onchainos-skills" ] || ln -s "$HOME/.codex/onchainos-skills/skills" "$HOME/.agents/skills/onchainos-skills"
log "skills linked: $(ls "$HOME/.agents/skills/onchainos-skills" 2>/dev/null | tr '\n' ' ')"

# --- wallet session: fresh AK login from env creds ---
if [ -n "${OKX_API_KEY:-}" ] && [ -n "${OKX_SECRET_KEY:-}" ] && [ -n "${OKX_PASSPHRASE:-}" ]; then
  onchainos wallet logout >/dev/null 2>&1 || true
  rm -f "$ONCHAINOS_HOME/session.json" "$ONCHAINOS_HOME/wallets.json" 2>/dev/null
  LOGIN_OUT="$(onchainos wallet login 2>&1)"
  echo "$LOGIN_OUT" | grep -q '"ok":true' && log "AK login ok" || log "AK login FAILED -> $LOGIN_OUT"
else
  log "FATAL: OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE not set"
fi
log "version -> $(onchainos --version 2>&1) | session.json? $([ -f "$ONCHAINOS_HOME/session.json" ] && echo yes || echo NO)"
log "my-asps -> $(onchainos agent get-my-agents --role asp 2>&1 | head -c 160)"

# --- bind provider=codex + auto-respond ---
okx-a2a config provider --provider codex >/dev/null 2>&1 && log "provider=codex"
okx-a2a config permissions --preset bypass >/dev/null 2>&1 || true
okx-a2a agent bypass on >/dev/null 2>&1 && log "bypass on"

# config/bypass already started the standalone daemon in the background; make sure it's up.
okx-a2a start >/dev/null 2>&1 || true

# --- background: confirm listener + heartbeat + self-heal ---
(
  sleep 30
  log "refresh -> $(timeout 45 okx-a2a agent refresh --json 2>&1 | head -c 200)"
  timeout 30 onchainos agent gate-check --role asp 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin).get('data',{});print('[start] gate-check ready=',d.get('ready'),'comm=',(d.get('communication') or {}).get('ok'))" 2>/dev/null || true
  while true; do
    sleep 60
    if ! timeout 30 onchainos agent get-my-agents --role asp 2>/dev/null | grep -q '"ok":true'; then
      log "session gone -> re-authenticating"
      timeout 30 onchainos wallet logout >/dev/null 2>&1 || true
      rm -f "$ONCHAINOS_HOME/session.json" "$ONCHAINOS_HOME/wallets.json" 2>/dev/null
      timeout 40 onchainos wallet login >/dev/null 2>&1
      timeout 45 okx-a2a agent refresh >/dev/null 2>&1
    fi
    timeout 30 onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1
  done
) &

# --- MAIN PROCESS: keepalive around the already-running standalone daemon.
#     (Do NOT `okx-a2a run` a second one — it collides and crash-loops.) ---
sleep 8
log "daemon status -> $(okx-a2a status 2>&1 | tr '\n' ' ' | head -c 220)"
log "entering keepalive (standalone daemon is the background listener)"
while true; do
  sleep 30
  okx-a2a status 2>&1 | grep -qiE "running|ready|pid=" || { log "daemon down -> restart"; okx-a2a start >/dev/null 2>&1 || true; }
done
