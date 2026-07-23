# LAVARD ASP provider — always-on agent 5909 for OKX AI.
# Runs the okx-a2a XMTP daemon STANDALONE (per-call codex provider, no gateway)
# with Codex pointed at free Gemini as the AI brain. Standalone => `refresh`
# works => the agent actually responds to calls (OpenClaw's embedded gateway did not).
FROM node:24-bookworm-slim

# system deps: curl (installer), ca-certs, python3 (LAVARD core is stdlib), gettext (envsubst), tar
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git python3 gettext-base tar procps \
    && rm -rf /var/lib/apt/lists/*

# --- onchainos CLI PINNED to v4.2.3 ---
# The latest (v4.3.0) fails to persist an AK wallet session on Linux/headless
# (login returns ok but writes no session.json). v4.2.3 works; pin it exactly.
ARG ONCHAINOS_VERSION=v4.2.3
RUN arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)  tgt=x86_64-unknown-linux-gnu ;; \
      aarch64) tgt=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac; \
    mkdir -p /root/.local/bin; \
    curl -fsSL "https://github.com/okx/onchainos-skills/releases/download/${ONCHAINOS_VERSION}/onchainos-${tgt}" -o /root/.local/bin/onchainos; \
    chmod +x /root/.local/bin/onchainos; \
    /root/.local/bin/onchainos --version
ENV PATH="/root/.local/bin:${PATH}"

# --- okx-a2a daemon + Codex CLI (per-call provider, no gateway) ---
RUN npm install -g @okxweb3/a2a-node@latest @openai/codex@latest

# --- LAVARD app (the AI runs in this cwd) ---
WORKDIR /app
COPY . /app

# identity + all runtime state live on the mounted persistent disk
ENV HOME=/data
ENV GEMINI_MODEL=gemini-3.1-flash-lite

RUN chmod +x /app/deploy/start.sh
CMD ["/app/deploy/start.sh"]
