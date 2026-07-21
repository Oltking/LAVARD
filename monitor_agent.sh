#!/bin/bash
# LAVARD agent 5909 liveness monitor.
# Polls online status every 2 min. On drop: auto-recovers (refresh + heartbeat +
# bypass) and fires a macOS notification with sound. Logs everything.

AGENT=5909
CHAIN_INDEX=196
INTERVAL=120
LOG="$HOME/.okx-agent-task/logs/lavard-monitor.log"
mkdir -p "$(dirname "$LOG")"

notify() {  # title, message, sound
  osascript -e "display notification \"$2\" with title \"$1\" sound name \"$3\"" 2>/dev/null
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $1" | tee -a "$LOG"; }

log "monitor started (pid $$) — polling every ${INTERVAL}s"
prev_ok=1

while true; do
  # 1) online status from backend
  online=$(onchainos agent profile "$AGENT" 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['data'].get('onlineStatus',0))" 2>/dev/null)
  # 2) local listener client count
  clients=$(okx-a2a agent refresh --json 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['payload'].get('activeClients',0))" 2>/dev/null)
  # 3) login mode (must be owner AK, not buyer email)
  ltype=$(onchainos wallet status 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['data'].get('loginType',''))" 2>/dev/null)

  ok=1
  [ "$online" = "1" ] || ok=0
  [ "$clients" = "1" ] || ok=0
  [ "$ltype" = "ak" ] || ok=0

  if [ "$ok" = "1" ]; then
    [ "$prev_ok" = "0" ] && { log "RECOVERED — online=$online clients=$clients login=$ltype"; notify "LAVARD 5909 recovered" "Agent is back online." "Glass"; }
    log "OK  online=$online clients=$clients login=$ltype"
  else
    log "DOWN  online=$online clients=$clients login=$ltype — attempting recovery"
    notify "⚠️ LAVARD 5909 OFFLINE" "online=$online clients=$clients login=$ltype. Recovering..." "Sosumi"
    # auto-recover (only meaningful if still on owner account)
    if [ "$ltype" = "ak" ]; then
      okx-a2a agent refresh >/dev/null 2>&1
      okx-a2a agent bypass on >/dev/null 2>&1
      onchainos agent heartbeat --chain-index "$CHAIN_INDEX" >/dev/null 2>&1
    else
      notify "⚠️ LAVARD wrong account" "Not on owner AK login — 5909 cannot listen. Fix login." "Basso"
      log "  cannot auto-recover: login=$ltype (need ak). Manual fix required."
    fi
    # keep-awake guard
    pgrep -x caffeinate >/dev/null || { caffeinate -dimsu & log "  restarted caffeinate (pid $!)"; }
  fi

  prev_ok=$ok
  sleep "$INTERVAL"
done
