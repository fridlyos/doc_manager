from __future__ import annotations

import pytest
from pydantic import ValidationError

from doc_manager.core.config import GenerationProvider, Settings


def test_defaults_are_local_and_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.bind_host == "127.0.0.1"
    assert settings.generation_provider is GenerationProvider.ollama
    assert settings.external_llm_enabled is False
    assert settings.external_source_default == "deny"
    assert settings.store_query_history is False


def test_openai_requires_external_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            generation_provider="openai",
            external_llm_enabled=False,
            openai_model="gpt-x",
        )


def test_openai_requires_model() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            generation_provider="openai",
            external_llm_enabled=True,
            external_provider_allowlist="openai",
            openai_model=None,
        )


def test_openai_valid_combo() -> None:
    settings = Settings(
        _env_file=None,
        generation_provider="openai",
        external_llm_enabled=True,
        external_provider_allowlist="openai",
        openai_model="gpt-x",
    )
    assert settings.generation_provider is GenerationProvider.openai
    assert "openai" in settings.external_provider_allowlist_set


def test_invalid_source_default_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, external_source_default="maybe")


def test_allowed_source_root_paths_parsed() -> None:
    settings = Settings(_env_file=None, allowed_source_roots="/sources, /extra")
    roots = [str(p) for p in settings.allowed_source_root_paths]
    assert roots == ["/sources", "/extra"]
