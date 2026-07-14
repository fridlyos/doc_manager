from __future__ import annotations

from fastapi.testclient import TestClient

from doc_manager.main import create_app


def test_liveness() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}
