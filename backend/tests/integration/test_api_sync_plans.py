"""Sync-plan endpoints (Phase 7.c): create (durable + idempotent), get, items."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from doc_manager.core.config import Settings
from doc_manager.db.models import SourceLocation, SyncPlan, SyncPlanItem
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


def idem() -> str:
    return uuid.uuid4().hex


def _two_locations(sync_engine: object) -> tuple[str, str]:
    with Session(sync_engine) as s:  # type: ignore[arg-type]
        a = SourceLocation(name="src", scan_root="/sources/a", display_root="/sources/a")
        b = SourceLocation(name="tgt", scan_root="/sources/b", display_root="/sources/b")
        s.add_all([a, b])
        s.commit()
        return str(a.id), str(b.id)


def _seed_ready_plan(sync_engine: object, source: str, target: str) -> str:
    with Session(sync_engine) as s:  # type: ignore[arg-type]
        plan = SyncPlan(
            source_location_id=uuid.UUID(source),
            target_location_id=uuid.UUID(target),
            status="ready",
            item_count=2,
            covered_percent=50.0,
            summary_json={"copy": 1, "conflict": 1},
        )
        s.add(plan)
        s.flush()
        s.add_all(
            [
                SyncPlanItem(
                    plan_id=plan.id,
                    action="copy",
                    reason="missing_in_target",
                    source_relative_path="new.txt",
                    source_sha256="a" * 64,
                    source_text_hash="t" * 64,
                ),
                SyncPlanItem(
                    plan_id=plan.id,
                    action="conflict",
                    reason="path_hash_mismatch",
                    source_relative_path="clash.txt",
                    source_sha256="b" * 64,
                    source_text_hash="u" * 64,
                    target_relative_path="clash.txt",
                    target_sha256="c" * 64,
                ),
            ]
        )
        s.commit()
        return str(plan.id)


def test_create_sync_plan_is_durable_and_idempotent(
    client: TestClient, sync_engine: object
) -> None:
    source, target = _two_locations(sync_engine)
    key = idem()
    resp = client.post(
        "/api/v1/sync-plans",
        json={"source_location_id": source, "target_location_id": target},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 202, resp.text
    plan = resp.json()["data"]
    assert plan["status"] == "building"
    assert plan["source_location_id"] == source
    assert resp.headers["Location"] == f"/api/v1/sync-plans/{plan['id']}"

    replay = client.post(
        "/api/v1/sync-plans",
        json={"source_location_id": source, "target_location_id": target},
        headers={"Idempotency-Key": key},
    )
    assert replay.json()["data"]["id"] == plan["id"]
    assert replay.json()["meta"]["idempotency_replayed"] is True


def test_create_rejects_same_source_and_target(client: TestClient, sync_engine: object) -> None:
    source, _ = _two_locations(sync_engine)
    resp = client.post(
        "/api/v1/sync-plans",
        json={"source_location_id": source, "target_location_id": source},
        headers={"Idempotency-Key": idem()},
    )
    assert resp.status_code == 422


def test_create_unknown_location_is_404(client: TestClient, sync_engine: object) -> None:
    source, _ = _two_locations(sync_engine)
    resp = client.post(
        "/api/v1/sync-plans",
        json={"source_location_id": source, "target_location_id": str(uuid.uuid4())},
        headers={"Idempotency-Key": idem()},
    )
    assert resp.status_code == 404


def test_create_requires_idempotency_key(client: TestClient, sync_engine: object) -> None:
    source, target = _two_locations(sync_engine)
    resp = client.post(
        "/api/v1/sync-plans",
        json={"source_location_id": source, "target_location_id": target},
    )
    assert resp.status_code == 400


def test_get_plan_and_items_with_filter(client: TestClient, sync_engine: object) -> None:
    source, target = _two_locations(sync_engine)
    plan_id = _seed_ready_plan(sync_engine, source, target)

    plan = client.get(f"/api/v1/sync-plans/{plan_id}").json()["data"]
    assert plan["status"] == "ready"
    assert plan["covered_percent"] == 50.0

    items = client.get(f"/api/v1/sync-plans/{plan_id}/items").json()["data"]
    assert {i["action"] for i in items} == {"copy", "conflict"}

    conflicts = client.get(f"/api/v1/sync-plans/{plan_id}/items?filter[action]=conflict").json()[
        "data"
    ]
    assert [i["source_relative_path"] for i in conflicts] == ["clash.txt"]
    assert conflicts[0]["target_relative_path"] == "clash.txt"


def test_get_unknown_plan_is_404(client: TestClient) -> None:
    assert client.get(f"/api/v1/sync-plans/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/v1/sync-plans/{uuid.uuid4()}/items").status_code == 404


def test_no_execution_endpoint_exists(client: TestClient, sync_engine: object) -> None:
    source, target = _two_locations(sync_engine)
    plan_id = _seed_ready_plan(sync_engine, source, target)
    # There is intentionally no execute/apply route in the MVP.
    assert client.post(f"/api/v1/sync-plans/{plan_id}/execute").status_code in (404, 405)
    assert client.post(f"/api/v1/sync-plans/{plan_id}/apply").status_code in (404, 405)
