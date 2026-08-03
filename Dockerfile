# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build


FROM ghcr.io/astral-sh/uv:0.12.1 AS uv


FROM python:3.12-slim AS python-builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY cli ./cli
COPY tradingagents ./tradingagents
COPY --from=frontend-builder /src/tradingagents/web/static ./tradingagents/web/static

RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /wheels \
 && uv pip install \
      --python /opt/venv/bin/python \
      --no-deps \
      /wheels/trading_agents_x-*.whl \
 && uv pip check --python /opt/venv/bin/python


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADINGAGENTS_HOME=/data \
    TRADINGAGENTS_DATABASE_PATH=/data/tradingagents.db \
    TRADINGAGENTS_CACHE_DIR=/data/cache

COPY --from=python-builder /opt/venv /opt/venv

RUN useradd --system --create-home --uid 10001 appuser \
 && install -d -m 0755 -o appuser -g appuser /data /app

USER appuser
WORKDIR /app
VOLUME ["/data"]

ENTRYPOINT ["tradingagents"]
CMD ["--help"]
