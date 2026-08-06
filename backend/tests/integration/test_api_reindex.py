"""Reindex endpoints (Phase 6.a): location + system-wide, durable + idempotent."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from doc_manager.core.config import Settings
from doc_manager.db.models import SourceLocation
from doc_manager.main import create_app

pytestmark = pytest.mark.usefixtures("pg_url")

_TRUNCATE = (
    "TRUNCATE scan_observations, job_events, job_checkpoints,"
    " ingestion_job_attempts, idempotency_records, ingestion_jobs,"
    " sync_plan_items, sync_plans, duplicate_members, duplicate_groups,"
    " chunks, file_versions, content_objects, catalog_entries,"
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
def client(pg_url: str, sync_engine: object) -> Iterator[TestClient]:
    app = create_app(Settings(env="test", database_url=pg_url))
    with TestClient(app) as test_client:
        yield test_client


def _seed_location(sync_engine: object) -> str:
    with Session(sync_engine) as s:  # type: ignore[arg-type]
        loc = SourceLocation(name="docs", scan_root="/sources", display_root="/sources")
        s.add(loc)
        s.commit()
        return str(loc.id)


def idem() -> str:
    return uuid.uuid4().hex


def test_location_reindex_enqueues_durable_job(client: TestClient, sync_engine: object) -> None:
    location_id = _seed_location(sync_engine)
    key = idem()
    resp = client.post(f"/api/v1/locations/{location_id}/reindex", headers={"Idempotency-Key": key})
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["job_type"] == "reindex_all_for_profile"
    assert job["target"] == {"resource_type": "source_location", "resource_id": location_id}
    assert resp.headers["Location"] == f"/api/v1/jobs/{job['id']}"

    replay = client.post(
        f"/api/v1/locations/{location_id}/reindex", headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 202
    assert replay.json()["data"]["id"] == job["id"]
    assert replay.json()["meta"]["idempotency_replayed"] is True


def test_location_reindex_requires_idempotency_key(client: TestClient, sync_engine: object) -> None:
    location_id = _seed_location(sync_engine)
    resp = client.post(f"/api/v1/locations/{location_id}/reindex")
    assert resp.status_code == 400
    assert resp.json()["code"] == "idempotency_key_required"


def test_location_reindex_unknown_location_is_404(client: TestClient) -> None:
    resp = client.post(
        f"/api/v1/locations/{uuid.uuid4()}/reindex", headers={"Idempotency-Key": idem()}
    )
    assert resp.status_code == 404


def test_system_reindex_enqueues_durable_job(client: TestClient) -> None:
    resp = client.post("/api/v1/system/reindex", headers={"Idempotency-Key": idem()})
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["job_type"] == "reindex_all_for_profile"
    assert job["target"] is None
    assert resp.headers["Location"] == f"/api/v1/jobs/{job['id']}"


def test_system_reindex_requires_idempotency_key(client: TestClient) -> None:
    resp = client.post("/api/v1/system/reindex")
    assert resp.status_code == 400


def test_remove_stale_vectors_enqueues_durable_job(client: TestClient) -> None:
    key = idem()
    resp = client.post("/api/v1/system/remove-stale-vectors", headers={"Idempotency-Key": key})
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["job_type"] == "remove_stale_vectors"
    assert resp.headers["Location"] == f"/api/v1/jobs/{job['id']}"

    replay = client.post("/api/v1/system/remove-stale-vectors", headers={"Idempotency-Key": key})
    assert replay.json()["data"]["id"] == job["id"]
    assert replay.json()["meta"]["idempotency_replayed"] is True


def test_remove_stale_vectors_requires_idempotency_key(client: TestClient) -> None:
    resp = client.post("/api/v1/system/remove-stale-vectors")
    assert resp.status_code == 400
