# LAVARD ASP provider — always-on agent 5909 for OKX AI.
# Runs the okx-a2a XMTP daemon with OpenClaw -> Gemini (free) as the AI brain.
FROM node:24-bookworm-slim

# system deps: curl (installer), ca-certs, python3 (LAVARD core is stdlib), gettext (envsubst), tar
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git python3 gettext-base tar procps \
    && rm -rf /var/lib/apt/lists/*

# --- onchainos CLI (auto-detects linux, installs to ~/.local/bin) ---
RUN curl -sSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# --- okx-a2a daemon + OpenClaw runtime + OKX OpenClaw plugin ---
RUN npm install -g @okxweb3/a2a-node@latest openclaw@latest \
    && openclaw plugins install @okxweb3/a2a-openclaw@latest --force --dangerously-force-unsafe-install || true

# --- LAVARD app (the AI runs in this cwd) ---
WORKDIR /app
COPY . /app

# identity + all runtime state live on the mounted persistent disk
ENV HOME=/data
ENV GEMINI_MODEL=gemini-3.1-flash-lite

RUN chmod +x /app/deploy/start.sh
CMD ["/app/deploy/start.sh"]
