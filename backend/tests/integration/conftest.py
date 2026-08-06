"""Integration fixtures: a real PostgreSQL (`docman_test`) migrated by Alembic.

Connection resolution order:
1. ``DOCMAN_TEST_DATABASE_URL`` (full SQLAlchemy URL), else
2. the compose PostgreSQL published on 127.0.0.1:5432, using the password from
   the repository ``.env`` (dev fallback ``docman``).

If PostgreSQL is unreachable, every test here skips — they never run against
production data and always use the dedicated ``docman_test`` database.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
_TEST_DB = "docman_test"


def _password_from_env_file() -> str:
    env_file = _REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DOCMAN_POSTGRES_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return "docman"


def _test_database_url() -> str:
    override = os.environ.get("DOCMAN_TEST_DATABASE_URL")
    if override:
        return override
    password = _password_from_env_file()
    return f"postgresql+psycopg://docman:{password}@127.0.0.1:5432/{_TEST_DB}"


def _postgres_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=1.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not _postgres_reachable():
        pytest.skip("PostgreSQL is not reachable on 127.0.0.1:5432")
    url = _test_database_url()

    # (Re)create the dedicated test database, then migrate to head. Alembic and
    # admin operations use the sync psycopg driver.
    admin_dsn = (
        url.replace("+psycopg", "")
        .replace(f"/{_TEST_DB}", "/postgres")
        .replace("postgresql://", "")
    )
    with psycopg.connect(f"postgresql://{admin_dsn}", autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {_TEST_DB} (FORCE)")
        conn.execute(f"CREATE DATABASE {_TEST_DB}")

    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    os.environ["DOCMAN_DATABASE_URL"] = url
    from doc_manager.core.config import get_settings

    get_settings.cache_clear()
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def db_engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_url, poolclass=None)
    # Isolate tests: wipe all ingestion tables between tests.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE scan_observations, job_events, job_checkpoints,"
                " ingestion_job_attempts, idempotency_records, ingestion_jobs,"
                " duplicate_members, duplicate_groups,"
                " chunks, file_versions, content_objects, catalog_entries,"
                " source_locations, scheduler_state CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


# --- Deterministic offline embedding + in-memory Qdrant --------------------
# Phase 4 indexing/search go through FastEmbed + Qdrant. Tests must not download a
# model or need a server, so a fake embedder (stable hash-derived vectors) and an
# in-memory Qdrant client stand in. Query and passage share the derivation so a
# query embeds close to the chunk that contains its words.

_FAKE_DIM = 8


class FakeEmbeddingService:
    """Deterministic bag-of-words embedder over a tiny fixed vocabulary."""

    def __init__(self) -> None:
        from doc_manager.embedding.profile import EmbeddingProfile

        self.profile = EmbeddingProfile(model_name="fake/test", vector_size=_FAKE_DIM)

    @staticmethod
    def _vector(text: str) -> list[float]:
        import hashlib
        import math

        vec = [0.0] * _FAKE_DIM
        for token in text.lower().split():
            slot = hashlib.sha256(token.encode()).digest()[0] % _FAKE_DIM
            vec[slot] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@dataclass
class VectorEnv:
    client: object  # AsyncQdrantClient
    embedding: FakeEmbeddingService

    def repository(self, settings: object) -> object:
        from doc_manager.vectors import QdrantRepository

        collection = self.embedding.profile.collection_name(settings.qdrant_collection)  # type: ignore[attr-defined]
        return QdrantRepository(self.client, collection=collection)


@pytest.fixture
def vector_env(monkeypatch: pytest.MonkeyPatch) -> VectorEnv:
    """Patch index_file's embedding + Qdrant builders to offline fakes.

    Returns the shared in-memory client so a test can inspect points, and the fake
    embedder so a test can embed the same way the retrieval layer does.
    """
    from qdrant_client import AsyncQdrantClient

    env = VectorEnv(client=AsyncQdrantClient(location=":memory:"), embedding=FakeEmbeddingService())
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.index_file.build_embedding_service",
        lambda settings: env.embedding,
    )
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.index_file.build_qdrant_repository",
        lambda settings, profile: env.repository(settings),
    )
    return env
