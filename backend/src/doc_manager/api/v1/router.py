"""Versioned API router.

Phase 2 adds locations and jobs. Phase 3 adds documents and the error queue.
Search, ask, duplicates, and sync-plan routes arrive in later phases (TECHSTACK
section 8).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from doc_manager import __version__
from doc_manager.api.v1.routes.ask import providers_router
from doc_manager.api.v1.routes.ask import router as ask_router
from doc_manager.api.v1.routes.documents import errors_router
from doc_manager.api.v1.routes.documents import router as documents_router
from doc_manager.api.v1.routes.duplicates import coverage_router
from doc_manager.api.v1.routes.duplicates import router as duplicates_router
from doc_manager.api.v1.routes.jobs import router as jobs_router
from doc_manager.api.v1.routes.locations import router as locations_router
from doc_manager.api.v1.routes.maintenance import router as maintenance_router
from doc_manager.api.v1.routes.search import router as search_router
from doc_manager.core.config import get_settings
from doc_manager.health import build_readiness

router = APIRouter(prefix="/api/v1")
router.include_router(locations_router)
router.include_router(documents_router)
router.include_router(errors_router)
router.include_router(duplicates_router)
router.include_router(coverage_router)
router.include_router(search_router)
router.include_router(ask_router)
router.include_router(providers_router)
router.include_router(maintenance_router)
router.include_router(jobs_router)


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, Any]:
    settings = get_settings()
    report = await build_readiness(settings)
    return {
        "version": __version__,
        "environment": settings.env.value,
        "generation_provider": settings.generation_provider.value,
        "external_llm_enabled": settings.external_llm_enabled,
        "filesystem_profile": settings.resolved_filesystem_profile,
        "ready": report.ready,
        "search_only": report.search_only,
        "components": [
            {"name": c.name, "required": c.required, "status": c.status.value}
            for c in report.components
        ],
    }
