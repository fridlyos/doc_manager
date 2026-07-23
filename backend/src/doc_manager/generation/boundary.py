"""Data-boundary accounting (contract §8.2; TECHSTACK §12).

Every Ask result reports a ``data_boundary`` describing exactly what — if
anything — left the local environment. The counters are the auditable proof of
the privacy boundary: only question/grounding/evidence text and opaque citation
ids may ever be sent, so the metadata counters (paths, file names, tags, catalog
ids, original files) are **structurally always zero** — there is no code path
that sets them.

The Ask service builds one of:
- ``local_boundary`` — local provider; nothing external, all flags false.
- ``confirmation_boundary`` — external gates pass but acknowledgment is missing;
  a counts-only preview, no request made.
- ``external_boundary`` — an external request was attempted; once the HTTP write
  begins, ``external_request_attempted`` and the conservatively-named
  ``external_transfer_occurred`` are both true even if the upstream later fails.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

_LOCAL = "local"
_EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ExternalPayload:
    """What was (or would be) sent externally. Metadata counters stay zero."""

    question_sent: bool = False
    grounding_instructions_sent: bool = False
    evidence_blocks_sent: int = 0
    evidence_characters_sent: int = 0
    opaque_citation_ids_sent: int = 0
    # Structurally always zero — no code sets these (§12).
    paths_sent: int = 0
    file_names_sent: int = 0
    tags_sent: int = 0
    catalog_ids_sent: int = 0
    original_files_sent: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DataBoundaryReport:
    classification: str
    external_processing_acknowledged: bool
    external_request_attempted: bool
    external_transfer_occurred: bool
    external_payload: ExternalPayload

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "external_processing_acknowledged": self.external_processing_acknowledged,
            "external_request_attempted": self.external_request_attempted,
            "external_transfer_occurred": self.external_transfer_occurred,
            "external_payload": self.external_payload.as_dict(),
        }


def local_boundary() -> DataBoundaryReport:
    """A fully-local result: nothing external, empty payload."""
    return DataBoundaryReport(
        classification=_LOCAL,
        external_processing_acknowledged=False,
        external_request_attempted=False,
        external_transfer_occurred=False,
        external_payload=ExternalPayload(),
    )


def external_boundary(
    *,
    acknowledged: bool,
    attempted: bool,
    occurred: bool,
    evidence_blocks: int,
    evidence_characters: int,
    citation_ids: int,
    question_sent: bool = True,
    grounding_sent: bool = True,
) -> DataBoundaryReport:
    """An external classification. ``attempted``/``occurred`` flip once the HTTP
    write begins; the payload reports only text/alias counts."""
    return DataBoundaryReport(
        classification=_EXTERNAL,
        external_processing_acknowledged=acknowledged,
        external_request_attempted=attempted,
        external_transfer_occurred=occurred,
        external_payload=ExternalPayload(
            question_sent=question_sent if attempted else False,
            grounding_instructions_sent=grounding_sent if attempted else False,
            evidence_blocks_sent=evidence_blocks if attempted else 0,
            evidence_characters_sent=evidence_characters if attempted else 0,
            opaque_citation_ids_sent=citation_ids if attempted else 0,
        ),
    )


def confirmation_summary(
    *, provider_id: str, evidence_blocks: int, evidence_characters: int
) -> dict[str, Any]:
    """Counts-only preview for an ``external_confirmation_required`` response.

    No provider call has occurred; metadata counts are zero (§8.1).
    """
    return {
        "classification": _EXTERNAL,
        "provider_id": provider_id,
        "evidence_blocks": evidence_blocks,
        "evidence_characters": evidence_characters,
        "paths_sent": 0,
        "file_names_sent": 0,
        "tags_sent": 0,
        "catalog_ids_sent": 0,
    }
