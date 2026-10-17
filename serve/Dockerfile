# Abiso serve: Messenger webhook + MCP + A2A on Cloud Run.
# Build context is the repo root:  docker build -f serve/Dockerfile .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY abiso/ abiso/
COPY serve/ serve/
# The refresh job (serve/refresh.py) captures with collect/ and prices Gemini with eval/smoke.py.
COPY collect/ collect/
COPY eval/__init__.py eval/smoke.py eval/
# The compiled feed is baked into the image for the prototype; redeploy to refresh it.
# data/ always exists in the build context (serve/cloudbuild.yaml creates it if empty).
COPY data/ data/

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn serve.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
