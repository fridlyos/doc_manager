"""Liveness and readiness reporting.

Readiness distinguishes *required* dependencies (PostgreSQL, Qdrant) from
*optional* ones (local/external generation providers, source mounts). The
service is "ready" when required dependencies are up; a stopped Ollama or an
unconfigured OpenAI provider degrades to search-only mode instead of failing
readiness (TECHSTACK section 10, Phase 1 exit criteria).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from doc_manager.core.config import GenerationProvider, Settings


class ComponentStatus(StrEnum):
    up = "up"
    down = "down"
    disabled = "disabled"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class Component:
    name: str
    required: bool
    status: ComponentStatus
    detail: str = ""


@dataclass(slots=True)
class ReadinessReport:
    components: list[Component] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        # Ready when every *required* component is up. Optional components may be
        # down or disabled without blocking readiness.
        return all(c.status is ComponentStatus.up for c in self.components if c.required)

    @property
    def search_only(self) -> bool:
        """True when required services are up but no generation provider is ready."""
        if not self.ready:
            return False
        providers = [c for c in self.components if c.name in {"ollama", "openai"}]
        return not any(c.status is ComponentStatus.up for c in providers)


async def _check_postgres(settings: Settings) -> Component:
    name = "postgres"
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return Component(name, required=True, status=ComponentStatus.up)
    except Exception as exc:  # noqa: BLE001 - report any failure as down
        return Component(
            name, required=True, status=ComponentStatus.down, detail=type(exc).__name__
        )
    finally:
        await engine.dispose()


async def _check_qdrant(settings: Settings, client: httpx.AsyncClient) -> Component:
    name = "qdrant"
    try:
        resp = await client.get(f"{settings.qdrant_url}/readyz", timeout=5.0)
        if resp.status_code == 200:
            return Component(name, required=True, status=ComponentStatus.up)
        return Component(
            name, required=True, status=ComponentStatus.down, detail=f"http {resp.status_code}"
        )
    except httpx.HTTPError as exc:
        return Component(
            name, required=True, status=ComponentStatus.down, detail=type(exc).__name__
        )


async def _check_ollama(settings: Settings, client: httpx.AsyncClient) -> Component:
    name = "ollama"
    if settings.generation_provider is not GenerationProvider.ollama:
        return Component(name, required=False, status=ComponentStatus.disabled)
    try:
        resp = await client.get(f"{settings.ollama_url}/api/tags", timeout=5.0)
        status = ComponentStatus.up if resp.status_code == 200 else ComponentStatus.down
        return Component(name, required=False, status=status)
    except httpx.HTTPError as exc:
        return Component(
            name, required=False, status=ComponentStatus.down, detail=type(exc).__name__
        )


def _check_openai(settings: Settings) -> Component:
    name = "openai"
    if (
        not settings.external_llm_enabled
        or settings.generation_provider is not GenerationProvider.openai
    ):
        return Component(name, required=False, status=ComponentStatus.disabled)
    # We do not spend a request (or expose the key) just to compute readiness;
    # presence of a model + secret is the Phase 1 readiness signal.
    if settings.openai_model and settings.read_openai_api_key():
        return Component(name, required=False, status=ComponentStatus.up, detail="configured")
    return Component(
        name, required=False, status=ComponentStatus.down, detail="missing model or key"
    )


async def build_readiness(settings: Settings) -> ReadinessReport:
    async with httpx.AsyncClient() as client:
        postgres, qdrant, ollama = await asyncio.gather(
            _check_postgres(settings),
            _check_qdrant(settings, client),
            _check_ollama(settings, client),
        )
    return ReadinessReport(components=[postgres, qdrant, ollama, _check_openai(settings)])
