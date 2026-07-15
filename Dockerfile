# LAVARD — orchestration ASP for OKX AI.
# Prod deployments set: LAVARD_PROFILE=prod, LAVARD_API_KEY, LAVARD_AUDIT_KEY,
# LAVARD_DATABASE_URL (postgresql+psycopg://…), LAVARD_REDIS_URL, and OKX_* creds when live.
# The API refuses to boot in prod with the default audit key or no API key (validate_for_prod).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core
COPY api ./api
COPY onchain ./onchain
COPY mcp ./mcp
COPY cli.py ./
# TheHouse sub-package (bundled aggregator) — optional but on by default (LAVARD_USE_THEHOUSE=1)
COPY thehouse ./thehouse

EXPOSE 8000

# healthcheck hits the always-public liveness endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
