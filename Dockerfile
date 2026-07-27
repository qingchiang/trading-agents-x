# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS python-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY cli ./cli
COPY tradingagents ./tradingagents
COPY --from=frontend-builder /src/tradingagents/web/static ./tradingagents/web/static

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip wheel \
      --no-deps \
      --wheel-dir /wheels \
      . \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install \
      /wheels/trading_agents_x-*.whl


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
