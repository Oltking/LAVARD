#!/usr/bin/env bash
# Run this ON THE MAC (while logged into the owner AK account) to package agent
# 5909's identity into a single seed file you upload to Render as a Secret File.
# The seed contains SECRETS — never commit it, never share it.
set -euo pipefail

OUT=/tmp/lavard-seed.b64
TAR=/tmp/lavard-seed.tar.gz

cd "$HOME"
# minimal, sufficient state: wallet creds/session + XMTP identity + a2a config.
# Exclude bulky, non-essential logs/caches (audit.jsonl, workflows, *-wal/-shm).
tar czf "$TAR" \
  --exclude='.onchainos/audit.jsonl' \
  --exclude='.onchainos/workflows' \
  --exclude='.onchainos/bin' \
  --exclude='*-wal' --exclude='*-shm' \
  .onchainos/session.json \
  .onchainos/wallets.json \
  .onchainos/chain_cache.json \
  .okx-agent-task/xmtp \
  .okx-agent-task/config.toml \
  2>/dev/null

base64 -i "$TAR" -o "$OUT"
rm -f "$TAR"

echo "Seed written to: $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
echo
echo "Next:"
echo "  1. Render dashboard -> your service -> Environment -> Secret Files -> Add"
echo "     Filename: IDENTITY_SEED"
echo "     Contents: paste the ENTIRE contents of $OUT"
echo "  2. IMPORTANT: once Render is live, STOP the Mac daemon so the XMTP"
echo "     identity only runs in one place:  okx-a2a stop  &&  pkill -f monitor_agent.sh"
