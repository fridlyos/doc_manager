"""Phase 8 DoD exit-criterion #3 — the restore drill.

Proves a full restore into empty PostgreSQL + Qdrant volumes:

    seed + index -> real backup (pg_dump) -> wipe both stores -> restore
    (pg_restore) -> rebuild vectors (reindex ?rebuild_vectors) -> consistency
    check is clean -> a known query returns the expected document.

Runs offline against the fake embedder + in-memory Qdrant, but needs the compose
PostgreSQL (skips otherwise, like the rest of the integration suite) and the
PG client tools (pg_dump/pg_restore/psql) on PATH.

The drill owns its own engines so it can DROP/CREATE ``docman_test`` without
fighting the function-scoped ``db_engine`` fixture, and it models the Qdrant wipe
with two in-memory clients (``client_a`` live, ``client_b`` the empty post-wipe
store). A hermetic teardown re-migrates ``docman_test`` so later tests are
unaffected even if the drill fails mid-way.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from doc_manager.backup.checksums import verify_sums
from doc_manager.backup.default_steps import DefaultSteps
from doc_manager.backup.runner import run_backup
from doc_manager.db.models import Chunk, SourceLocation
from doc_manager.domain.enums import JobOrigin, JobType
from doc_manager.embedding.profile import EmbeddingProfile
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import JobError
from doc_manager.jobs.handlers import HANDLERS
from doc_manager.jobs.handlers.consistency import scan_consistency
from doc_manager.restore import run_restore
from doc_manager.restore.default_steps import DefaultRestoreSteps
from doc_manager.retrieval import RetrievalService
from doc_manager.vectors import QdrantRepository

pytestmark = pytest.mark.usefixtures("pg_url")

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TEST_DB = "docman_test"
_FAKE_DIM = 8


class _FakeEmbedder:
    """Deterministic offline embedder (mirrors conftest's) — same instance is used
    for indexing, the rebuild, consistency, and search, so its profile hash is
    internally consistent regardless of the configured model."""

    def __init__(self) -> None:
        self.profile = EmbeddingProfile(model_name="fake/test", vector_size=_FAKE_DIM)

    @staticmethod
    def _vector(text: str) -> list[float]:
        import hashlib
        import math

        vec = [0.0] * _FAKE_DIM
        for token in text.lower().split():
            vec[hashlib.sha256(token.encode()).digest()[0] % _FAKE_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _admin_dsn(url: str) -> str:
    inner = (
        url.replace("+psycopg", "")
        .replace(f"/{_TEST_DB}", "/postgres")
        .replace("postgresql://", "")
    )
    return f"postgresql://{inner}"


def _recreate_empty_db(url: str) -> None:
    with psycopg.connect(_admin_dsn(url), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {_TEST_DB} (FORCE)")
        conn.execute(f"CREATE DATABASE {_TEST_DB}")


def _migrate(url: str) -> None:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    os.environ["DOCMAN_DATABASE_URL"] = url
    command.upgrade(config, "head")


@pytest.fixture
def drill_db(pg_url: str) -> Iterator[str]:
    """Yield the test URL; on teardown reset docman_test to a clean migrated state
    so the drill is hermetic regardless of pass/fail."""
    yield pg_url
    _recreate_empty_db(pg_url)
    _migrate(pg_url)


async def _run_one(engine: object, db_engine: AsyncEngine) -> str | None:
    async with db_engine.connect() as conn:
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            claim = await engine.claim_next(session, worker_id="w", lease_seconds=60)
            if claim.job is None:
                return None
            job = claim.job
            assert job.lease_token is not None
            ctx = JobContext(
                session=session,
                engine=engine,
                job=job,
                worker_id="w",
                lease_token=job.lease_token,
                lease_seconds=60,
            )
            try:
                await HANDLERS[JobType(job.job_type)](ctx)
            except JobError:
                pass
            finally:
                if job.job_type == JobType.scan_location.value and job.source_location_id:
                    await engine.release_scan_lock(session, job.source_location_id)
            return str(job.job_type)
        finally:
            await session.close()


async def _drain(engine: object, db_engine: AsyncEngine) -> None:
    while await _run_one(engine, db_engine) is not None:
        pass


async def _count_points(client: object, collection: str) -> int:
    if not await client.collection_exists(collection):  # type: ignore[attr-defined]
        return 0
    return (await client.count(collection)).count  # type: ignore[attr-defined]


async def test_restore_drill_into_empty_volumes(
    drill_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for tool in ("pg_dump", "pg_restore", "psql"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH (run inside the maintenance image)")

    from qdrant_client import AsyncQdrantClient

    from doc_manager.core.config import Settings
    from doc_manager.jobs.queue import JobEngine

    pg_url = drill_db
    embedder = _FakeEmbedder()
    client_a = AsyncQdrantClient(location=":memory:")
    client_b = AsyncQdrantClient(location=":memory:")
    current: dict[str, object] = {"client": client_a}
    settings = Settings(database_url=pg_url, artifact_root=tmp_path / "artifacts")
    collection = embedder.profile.collection_name(settings.qdrant_collection)

    def _repo(s: object) -> QdrantRepository:
        return QdrantRepository(current["client"], collection=collection)

    monkeypatch.setattr("doc_manager.jobs.handlers.index_file.get_settings", lambda: settings)
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.index_file.build_embedding_service", lambda s: embedder
    )
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.index_file.build_qdrant_repository", lambda s, p: _repo(s)
    )

    # --- A. seed + index: populate PostgreSQL + client_a ---------------------
    (tmp_path / "contract.txt").write_text("the renewal clause covers december terms")
    (tmp_path / "recipe.txt").write_text("mix flour sugar butter and bake slowly")
    engine = create_async_engine(pg_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    jobs = JobEngine()
    async with sf() as session:
        loc = SourceLocation(
            name=f"loc-{uuid.uuid4().hex[:8]}", scan_root=str(tmp_path), display_root=str(tmp_path)
        )
        session.add(loc)
        await session.commit()
        location_id = loc.id
    async with sf() as session:
        await jobs.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location_id)},
            origin=JobOrigin.api,
            source_location_id=location_id,
        )
        await session.commit()
    await _drain(jobs, engine)

    async with sf() as session:
        chunk_count = (await session.scalars(select(func.count()).select_from(Chunk))).one()
    assert chunk_count > 0
    assert await _count_points(client_a, collection) == chunk_count

    # --- B. real backup (real pg_dump/pg_dumpall against docman_test) --------
    backup_root = tmp_path / "backups"
    result = run_backup(
        staging_root=tmp_path / "staging",
        backup_root=backup_root,
        steps=DefaultSteps(settings),
        config_snapshot={"embedding_model": embedder.profile.model_name},
    )
    set_dir = backup_root / "completed" / result.backup_id
    assert (set_dir / "COMPLETED").is_file()
    assert verify_sums(set_dir).ok

    # --- C. wipe: empty PostgreSQL + empty Qdrant ----------------------------
    await engine.dispose()
    _recreate_empty_db(pg_url)  # truly empty: no schema, no alembic
    current["client"] = client_b

    # --- D. restore PostgreSQL (real psql globals + pg_restore) --------------
    restore_result = run_restore(
        backup_root=backup_root, backup_id=result.backup_id, steps=DefaultRestoreSteps(settings)
    )
    assert restore_result.restored_postgres

    # --- E. rebuild the vector index into the empty client_b -----------------
    engine2 = create_async_engine(pg_url)
    sf2 = async_sessionmaker(engine2, expire_on_commit=False)
    async with sf2() as session:
        await jobs.enqueue(
            session,
            job_type=JobType.reindex_all_for_profile,
            payload={"version": 1, "scope": "all", "rebuild_vectors": True},
            origin=JobOrigin.api,
            dedupe_key="reindex:all",
        )
        await session.commit()
    await _drain(jobs, engine2)

    # --- F. consistency gate -------------------------------------------------
    repo_b = QdrantRepository(client_b, collection=collection)
    async with sf2() as session:
        report = await scan_consistency(session, repo_b, embedder.profile.hash)
    assert report.clean, f"drift after restore+rebuild: {report}"
    assert report.chunks_expected == report.points_found == chunk_count

    # --- G. known-query search -----------------------------------------------
    service = RetrievalService(embedder, repo_b)
    async with sf2() as session:
        results = await service.search(
            session, query="the renewal clause covers december terms", top_k=5
        )
    assert results, "expected at least one hit after restore"
    assert results[0].paths[0].display_path.endswith("contract.txt")
    assert results[0].availability == "current"

    await engine2.dispose()
