"""Duplicates + coverage endpoints (Phase 6.c). Seeds report rows directly."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from doc_manager.core.config import Settings
from doc_manager.db.models import (
    CatalogEntry,
    DuplicateGroup,
    DuplicateMember,
    SourceLocation,
)
from doc_manager.domain.enums import CatalogEntryState
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


def _seed(sync_engine: object) -> dict[str, str]:
    now = datetime.now(UTC)
    with Session(sync_engine) as s:  # type: ignore[arg-type]
        loc = SourceLocation(name="docs", scan_root="/sources", display_root="/sources")
        s.add(loc)
        s.flush()
        # Catalog entries: 2 drive coverage counts (indexed + failed); members
        # reference real entry ids (FK).
        entries = []
        for i, state in enumerate(
            [CatalogEntryState.indexed, CatalogEntryState.failed, CatalogEntryState.indexed]
        ):
            entry = CatalogEntry(
                source_location_id=loc.id,
                relative_path=f"f{i}.txt",
                file_name=f"f{i}.txt",
                extension="txt",
                state=state.value,
                sha256=f"{i}" * 64,
                last_observed_mtime=now,
            )
            s.add(entry)
            entries.append(entry)
        s.flush()
        exact = DuplicateGroup(kind="exact", group_hash="a" * 64, member_count=2)
        text_group = DuplicateGroup(kind="text", group_hash="b" * 64, member_count=2)
        s.add_all([exact, text_group])
        s.flush()
        for entry, name in ((entries[0], "one"), (entries[1], "two")):
            s.add(
                DuplicateMember(
                    group_id=exact.id,
                    catalog_entry_id=entry.id,
                    source_location_id=loc.id,
                    display_path=f"/sources/{name}.pdf",
                    state="indexed",
                    sha256="a" * 64,
                )
            )
        s.add(
            DuplicateMember(
                group_id=text_group.id,
                catalog_entry_id=entries[2].id,
                source_location_id=loc.id,
                display_path="/sources/reflow.pdf",
                state="indexed",
                sha256="c" * 64,
            )
        )
        s.commit()
        return {"location": str(loc.id), "exact": str(exact.id), "text": str(text_group.id)}


def test_list_duplicates_and_filter(client: TestClient, sync_engine: object) -> None:
    ids = _seed(sync_engine)
    body = client.get("/api/v1/duplicates").json()
    kinds = {g["kind"] for g in body["data"]}
    assert kinds == {"exact", "text"}
    assert body["meta"]["effective_sort"] == ["-member_count", "id"]

    only_text = client.get("/api/v1/duplicates?filter[kind]=text").json()["data"]
    assert [g["id"] for g in only_text] == [ids["text"]]


def test_duplicate_group_detail(client: TestClient, sync_engine: object) -> None:
    ids = _seed(sync_engine)
    doc = client.get(f"/api/v1/duplicates/{ids['exact']}").json()["data"]
    assert doc["kind"] == "exact"
    paths = {m["display_path"] for m in doc["members"]}
    assert paths == {"/sources/one.pdf", "/sources/two.pdf"}

    assert client.get(f"/api/v1/duplicates/{uuid.uuid4()}").status_code == 404


def test_coverage_counts_by_state(client: TestClient, sync_engine: object) -> None:
    ids = _seed(sync_engine)
    data = client.get("/api/v1/coverage").json()["data"]
    entry = next(loc for loc in data if loc["source_location_id"] == ids["location"])
    assert entry["total"] == 3
    assert entry["by_state"]["indexed"] == 2
    assert entry["by_state"]["failed"] == 1


def test_rebuild_enqueues_durable_job(client: TestClient) -> None:
    resp = client.post("/api/v1/duplicates/rebuild", headers={"Idempotency-Key": idem()})
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["job_type"] == "build_duplicate_report"


def test_rebuild_requires_idempotency_key(client: TestClient) -> None:
    assert client.post("/api/v1/duplicates/rebuild").status_code == 400
