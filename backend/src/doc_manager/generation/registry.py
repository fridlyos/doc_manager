"""Generation provider registry (TECHSTACK 5.13, §12).

Holds the built provider adapters and decides which are *eligible* for a given
deployment configuration. Eligibility is a static, config-level gate (it never
makes a network call — that is ``provider.readiness()``):

- a **local** provider is always eligible;
- an **external** provider is eligible only when deployment opt-in
  (``external_llm_enabled``) is set, it is on ``external_provider_allowlist``, and
  its secret is present.

There is **no automatic fallback**: selecting an ineligible or unknown provider is
an explicit error, never a silent switch to another provider (exit criterion 5).

Phase 5.a ships the gate; concrete adapters register in 5.b (Ollama) and 5.d
(OpenAI), so ``build_registry`` starts empty and is populated as adapters land.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import find_spec

from doc_manager.core.config import Settings
from doc_manager.core.logging import get_logger
from doc_manager.generation.base import DataBoundary, GenerationProvider
from doc_manager.generation.errors import GenerationError, GenerationErrorCode

log = get_logger("doc_manager.generation.registry")


def _allowlist(settings: Settings) -> set[str]:
    return {p.strip() for p in settings.external_provider_allowlist.split(",") if p.strip()}


class ProviderRegistry:
    def __init__(self, providers: Sequence[GenerationProvider]) -> None:
        self._by_id: dict[str, GenerationProvider] = {}
        for provider in providers:
            if provider.provider_id in self._by_id:
                raise ValueError(f"duplicate provider_id: {provider.provider_id}")
            self._by_id[provider.provider_id] = provider

    def all(self) -> list[GenerationProvider]:
        return list(self._by_id.values())

    def get(self, provider_id: str) -> GenerationProvider:
        """The named provider, or ``unknown_provider`` — never a fallback."""
        provider = self._by_id.get(provider_id)
        if provider is None:
            raise GenerationError(
                GenerationErrorCode.unknown_provider, f"no such provider: {provider_id}"
            )
        return provider

    def is_eligible(self, settings: Settings, provider: GenerationProvider) -> bool:
        """Config-level eligibility (no network). See module docstring."""
        if provider.data_boundary is DataBoundary.local:
            return True
        return (
            settings.external_llm_enabled
            and provider.provider_id in _allowlist(settings)
            and provider.secret_available(settings)
        )

    def eligible_ids(self, settings: Settings) -> list[str]:
        return [p.provider_id for p in self._by_id.values() if self.is_eligible(settings, p)]

    def require_eligible(self, settings: Settings, provider_id: str) -> GenerationProvider:
        """Return the provider only if it is known *and* eligible; else raise.

        External ineligibility is reported as ``provider_unavailable`` (the adapter
        exists but is not enabled here), distinct from ``unknown_provider``.
        """
        provider = self.get(provider_id)
        if not self.is_eligible(settings, provider):
            raise GenerationError(
                GenerationErrorCode.provider_unavailable,
                f"provider {provider_id} is not enabled in this deployment",
                retryable=False,
            )
        return provider


def build_registry(settings: Settings) -> ProviderRegistry:
    """Assemble the deployment's providers.

    The local Ollama adapter is always registered (eligibility still gates use).
    The external OpenAI adapter is registered only when the ``openai`` extra is
    installed and a model is configured — eligibility (opt-in + allowlist +
    secret) and the external-processing policy still gate every actual use.
    """
    from doc_manager.generation.ollama import build_ollama_provider

    providers: list[GenerationProvider] = [build_ollama_provider(settings)]

    if settings.openai_model and find_spec("openai") is not None:
        from doc_manager.generation.openai_provider import build_openai_provider

        providers.append(build_openai_provider(settings))
    elif settings.external_llm_enabled and "openai" in _allowlist(settings):
        log.warning(
            "openai_provider_unavailable",
            reason="missing openai extra or openai_model not configured",
        )

    return ProviderRegistry(providers)
