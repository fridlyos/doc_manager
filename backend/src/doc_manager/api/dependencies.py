"""FastAPI dependencies: database sessions and idempotency-key handling."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doc_manager.api.errors import Problem
from doc_manager.core.config import Settings
from doc_manager.db.models import IdempotencyRecord, IngestionJob


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_retrieval_service(request: Request) -> Any:
    """The process-shared retrieval service (loads the embedding model once).

    Built lazily on first search and cached on ``app.state`` so the FastEmbed model
    loads at most once per process. Tests inject a fake by pre-setting
    ``app.state.retrieval_service``.
    """
    existing = getattr(request.app.state, "retrieval_service", None)
    if existing is not None:
        return existing
    # Imported here to keep the heavy embedding/vector stack out of import time.
    from doc_manager.embedding import build_embedding_service
    from doc_manager.retrieval import RetrievalService
    from doc_manager.vectors import build_qdrant_repository

    settings: Settings = request.app.state.settings
    embedding = build_embedding_service(settings)
    repo = build_qdrant_repository(settings, embedding.profile)
    service = RetrievalService(embedding, repo)
    request.app.state.retrieval_service = service
    return service


def get_provider_registry(request: Request) -> Any:
    """The process-shared generation provider registry (Phase 5)."""
    existing = getattr(request.app.state, "provider_registry", None)
    if existing is not None:
        return existing
    from doc_manager.generation import build_registry

    settings: Settings = request.app.state.settings
    registry = build_registry(settings)
    request.app.state.provider_registry = registry
    return registry


def get_ask_service(request: Request) -> Any:
    """The process-shared Ask orchestrator. Tests may inject one on app.state."""
    existing = getattr(request.app.state, "ask_service", None)
    if existing is not None:
        return existing
    from doc_manager.generation.ask import AskService

    settings: Settings = request.app.state.settings
    service = AskService(get_retrieval_service(request), settings)
    request.app.state.ask_service = service
    return service


def require_idempotency_key(request: Request) -> str:
    """Job-creating POSTs require Idempotency-Key (contract section 6.1)."""
    key = request.headers.get("Idempotency-Key")
    if key is None:
        raise Problem(
            400,
            "idempotency_key_required",
            "This operation requires an Idempotency-Key header.",
        )
    if not (16 <= len(key) <= 128) or not all(33 <= ord(c) <= 126 for c in key):
        raise Problem(
            422,
            "validation_failed",
            "Idempotency-Key must be 16-128 visible ASCII characters.",
            errors=[
                {
                    "pointer": "/headers/idempotency-key",
                    "code": "invalid",
                    "message": "Must be 16-128 visible ASCII characters.",
                }
            ],
        )
    return key


def request_fingerprint(route_params: dict[str, Any], body: dict[str, Any] | None) -> str:
    """Semantic fingerprint over route parameters and normalized body."""
    raw = json.dumps(
        {"params": route_params, "body": body or {}},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class IdempotencyOutcome:
    """Either a fresh reservation (record to fill in) or a replayed job."""

    def __init__(
        self, record: IdempotencyRecord | None = None, replayed_job: IngestionJob | None = None
    ) -> None:
        self.record = record
        self.replayed_job = replayed_job


async def reserve_idempotency(
    session: AsyncSession,
    *,
    method: str,
    route_template: str,
    key: str,
    fingerprint: str,
) -> IdempotencyOutcome:
    """Atomically reserve the key or classify the replay (contract sec. 6.1)."""
    scope = f"{method}:{route_template}:{key}"
    record = IdempotencyRecord(scope=scope, fingerprint=fingerprint)
    nested = await session.begin_nested()
    try:
        session.add(record)
        await session.flush()
        await nested.commit()
        return IdempotencyOutcome(record=record)
    except IntegrityError:
        await nested.rollback()
    existing = await session.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.scope == scope)
    )
    if existing is None:  # reservation raced a delete; extremely unlikely
        raise Problem(
            409,
            "idempotency_in_progress",
            "A request with this idempotency key is being processed.",
            retryable=True,
        )
    if existing.fingerprint != fingerprint:
        raise Problem(
            409,
            "idempotency_conflict",
            "This idempotency key was already used for a different request.",
        )
    if existing.job_id is None:
        raise Problem(
            409,
            "idempotency_in_progress",
            "A request with this idempotency key is being processed.",
            retryable=True,
        )
    job = await session.get(IngestionJob, existing.job_id)
    if job is None:
        raise Problem(404, "not_found", "The original job no longer exists.")
    return IdempotencyOutcome(replayed_job=job)


def parse_uuid(value: str, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise Problem(404, "not_found", f"No such {what}.") from exc
