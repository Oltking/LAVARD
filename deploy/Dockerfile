# LAVARD ASP provider — always-on agent 5909 for OKX AI.
# Runs the okx-a2a XMTP daemon with OpenClaw -> Gemini (free) as the AI brain.
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
    # Also expose it on the DEFAULT PATH. The AI brain shells out via a login
    # shell (`/bin/bash -lc`) to run `onchainos agent next-action|apply|deliver`;
    # a login shell resets PATH from /etc/profile and drops /root/.local/bin, so
    # `command -v onchainos` fails and the reply commands never run. Symlinking
    # into /usr/local/bin (always on the default PATH) makes it resolvable in
    # every shell, and a profile.d line covers HOME=/data login shells too.
    ln -sf /root/.local/bin/onchainos /usr/local/bin/onchainos; \
    printf 'export PATH="/root/.local/bin:$PATH"\nexport HOME=/data\nexport ONCHAINOS_HOME=/data/.onchainos\nexport OPENCLAW_STATE_DIR=/root/.openclaw\nexport OPENCLAW_CONFIG_PATH=/root/.openclaw/openclaw.json\n' > /etc/profile.d/onchainos.sh; \
    /root/.local/bin/onchainos --version; \
    /bin/bash -lc 'command -v onchainos'
ENV PATH="/root/.local/bin:${PATH}"

# --- okx-a2a daemon + OpenClaw runtime + OKX OpenClaw plugin ---
RUN npm install -g @okxweb3/a2a-node@latest openclaw@latest \
    && openclaw plugins install @okxweb3/a2a-openclaw@latest --force --dangerously-force-unsafe-install || true

# --- LAVARD app (the AI runs in this cwd) ---
WORKDIR /app
COPY . /app

# identity + all runtime state live on the mounted persistent disk
ENV HOME=/data
ENV GEMINI_MODEL=gemini-3-flash

RUN chmod +x /app/deploy/start.sh
CMD ["/app/deploy/start.sh"]
