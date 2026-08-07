# Architecture Decision Records

Architecture Decision Records (ADRs) capture decisions that materially constrain implementation. They explain the context, selected option, consequences, and alternatives so later changes are deliberate rather than accidental.

## Status meanings

- `Proposed`: ready for Phase 0 review but not yet approved as an implementation constraint.
- `Accepted`: approved and binding on implementation.
- `Superseded`: replaced by a later ADR that links back to it.
- `Rejected`: considered but not selected.

Phase 1 must not begin until the Phase 0 ADR set is accepted or explicitly amended.

## Phase 0 ADRs

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-separate-physical-files-from-canonical-content.md) | Separate physical files from canonical content | Proposed |
| [0002](0002-postgresql-durable-job-queue.md) | Use PostgreSQL as the durable job queue | Proposed |
| [0003](0003-content-addressed-extracted-text.md) | Store extracted text as content-addressed compressed artifacts | Proposed |
| [0004](0004-pluggable-generation-provider-boundary.md) | Use a pluggable generation-provider privacy boundary | Proposed |
| [0005](0005-windows-docker-desktop-nas-storage.md) | Use Windows Docker Desktop with local database volumes and NAS file mounts | Proposed |
| [0006](0006-sync-executor-boundary.md) | Keep sync execution out of the MVP behind a separately-reviewed executor boundary | Proposed |

## Creating later ADRs

Use the next four-digit sequence number. An ADR is immutable after acceptance except for status and supersession links; a changed decision gets a new ADR.

