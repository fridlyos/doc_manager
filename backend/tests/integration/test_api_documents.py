"""API contract tests for documents, the error queue, and manual reindex (3.e).

Seeds catalog entries + file versions + content objects directly (a real
scan/index is covered by test_index_file), then exercises the read/list/detail
projections, per-document error isolation, and the reindex job-creation path
including idempotent replay.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from doc_manager.core.config import Settings
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    FileVersion,
    SourceLocation,
)
from doc_manager.domain.enums import CatalogEntryState, ExtractionStatus
from doc_manager.main import create_app

pytestmark = pytest.mark.usefixtures("pg_url")

_TRUNCATE = (
    "TRUNCATE scan_observations, job_events, job_checkpoints,"
    " ingestion_job_attempts, idempotency_records, ingestion_jobs,"
    " file_versions, content_objects, catalog_entries,"
    " source_locations, scheduler_state CASCADE"
)


@pytest.fixture
def sync_engine(pg_url: str) -> Iterator[object]:
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text(_TRUNCATE))
    yield engine
    engine.dispose()


@pytest.fixture
def client(pg_url: str, tmp_path: Path, sync_engine: object) -> Iterator[TestClient]:
    settings = Settings(
        env="test",
        database_url=pg_url,
        allowed_source_roots=str(tmp_path),
        artifact_root=tmp_path / "artifacts",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def idem() -> str:
    return uuid.uuid4().hex


def _seed(sync_engine: object, tmp_path: Path) -> dict[str, str]:
    """One indexed, one failed, and one unsupported document under one location."""
    now = datetime.now(UTC)
    with Session(sync_engine) as s:  # type: ignore[arg-type]
        loc = SourceLocation(
            name="docs",
            scan_root=str(tmp_path),
            display_root=str(tmp_path),
        )
        s.add(loc)
        s.flush()

        content = ContentObject(
            text_hash="a" * 64,
            structure_hash="b" * 64,
            extractor_name="text",
            extractor_version="1",
            extraction_profile_hash="c" * 64,
            normalization_version="1",
            artifact_path="1/bb/bb.cccccccccccc.json.gz",
            page_count=2,
            character_count=120,
        )
        s.add(content)
        s.flush()

        indexed = CatalogEntry(
            source_location_id=loc.id,
            relative_path="sub/notes.md",
            file_name="notes.md",
            extension="md",
            state=CatalogEntryState.indexed.value,
            sha256="d" * 64,
            last_observed_size_bytes=120,
            last_observed_mtime=now,
        )
        failed = CatalogEntry(
            source_location_id=loc.id,
            relative_path="locked.pdf",
            file_name="locked.pdf",
            extension="pdf",
            state=CatalogEntryState.failed.value,
            sha256="e" * 64,
            last_observed_size_bytes=2048,
            last_observed_mtime=now,
        )
        unsupported = CatalogEntry(
            source_location_id=loc.id,
            relative_path="data.xml",
            file_name="data.xml",
            extension="xml",
            state=CatalogEntryState.unsupported.value,
            sha256="f" * 64,
            last_observed_size_bytes=64,
            last_observed_mtime=now,
        )
        s.add_all([indexed, failed, unsupported])
        s.flush()

        vi = FileVersion(
            catalog_entry_id=indexed.id,
            size_bytes=120,
            mtime=now,
            sha256="d" * 64,
            content_object_id=content.id,
            extraction_status=ExtractionStatus.extracted.value,
            indexed_at=now,
        )
        vf = FileVersion(
            catalog_entry_id=failed.id,
            size_bytes=2048,
            mtime=now,
            sha256="e" * 64,
            extraction_status=ExtractionStatus.failed.value,
            error_code="encrypted",
            error_message="the pdf is password protected",
        )
        vu = FileVersion(
            catalog_entry_id=unsupported.id,
            size_bytes=64,
            mtime=now,
            sha256="f" * 64,
            extraction_status=ExtractionStatus.unsupported.value,
        )
        s.add_all([vi, vf, vu])
        s.flush()
        indexed.current_file_version_id = vi.id
        failed.current_file_version_id = vf.id
        unsupported.current_file_version_id = vu.id
        s.commit()
        return {
            "location": str(loc.id),
            "indexed": str(indexed.id),
            "failed": str(failed.id),
            "unsupported": str(unsupported.id),
        }


def test_list_documents_projects_all_states(
    client: TestClient, sync_engine: object, tmp_path: Path
) -> None:
    ids = _seed(sync_engine, tmp_path)
    resp = client.get("/api/v1/documents")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["api_version"] == "1"
    assert body["meta"]["effective_sort"] == ["-updated_at", "id"]
    data = {d["id"]: d for d in body["data"]}
    assert set(data) == {ids["indexed"], ids["failed"], ids["unsupported"]}

    indexed = data[ids["indexed"]]
    # display_path joins display_root with the stored posix relative path.
    assert indexed["display_path"] == str(tmp_path / "sub" / "notes.md")
    assert indexed["state"] == "indexed"
    assert indexed["extraction_status"] == "extracted"
    assert indexed["error"] is None
    assert indexed["content_object"]["page_count"] == 2
    assert indexed["content_object"]["character_count"] == 120

    failed = data[ids["failed"]]
    assert failed["state"] == "failed"
    assert failed["error"] == {
        "code": "encrypted",
        "message": "the pdf is password protected",
    }
    assert failed["content_object"] is None


def test_filter_by_state_and_extension(
    client: TestClient, sync_engine: object, tmp_path: Path
) -> None:
    ids = _seed(sync_engine, tmp_path)
    resp = client.get("/api/v1/documents?filter[state]=failed")
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()["data"]] == [ids["failed"]]

    resp = client.get("/api/v1/documents?filter[extension]=md")
    assert [d["id"] for d in resp.json()["data"]] == [ids["indexed"]]

    resp = client.get("/api/v1/documents?filter[state]=bogus")
    assert resp.status_code == 422


def test_get_document_detail(client: TestClient, sync_engine: object, tmp_path: Path) -> None:
    ids = _seed(sync_engine, tmp_path)
    resp = client.get(f"/api/v1/documents/{ids['failed']}")
    assert resp.status_code == 200
    doc = resp.json()["data"]
    assert doc["file_name"] == "locked.pdf"
    assert doc["error"]["code"] == "encrypted"

    missing = client.get(f"/api/v1/documents/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_error_queue_lists_only_failed(
    client: TestClient, sync_engine: object, tmp_path: Path
) -> None:
    ids = _seed(sync_engine, tmp_path)
    resp = client.get("/api/v1/errors")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [d["id"] for d in data] == [ids["failed"]]
    assert data[0]["error"]["code"] == "encrypted"


def test_reindex_creates_job_and_replays(
    client: TestClient, sync_engine: object, tmp_path: Path
) -> None:
    ids = _seed(sync_engine, tmp_path)
    key = idem()
    resp = client.post(
        f"/api/v1/documents/{ids['failed']}/reindex",
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["job_type"] == "index_file"
    assert job["target"] == {"resource_type": "catalog_entry", "resource_id": ids["failed"]}
    assert resp.headers["Location"] == f"/api/v1/jobs/{job['id']}"

    replay = client.post(
        f"/api/v1/documents/{ids['failed']}/reindex",
        headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 202
    assert replay.json()["data"]["id"] == job["id"]
    assert replay.json()["meta"]["idempotency_replayed"] is True


def test_reindex_requires_idempotency_key(
    client: TestClient, sync_engine: object, tmp_path: Path
) -> None:
    ids = _seed(sync_engine, tmp_path)
    resp = client.post(f"/api/v1/documents/{ids['indexed']}/reindex")
    assert resp.status_code == 400
    assert resp.json()["code"] == "idempotency_key_required"
