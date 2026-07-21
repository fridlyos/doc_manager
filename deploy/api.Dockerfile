# syntax=docker/dockerfile:1
# API image. Serves the FastAPI app and the built frontend static assets.
# Pin the base digests before first production data.
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

# uv provides reproducible, lockfile-driven installs.
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app

# Install dependencies first for layer caching.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Install the project itself.
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY --from=frontend-build /frontend/dist /app/frontend-dist

# Drop privileges.
RUN useradd --uid 10001 --create-home appuser
USER appuser

EXPOSE 8000
# DOCMAN_BIND_HOST must be 0.0.0.0 inside the container; the host port is bound
# to 127.0.0.1 by Compose so the service is not LAN-exposed by default.
CMD ["doc-manager-api"]
