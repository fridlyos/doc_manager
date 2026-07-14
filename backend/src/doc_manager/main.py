"""FastAPI application factory and API entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from doc_manager import __version__
from doc_manager.api.health import router as health_router
from doc_manager.api.v1.router import router as v1_router
from doc_manager.core.config import Settings, get_settings
from doc_manager.core.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.env.value != "development")
    log = get_logger("doc_manager.api")
    log.info(
        "api_startup",
        version=__version__,
        environment=settings.env.value,
        generation_provider=settings.generation_provider.value,
        external_llm_enabled=settings.external_llm_enabled,
    )
    yield
    log.info("api_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="doc_manager",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(v1_router)
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
