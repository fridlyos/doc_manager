"""Provider-neutral generation layer (TECHSTACK 5.13, Phase 5).

The interface + registry + normalized events that let the RAG/Ask layer drive a
local (Ollama) or explicitly-external (OpenAI) provider without depending on any
SDK's types, with no automatic fallback. Phase 5.a delivers the foundation;
adapters and the Ask service arrive in later Phase 5 steps.
"""

from doc_manager.generation.base import (
    DataBoundary,
    GenerationProvider,
    GenerationRequest,
    ProviderCapabilities,
    ProviderReadiness,
)
from doc_manager.generation.errors import GenerationError, GenerationErrorCode
from doc_manager.generation.events import (
    FinishReason,
    GenDelta,
    GenerationEvent,
    GenFinished,
    GenRefusal,
    GenStarted,
    GenUsage,
    Usage,
)
from doc_manager.generation.registry import ProviderRegistry, build_registry
from doc_manager.generation.timeout import stream_with_timeout

__all__ = [
    "DataBoundary",
    "FinishReason",
    "GenDelta",
    "GenFinished",
    "GenRefusal",
    "GenStarted",
    "GenUsage",
    "GenerationError",
    "GenerationErrorCode",
    "GenerationEvent",
    "GenerationProvider",
    "GenerationRequest",
    "ProviderCapabilities",
    "ProviderReadiness",
    "ProviderRegistry",
    "Usage",
    "build_registry",
    "stream_with_timeout",
]
