"""Process-level health endpoints (unversioned, used by Docker health checks)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from doc_manager.core.config import get_settings
from doc_manager.health import build_readiness

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Liveness: the process is up and serving. No dependency checks."""
    return {"status": "alive"}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness: required dependencies are up. Optional providers may be down."""
    settings = get_settings()
    report = await build_readiness(settings)
    if not report.ready:
        response.status_code = 503
    return {
        "ready": report.ready,
        "search_only": report.search_only,
        "components": [
            {
                "name": c.name,
                "required": c.required,
                "status": c.status.value,
                "detail": c.detail,
            }
            for c in report.components
        ],
    }
