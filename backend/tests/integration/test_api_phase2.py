"""API contract tests for locations and jobs (envelopes, ETag, idempotency)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from doc_manager.core.config import Settings
from doc_manager.main import create_app

pytestmark = pytest.mark.usefixtures("pg_url")


@pytest.fixture
def client(pg_url: str, tmp_path: Path, db_engine: object) -> Iterator[TestClient]:
    # db_engine is requested only for its table-truncation side effect.
    settings = Settings(
        env="test",
        database_url=pg_url,
        allowed_source_roots=str(tmp_path),
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def make_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "readme.md").write_text("hi")
    return root


def location_body(root: Path, name: str = "contracts") -> dict[str, object]:
    return {"name": name, "scan_root": str(root)}


def idem() -> str:
    return uuid.uuid4().hex  # 32 visible ASCII chars


def test_create_get_list_location(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "contracts")
    resp = client.post("/api/v1/locations", json=location_body(root))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["meta"]["api_version"] == "1"
    assert "request_id" in body["meta"]
    loc = body["data"]
    assert loc["display_root"] == str(root)
    assert loc["revision"] == 1
    assert resp.headers["Location"] == f"/api/v1/locations/{loc['id']}"
    etag = resp.headers["ETag"]
    assert etag == f'"location-{loc["id"]}-1"'

    got = client.get(f"/api/v1/locations/{loc['id']}")
    assert got.status_code == 200
    assert got.headers["ETag"] == etag
    not_modified = client.get(f"/api/v1/locations/{loc['id']}", headers={"If-None-Match": etag})
    assert not_modified.status_code == 304

    listing = client.get("/api/v1/locations")
    assert listing.status_code == 200
    page = listing.json()
    assert [entry["id"] for entry in page["data"]] == [loc["id"]]
    assert page["page"] == {"limit": 50, "has_more": False, "next_cursor": None}
    assert page["meta"]["effective_sort"] == ["-updated_at", "id"]


def test_create_rejects_unknown_fields_and_bad_roots(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "a")
    bad = client.post("/api/v1/locations", json={**location_body(root), "surprise": True})
    assert bad.status_code == 422
    problem = bad.json()
    assert problem["code"] == "validation_failed"
    assert bad.headers["content-type"].startswith("application/problem+json")

    outside = client.post(
        "/api/v1/locations", json={"name": "x", "scan_root": "/definitely/elsewhere"}
    )
    assert outside.status_code == 422


def test_overlapping_roots_conflict(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "parent")
    assert client.post("/api/v1/locations", json=location_body(root)).status_code == 201
    child = root / "sub"
    child.mkdir()
    resp = client.post("/api/v1/locations", json={"name": "child", "scan_root": str(child)})
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def test_patch_requires_and_checks_if_match(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "patchme")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    url = f"/api/v1/locations/{created['id']}"
    headers = {"Content-Type": "application/merge-patch+json"}

    missing = client.patch(url, content=b'{"name": "renamed"}', headers=headers)
    assert missing.status_code == 428
    assert missing.json()["code"] == "precondition_required"

    stale = client.patch(
        url,
        content=b'{"name": "renamed"}',
        headers={**headers, "If-Match": f'"location-{created["id"]}-99"'},
    )
    assert stale.status_code == 412
    assert stale.json()["current_etag"] == f'"location-{created["id"]}-1"'

    good = client.patch(
        url,
        content=b'{"name": "renamed", "scan_interval_minutes": 30}',
        headers={**headers, "If-Match": f'"location-{created["id"]}-1"'},
    )
    assert good.status_code == 200, good.text
    updated = good.json()["data"]
    assert updated["name"] == "renamed"
    assert updated["scan_interval_minutes"] == 30
    assert updated["revision"] == 2
    assert good.headers["ETag"] == f'"location-{created["id"]}-2"'

    wrong_type = client.patch(
        url,
        json={"name": "zzz"},  # sends application/json, not merge-patch
        headers={"If-Match": good.headers["ETag"]},
    )
    assert wrong_type.status_code == 415


def test_delete_location_requires_if_match_and_cascades(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "deleteme")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    url = f"/api/v1/locations/{created['id']}"
    etag = f'"location-{created["id"]}-1"'

    missing = client.delete(url)
    assert missing.status_code == 428
    assert missing.json()["code"] == "precondition_required"

    stale = client.delete(url, headers={"If-Match": f'"location-{created["id"]}-99"'})
    assert stale.status_code == 412

    gone = client.delete(url, headers={"If-Match": etag})
    assert gone.status_code == 204
    assert client.get(url).status_code == 404
    # The scan_root frees up for reuse once the location is deleted.
    reused = client.post("/api/v1/locations", json=location_body(root, name="reused"))
    assert reused.status_code == 201


def test_delete_location_blocked_by_open_scan(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "busy")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    scan = client.post(
        f"/api/v1/locations/{created['id']}/scan", headers={"Idempotency-Key": idem()}
    )
    assert scan.status_code == 202

    blocked = client.delete(
        f"/api/v1/locations/{created['id']}",
        headers={"If-Match": f'"location-{created["id"]}-1"'},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "conflict"


def test_location_test_endpoint(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "probe")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    resp = client.post(f"/api/v1/locations/{created['id']}/test")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["checks"][0] == {"name": "scan_root_exists", "ok": True, "detail": ""}


def test_scan_requires_idempotency_key(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "scanme")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    resp = client.post(f"/api/v1/locations/{created['id']}/scan")
    assert resp.status_code == 400
    assert resp.json()["code"] == "idempotency_key_required"


def test_scan_idempotent_replay_and_coalescing(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "scans")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    url = f"/api/v1/locations/{created['id']}/scan"
    key = idem()

    first = client.post(url, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    job = first.json()["data"]
    assert job["job_type"] == "scan_location"
    assert job["status"] == "queued"
    assert first.headers["Location"] == f"/api/v1/jobs/{job['id']}"
    assert first.json()["meta"]["idempotency_replayed"] is False

    replay = client.post(url, headers={"Idempotency-Key": key})
    assert replay.status_code == 202
    assert replay.json()["data"]["id"] == job["id"]
    assert replay.json()["meta"]["idempotency_replayed"] is True
    assert replay.headers["Idempotency-Replayed"] == "true"

    # New key, same location: domain coalescing onto the open scan job.
    coalesced = client.post(url, headers={"Idempotency-Key": idem()})
    assert coalesced.status_code == 202
    assert coalesced.json()["data"]["id"] == job["id"]
    assert coalesced.json()["meta"]["coalesced"] is True

    # Same key reused for a different location: conflict.
    other_root = make_root(tmp_path, "other")
    other = client.post(
        "/api/v1/locations", json={"name": "other", "scan_root": str(other_root)}
    ).json()["data"]
    conflict = client.post(
        f"/api/v1/locations/{other['id']}/scan", headers={"Idempotency-Key": key}
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_jobs_list_get_cancel(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "jobsflow")
    created = client.post("/api/v1/locations", json=location_body(root)).json()["data"]
    job = client.post(
        f"/api/v1/locations/{created['id']}/scan", headers={"Idempotency-Key": idem()}
    ).json()["data"]

    listing = client.get("/api/v1/jobs?filter[status]=queued")
    assert listing.status_code == 200
    assert [j["id"] for j in listing.json()["data"]] == [job["id"]]

    bad_filter = client.get("/api/v1/jobs?filter[status]=nonsense")
    assert bad_filter.status_code == 422

    got = client.get(f"/api/v1/jobs/{job['id']}")
    assert got.status_code == 200
    body = got.json()["data"]
    assert body["target"] == {
        "resource_type": "source_location",
        "resource_id": created["id"],
    }
    assert body["error"] is None
    # Lease internals are never public (contract sec. 6.2).
    assert "lease_owner" not in body and "lease_token" not in body

    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"

    # Terminal now (queued -> cancelled): a repeat cancel returns the state,
    # and retry becomes available.
    repeat = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert repeat.status_code == 200
    assert repeat.json()["data"]["status"] == "cancelled"

    retried = client.post(f"/api/v1/jobs/{job['id']}/retry", headers={"Idempotency-Key": idem()})
    assert retried.status_code == 202
    child = retried.json()["data"]
    assert child["retry_of_job_id"] == job["id"]
    assert child["status"] == "queued"

    # Retrying an open (queued) job is rejected.
    not_retryable = client.post(
        f"/api/v1/jobs/{child['id']}/retry", headers={"Idempotency-Key": idem()}
    )
    assert not_retryable.status_code == 409
    assert not_retryable.json()["code"] == "job_not_retryable"


def test_request_id_header_contract(client: TestClient) -> None:
    ok = client.get("/api/v1/jobs")
    assert ok.status_code == 200
    assert ok.headers["Docman-Api-Version"] == "1"
    rid = ok.headers["X-Request-ID"]
    assert ok.json()["meta"]["request_id"] == rid

    supplied = str(uuid.uuid4())
    echoed = client.get("/api/v1/jobs", headers={"X-Request-ID": supplied})
    assert echoed.headers["X-Request-ID"] == supplied

    bad = client.get("/api/v1/jobs", headers={"X-Request-ID": "not-a-uuid"})
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_request_id"
    assert bad.headers["X-Request-ID"] != "not-a-uuid"
