from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from doc_manager.core.config import Settings
from doc_manager.main import create_app


async def test_liveness() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}


async def test_frontend_browser_routes_use_spa_fallback(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main>doc manager</main>", encoding="utf-8")
    app = create_app(Settings(_env_file=None, frontend_dist=tmp_path))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/locations")
        assert resp.status_code == 200
        assert "doc manager" in resp.text

        api_miss = await client.get("/api/not-a-route")
        assert api_miss.status_code == 404
