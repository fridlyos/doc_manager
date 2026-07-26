"""External-processing policy (TECHSTACK 5.13, §12; contract §8.1).

Decides whether one Ask request may send evidence to an external provider. The
default is **deny**, evaluated per request against every evidence-bearing source
location. It **fails closed** — a single denied source blocks the whole request;
evidence is never silently dropped and no other provider is selected (no
fallback). External transfer proceeds only when the deployment opt-in, the
provider eligibility, **all** evidence sources' `allow`, and the request's
explicit acknowledgment line up.

This module is the *transfer* gate. Deployment eligibility (enabled + allowlist +
secret) is the registry's job (Phase 5.a); the Ask service checks that first, so
by the time policy runs the provider is already eligible. Policy still re-verifies
`external_llm_enabled` as a hard, fail-closed invariant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from doc_manager.core.config import Settings
from doc_manager.domain.enums import ExternalGenerationPolicy
from doc_manager.generation.base import DataBoundary, GenerationProvider


class ExternalDecision(StrEnum):
    #: Proceed. Local providers, or an external transfer with every gate satisfied.
    allowed = "allowed"
    #: All gates pass except acknowledgment — return a counts-only preview and make
    #: no provider call until the user confirms (contract §8.1).
    confirmation_required = "confirmation_required"
    #: Fail closed — deployment disabled or an evidence source denies external use.
    denied = "denied"


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    decision: ExternalDecision
    boundary: DataBoundary
    #: Safe, source-name-free explanation for denied/confirmation outcomes.
    reason: str = ""
    #: How many evidence sources deny external processing (0 unless denied).
    denied_source_count: int = 0

    @property
    def is_allowed(self) -> bool:
        return self.decision is ExternalDecision.allowed


def evaluate_external_policy(
    *,
    settings: Settings,
    provider: GenerationProvider,
    evidence_source_policies: Sequence[str],
    acknowledged: bool,
) -> PolicyOutcome:
    """Decide the data boundary for one Ask request.

    ``evidence_source_policies`` is the ``external_generation_policy`` of every
    source location backing the selected evidence.
    """
    if provider.data_boundary is DataBoundary.local:
        # Local inference never transfers evidence; policy does not apply.
        return PolicyOutcome(ExternalDecision.allowed, DataBoundary.local)

    if not settings.external_llm_enabled:
        return PolicyOutcome(
            ExternalDecision.denied,
            DataBoundary.external,
            reason="external generation is disabled for this deployment",
        )

    denied = sum(
        1 for policy in evidence_source_policies if policy != ExternalGenerationPolicy.allow.value
    )
    if denied:
        return PolicyOutcome(
            ExternalDecision.denied,
            DataBoundary.external,
            reason="one or more evidence sources deny external processing",
            denied_source_count=denied,
        )

    if not acknowledged:
        return PolicyOutcome(
            ExternalDecision.confirmation_required,
            DataBoundary.external,
            reason="external processing must be explicitly acknowledged",
        )

    return PolicyOutcome(ExternalDecision.allowed, DataBoundary.external)
