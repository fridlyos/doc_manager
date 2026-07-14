"""Versioned API router.

Phase 1 exposes only system status. Locations, documents, search, ask, jobs,
duplicates, and sync-plan routes are added in later phases (TECHSTACK section 8).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from doc_manager import __version__
from doc_manager.core.config import get_settings
from doc_manager.health import build_readiness

router = APIRouter(prefix="/api/v1")


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, Any]:
    settings = get_settings()
    report = await build_readiness(settings)
    return {
        "version": __version__,
        "environment": settings.env.value,
        "generation_provider": settings.generation_provider.value,
        "external_llm_enabled": settings.external_llm_enabled,
        "ready": report.ready,
        "search_only": report.search_only,
        "components": [
            {"name": c.name, "required": c.required, "status": c.status.value}
            for c in report.components
        ],
    }
