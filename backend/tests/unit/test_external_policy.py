"""External-processing policy + data-boundary accounting (Phase 5.c).

Pure/offline. Verifies the deny-default, fail-closed transfer gate and that the
boundary counters never carry metadata.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from doc_manager.core.config import Settings
from doc_manager.generation import (
    DataBoundary,
    ExternalDecision,
    GenerationEvent,
    PolicyOutcome,
    confirmation_summary,
    evaluate_external_policy,
    external_boundary,
    local_boundary,
)
from doc_manager.generation.base import ProviderCapabilities, ProviderReadiness


class StubProvider:
    def __init__(self, provider_id: str, boundary: DataBoundary) -> None:
        self.provider_id = provider_id
        self.data_boundary = boundary
        self.capabilities = ProviderCapabilities(context_tokens=8192, max_output_tokens=512)

    async def readiness(self) -> ProviderReadiness:  # pragma: no cover - unused here
        raise NotImplementedError

    def secret_available(self, settings: Settings) -> bool:
        return True

    async def generate(self, request: Any) -> AsyncIterator[GenerationEvent]:  # pragma: no cover
        raise NotImplementedError
        yield


_LOCAL = StubProvider("ollama", DataBoundary.local)
_EXTERNAL = StubProvider("openai", DataBoundary.external)


def _evaluate(
    settings: Settings, provider: StubProvider, policies: Sequence[str], ack: bool
) -> PolicyOutcome:
    return evaluate_external_policy(
        settings=settings,
        provider=provider,
        evidence_source_policies=policies,
        acknowledged=ack,
    )


def test_local_provider_bypasses_policy() -> None:
    out = _evaluate(Settings(external_llm_enabled=False), _LOCAL, ["deny", "deny"], ack=False)
    assert out.decision is ExternalDecision.allowed
    assert out.boundary is DataBoundary.local


def test_external_disabled_is_denied() -> None:
    out = _evaluate(Settings(external_llm_enabled=False), _EXTERNAL, ["allow"], ack=True)
    assert out.decision is ExternalDecision.denied
    assert out.boundary is DataBoundary.external


def test_any_denied_source_fails_closed() -> None:
    out = _evaluate(
        Settings(external_llm_enabled=True), _EXTERNAL, ["allow", "deny", "allow"], ack=True
    )
    assert out.decision is ExternalDecision.denied
    assert out.denied_source_count == 1


def test_all_allow_without_ack_requires_confirmation() -> None:
    out = _evaluate(Settings(external_llm_enabled=True), _EXTERNAL, ["allow", "allow"], ack=False)
    assert out.decision is ExternalDecision.confirmation_required
    assert out.boundary is DataBoundary.external


def test_all_allow_with_ack_is_allowed() -> None:
    out = _evaluate(Settings(external_llm_enabled=True), _EXTERNAL, ["allow"], ack=True)
    assert out.decision is ExternalDecision.allowed
    assert out.is_allowed


def test_no_evidence_sources_with_ack_is_allowed() -> None:
    out = _evaluate(Settings(external_llm_enabled=True), _EXTERNAL, [], ack=True)
    assert out.decision is ExternalDecision.allowed


# --- boundary accounting ----------------------------------------------------

_META_KEYS = {
    "paths_sent",
    "file_names_sent",
    "tags_sent",
    "catalog_ids_sent",
    "original_files_sent",
}


def test_local_boundary_is_all_zero_and_local() -> None:
    report = local_boundary().as_dict()
    assert report["classification"] == "local"
    assert report["external_request_attempted"] is False
    assert report["external_transfer_occurred"] is False
    assert all(report["external_payload"][k] == 0 for k in _META_KEYS)


def test_external_boundary_attempted_reports_text_counts_no_metadata() -> None:
    report = external_boundary(
        acknowledged=True,
        attempted=True,
        occurred=True,
        evidence_blocks=3,
        evidence_characters=1200,
        citation_ids=3,
    ).as_dict()
    assert report["classification"] == "external"
    assert report["external_transfer_occurred"] is True
    payload = report["external_payload"]
    assert payload["evidence_blocks_sent"] == 3
    assert payload["question_sent"] is True
    # Metadata counters are structurally zero.
    assert all(payload[k] == 0 for k in _META_KEYS)


def test_external_boundary_not_attempted_zeroes_payload() -> None:
    payload = external_boundary(
        acknowledged=True,
        attempted=False,
        occurred=False,
        evidence_blocks=3,
        evidence_characters=1200,
        citation_ids=3,
    ).as_dict()["external_payload"]
    assert payload["evidence_blocks_sent"] == 0
    assert payload["question_sent"] is False


def test_confirmation_summary_counts_only() -> None:
    summary = confirmation_summary(
        provider_id="openai", evidence_blocks=4, evidence_characters=9210
    )
    assert summary == {
        "classification": "external",
        "provider_id": "openai",
        "evidence_blocks": 4,
        "evidence_characters": 9210,
        "paths_sent": 0,
        "file_names_sent": 0,
        "tags_sent": 0,
        "catalog_ids_sent": 0,
    }
