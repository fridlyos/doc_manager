"""FastAPI application factory and API entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from doc_manager import __version__
from doc_manager.api.errors import RequestIDMiddleware, install_error_handlers
from doc_manager.api.health import router as health_router
from doc_manager.api.v1.router import router as v1_router
from doc_manager.core.config import Settings, get_settings
from doc_manager.core.logging import configure_logging, get_logger
from doc_manager.db.session import create_engine, create_session_factory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.env.value != "development")
    log = get_logger("doc_manager.api")
    engine = create_engine(settings)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    log.info(
        "api_startup",
        version=__version__,
        environment=settings.env.value,
        generation_provider=settings.generation_provider.value,
        external_llm_enabled=settings.external_llm_enabled,
    )
    yield
    await engine.dispose()
    log.info("api_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="doc_manager",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.add_middleware(RequestIDMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(v1_router)

    # The production image bundles the Vite build. Browser routes such as
    # /locations must return index.html so React Router can resolve them.
    # API and health misses remain real 404s rather than being masked by the SPA.
    frontend_dist = settings.frontend_dist
    index_file = frontend_dist / "index.html"
    assets_dir = frontend_dist / "assets"
    if index_file.is_file():
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def frontend_fallback(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            if full_path == "health" or full_path.startswith("health/"):
                raise HTTPException(status_code=404)
            return FileResponse(index_file)

    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: start uvicorn bound to configured host/port."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "doc_manager.main:app",
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
