"""Provider-neutral generation interface (TECHSTACK 5.13).

The RAG/Ask layer depends on this ``GenerationProvider`` protocol, never on
Ollama/OpenAI response types. Adapters (Phase 5.b Ollama, 5.d OpenAI) implement
it; the registry (``registry.py``) selects among them with no automatic fallback.

An adapter is stateless: each ``generate`` call is an independent, streamed
request. Cancellation is cooperative — closing the returned async iterator
(``aclose``) stops the provider work; the Ask service does this on disconnect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from doc_manager.core.config import Settings
from doc_manager.generation.events import GenerationEvent


class DataBoundary(StrEnum):
    #: Inference stays on the local host (Ollama). No external transfer.
    local = "local"
    #: Inference is performed by an external service (OpenAI).
    external = "external"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Provider/model limits the RAG layer must respect."""

    #: Total context window (tokens) of the configured model.
    context_tokens: int
    #: Maximum tokens the provider may generate per request.
    max_output_tokens: int
    supports_streaming: bool = True


@dataclass(frozen=True, slots=True)
class ProviderReadiness:
    """Result of an adapter's readiness/model-validation probe."""

    ready: bool
    detail: str = ""
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """A fully-formed generation request. The RAG layer (5.e) builds it.

    ``system_prompt`` carries the grounding instructions and numbered evidence
    blocks; ``user_prompt`` is the question. Evidence text is already embedded —
    the provider never resolves paths or citations itself.
    """

    system_prompt: str
    user_prompt: str
    max_output_tokens: int
    #: Optional model override; must be a model the provider config exposes. When
    #: ``None`` the adapter uses its configured active model.
    model_id: str | None = None


@runtime_checkable
class GenerationProvider(Protocol):
    """Normalized, stateless, streaming generation adapter."""

    provider_id: str
    data_boundary: DataBoundary
    capabilities: ProviderCapabilities

    async def readiness(self) -> ProviderReadiness:
        """Validate the endpoint/model without generating. Cheap, no side effects."""
        ...

    def secret_available(self, settings: Settings) -> bool:
        """Whether this provider's required credential is present.

        Local providers need none (return ``True``); external providers check the
        injected secret. Used by the registry's eligibility gate.
        """
        ...

    def generate(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        """Stream normalized events for one request. Never yields SDK types."""
        ...
