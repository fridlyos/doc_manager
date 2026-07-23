"""SQL↔vector consistency scan (Phase 4.e).

Seeds a content object + chunk rows and matching Qdrant points, then checks that
``scan_consistency`` reports clean, detects a missing point, and detects an orphan
point. Uses real PostgreSQL + an in-memory Qdrant client.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doc_manager.chunking.chunker import Chunk as ChunkRecord
from doc_manager.chunking.profile import chunk_id
from doc_manager.db.models import Chunk, ContentObject
from doc_manager.embedding.profile import EmbeddingProfile
from doc_manager.jobs.handlers.consistency import ConsistencyReport, scan_consistency
from doc_manager.vectors import QdrantRepository, build_point, point_id

pytestmark = pytest.mark.usefixtures("pg_url")

_CP = "cp-hash"
_EP = "ep-hash"


def test_report_accounts_missing_and_orphans() -> None:
    report = ConsistencyReport()
    report.account(uuid.uuid4(), expected={"a", "b"}, actual={"a", "b"})
    assert report.clean
    report.account(uuid.uuid4(), expected={"a", "b"}, actual={"a"})  # missing b
    report.account(uuid.uuid4(), expected={"a"}, actual={"a", "x"})  # orphan x
    assert not report.clean
    assert report.missing_points == 1
    assert report.orphan_points == 1
    assert report.content_objects_checked == 3
    assert len(report.drifted_content_ids) == 2


async def _seed_content(session: AsyncSession) -> uuid.UUID:
    content = ContentObject(
        text_hash="t" * 64,
        structure_hash="s" * 64,
        extractor_name="text",
        extractor_version="1",
        extraction_profile_hash="e" * 64,
        normalization_version="norm-1",
        artifact_path="x.json.gz",
        page_count=1,
        character_count=10,
    )
    session.add(content)
    await session.flush()
    for i in range(3):
        session.add(
            Chunk(
                id=chunk_id(content.id, _CP, i),
                content_object_id=content.id,
                chunk_index=i,
                page_start=i + 1,
                page_end=i + 1,
                token_count=5,
                text_hash="h" * 64,
                chunking_profile_hash=_CP,
                embedding_profile_hash=_EP,
            )
        )
    await session.commit()
    return content.id


def _point(content_id: uuid.UUID, index: int) -> object:
    return build_point(
        content_object_id=content_id,
        chunk_id=chunk_id(content_id, _CP, index),
        chunk=ChunkRecord(
            index=index, text="t", token_count=5, page_start=1, page_end=1, text_hash="h" * 64
        ),
        vector=[1.0, 0.0, 0.0, 0.0],
        chunking_profile_hash=_CP,
        embedding_profile_hash=_EP,
    )


async def _repo() -> QdrantRepository:
    repo = QdrantRepository(AsyncQdrantClient(location=":memory:"), collection="chunks")
    await repo.ensure_collection(EmbeddingProfile(model_name="m", vector_size=4))
    return repo


async def test_scan_reports_clean_when_points_match(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        content_id = await _seed_content(session)
    repo = await _repo()
    await repo.upsert_points([_point(content_id, i) for i in range(3)])

    async with session_factory() as session:
        report = await scan_consistency(session, repo, _EP)
    assert report.clean
    assert report.content_objects_checked == 1
    assert report.chunks_expected == 3
    assert report.points_found == 3


async def test_scan_detects_missing_point(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        content_id = await _seed_content(session)
    repo = await _repo()
    await repo.upsert_points([_point(content_id, i) for i in range(2)])  # only 2 of 3

    async with session_factory() as session:
        report = await scan_consistency(session, repo, _EP)
    assert not report.clean
    assert report.missing_points == 1
    assert report.orphan_points == 0
    assert report.drifted_content_ids == [str(content_id)]


async def test_scan_detects_orphan_point(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        content_id = await _seed_content(session)
    repo = await _repo()
    points = [_point(content_id, i) for i in range(3)]
    # An orphan: a point whose chunk index has no SQL row.
    from doc_manager.vectors.point import VectorPoint

    orphan_id = point_id(content_id, _CP, _EP, 99)
    points.append(
        VectorPoint(
            id=orphan_id,
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"content_object_id": str(content_id), "embedding_profile_hash": _EP},
        )
    )
    await repo.upsert_points(points)

    async with session_factory() as session:
        report = await scan_consistency(session, repo, _EP)
    assert not report.clean
    assert report.orphan_points == 1
    assert report.missing_points == 0
