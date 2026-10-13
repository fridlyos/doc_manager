# syntax=docker/dockerfile:1
# Worker image. Same package as the API for shared domain logic, different
# entrypoint and no exposed port. Separate container gives failure isolation.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

# Tesseract for OCR of scanned/image-only PDFs (used only when DOCMAN_OCR_ENABLED).
# PyMuPDF calls the engine and needs TESSDATA_PREFIX pointing at the language data
# (Debian bookworm ships Tesseract 5 under /usr/share/tesseract-ocr/5/tessdata).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --uid 10001 --create-home appuser
USER appuser

CMD ["doc-manager-worker"]
