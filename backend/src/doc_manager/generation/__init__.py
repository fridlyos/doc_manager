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
from doc_manager.generation.boundary import (
    DataBoundaryReport,
    ExternalPayload,
    confirmation_summary,
    external_boundary,
    local_boundary,
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
from doc_manager.generation.ollama import OllamaProvider, build_ollama_provider
from doc_manager.generation.openai_provider import OpenAIProvider, build_openai_provider
from doc_manager.generation.policy import (
    ExternalDecision,
    PolicyOutcome,
    evaluate_external_policy,
)
from doc_manager.generation.registry import ProviderRegistry, build_registry
from doc_manager.generation.timeout import stream_with_timeout

__all__ = [
    "DataBoundary",
    "DataBoundaryReport",
    "ExternalDecision",
    "ExternalPayload",
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
    "OllamaProvider",
    "OpenAIProvider",
    "PolicyOutcome",
    "ProviderCapabilities",
    "ProviderReadiness",
    "ProviderRegistry",
    "Usage",
    "build_ollama_provider",
    "build_openai_provider",
    "build_registry",
    "confirmation_summary",
    "evaluate_external_policy",
    "external_boundary",
    "local_boundary",
    "stream_with_timeout",
]
