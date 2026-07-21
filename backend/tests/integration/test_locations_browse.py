"""Directory-browse endpoint: allowed-root listing, traversal safety, symlinks."""

from __future__ import annotations

import os
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


def test_browse_lists_allowed_roots_when_no_path(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/api/v1/locations/browse")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["path"] is None
    assert data["parent"] is None
    assert data["entries"] == [{"name": str(tmp_path), "path": str(tmp_path), "kind": "dir"}]


def test_browse_lists_directory_contents(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "contracts")
    (root / "sub").mkdir()
    resp = client.get("/api/v1/locations/browse", params={"path": str(root)})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["path"] == str(root)
    # root is a child of the allowed root (tmp_path), so it has a parent.
    assert data["parent"] == str(tmp_path)
    # Directories sort before files.
    assert data["entries"] == [
        {"name": "sub", "path": str(root / "sub"), "kind": "dir"},
        {"name": "readme.md", "path": str(root / "readme.md"), "kind": "file"},
    ]


def test_browse_at_allowed_root_has_no_parent(client: TestClient, tmp_path: Path) -> None:
    make_root(tmp_path, "child")
    resp = client.get("/api/v1/locations/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["path"] == str(tmp_path)
    assert data["parent"] is None


def test_browse_descends_and_reports_parent(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "deep")
    (root / "sub").mkdir()
    resp = client.get("/api/v1/locations/browse", params={"path": str(root / "sub")})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["parent"] == str(root)


def test_browse_rejects_path_outside_allowed_root(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere"
    resp = client.get("/api/v1/locations/browse", params={"path": str(outside)})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


def test_browse_rejects_traversal(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/api/v1/locations/browse", params={"path": f"{tmp_path}/../etc"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


def test_browse_rejects_missing_path(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/api/v1/locations/browse", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_browse_ignores_mismatched_client_path_style(client: TestClient, tmp_path: Path) -> None:
    # A posix container mount must browse even if the client sends a Windows
    # path_style (e.g. from a windows filesystem profile). Regression: /hostfs
    # under path_style=mapped_drive raised "must be an absolute path".
    root = make_root(tmp_path, "hostfs-like")
    resp = client.get(
        "/api/v1/locations/browse",
        params={"path": str(root), "path_style": "mapped_drive"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["path"] == str(root)
    assert data["path_style"] == "linux"


def test_capabilities_reports_profile_and_picker(client: TestClient) -> None:
    resp = client.get("/api/v1/locations/capabilities")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["filesystem_profile"] in {"windows", "unix"}
    assert isinstance(data["native_picker_available"], bool)


def test_pick_folder_unavailable_falls_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "doc_manager.api.v1.routes.locations.native_picker_available", lambda: False
    )
    resp = client.post("/api/v1/locations/pick-folder")
    assert resp.status_code == 422
    assert resp.json()["code"] == "native_picker_unavailable"


def test_pick_folder_returns_selected_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("doc_manager.api.v1.routes.locations.native_picker_available", lambda: True)
    monkeypatch.setattr(
        "doc_manager.api.v1.routes.locations.pick_folder_native", lambda: "Z:\\Docs\\Reports"
    )
    resp = client.post("/api/v1/locations/pick-folder")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data == {"path": "Z:\\Docs\\Reports", "path_style": "mapped_drive"}


def test_pick_folder_cancelled_returns_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("doc_manager.api.v1.routes.locations.native_picker_available", lambda: True)
    monkeypatch.setattr("doc_manager.api.v1.routes.locations.pick_folder_native", lambda: None)
    resp = client.post("/api/v1/locations/pick-folder")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"path": None, "path_style": None}


def test_browse_skips_symlinks(client: TestClient, tmp_path: Path) -> None:
    root = make_root(tmp_path, "linky")
    (root / "real").mkdir()
    try:
        os.symlink(root / "real", root / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    resp = client.get("/api/v1/locations/browse", params={"path": str(root)})
    assert resp.status_code == 200, resp.text
    names = {e["name"] for e in resp.json()["data"]["entries"]}
    assert "real" in names
    assert "link" not in names
