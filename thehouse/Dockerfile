FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY core ./core
COPY directory ./directory
COPY gateway ./gateway
COPY onchain ./onchain
COPY assets ./assets

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000

# prod deployments set THEHOUSE_PROFILE=prod, THEHOUSE_DATABASE_URL, THEHOUSE_REDIS_URL,
# THEHOUSE_INTERNAL_API_TOKEN (see docs/GO_LIVE.md)
CMD ["uvicorn", "core.api:app", "--host", "0.0.0.0", "--port", "8000"]
