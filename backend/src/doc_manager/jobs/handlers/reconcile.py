"""Content-aware reconciliation of a scan against the catalog (TECHSTACK 7.1/7.6).

Pure functions: given the catalog snapshot for a location and the files observed
by a complete scan, produce a plan of catalog transitions. SHA-256 is the
authority, so this recognizes:

- **added** — a new path with content not seen elsewhere.
- **changed** — an existing path whose bytes differ (re-index needed).
- **metadata**/**unchanged** — an existing path with identical bytes (an
  mtime-only touch never triggers re-index).
- **moved** — a new path whose bytes match a path that disappeared this scan;
  the entry is retargeted, preserving its indexed state and content identity.
- **restored** — a previously ``missing`` path observed again.
- **missing** — a catalog path not observed and not claimed by a move.

Keeping this DB-free makes the filesystem lifecycle cases unit-testable without
Postgres, and keeps the fenced apply transaction in the handler small.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from doc_manager.domain.enums import CatalogEntryState

_MISSING = CatalogEntryState.missing.value
_DISCOVERED = CatalogEntryState.discovered.value


@dataclass(frozen=True, slots=True)
class ObservedFile:
    relative_path: str
    file_name: str
    extension: str
    size_bytes: int
    mtime: datetime
    sha256: str


@dataclass(frozen=True, slots=True)
class CatalogRow:
    id: uuid.UUID
    relative_path: str
    state: str
    size_bytes: int | None
    mtime: datetime | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class AddEntry:
    observed: ObservedFile
    kind: str = "added"


@dataclass(frozen=True, slots=True)
class UpdateEntry:
    entry_id: uuid.UUID
    observed: ObservedFile
    state: str
    clear_missing: bool
    kind: str  # unchanged | metadata | changed | moved | restored


@dataclass(frozen=True, slots=True)
class MarkMissing:
    entry_id: uuid.UUID


@dataclass(slots=True)
class ReconcilePlan:
    adds: list[AddEntry] = field(default_factory=list)
    updates: list[UpdateEntry] = field(default_factory=list)
    missing: list[MarkMissing] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = defaultdict(int)
        for add in self.adds:
            tally[add.kind] += 1
        for upd in self.updates:
            tally[upd.kind] += 1
        if self.missing:
            tally["missing"] = len(self.missing)
        return dict(tally)


def reconcile(catalog: list[CatalogRow], observations: list[ObservedFile]) -> ReconcilePlan:
    observed_by_path = {o.relative_path: o for o in observations}
    catalog_by_path = {c.relative_path: c for c in catalog}

    # Entries that disappeared this scan are candidate move/rename sources,
    # indexed by content hash. A missing entry may also be the source of a
    # move (a file that was missing reappears under a new name).
    disappeared_by_sha: dict[str, list[CatalogRow]] = defaultdict(list)
    for row in catalog:
        if row.relative_path not in observed_by_path and row.sha256 is not None:
            disappeared_by_sha[row.sha256].append(row)

    plan = ReconcilePlan()
    moved_source_ids: set[uuid.UUID] = set()

    for path, obs in observed_by_path.items():
        existing = catalog_by_path.get(path)
        if existing is None:
            source = _claim_move_source(disappeared_by_sha, obs.sha256)
            if source is not None:
                moved_source_ids.add(source.id)
                was_missing = source.state == _MISSING
                plan.updates.append(
                    UpdateEntry(
                        entry_id=source.id,
                        observed=obs,
                        state=_DISCOVERED if was_missing else source.state,
                        clear_missing=was_missing,
                        kind="moved",
                    )
                )
            else:
                plan.adds.append(AddEntry(observed=obs))
            continue

        if existing.state == _MISSING:
            plan.updates.append(
                UpdateEntry(
                    entry_id=existing.id,
                    observed=obs,
                    state=_DISCOVERED,
                    clear_missing=True,
                    kind="restored",
                )
            )
        elif existing.sha256 == obs.sha256:
            same_meta = existing.size_bytes == obs.size_bytes and existing.mtime == obs.mtime
            plan.updates.append(
                UpdateEntry(
                    entry_id=existing.id,
                    observed=obs,
                    state=existing.state,
                    clear_missing=False,
                    kind="unchanged" if same_meta else "metadata",
                )
            )
        else:
            plan.updates.append(
                UpdateEntry(
                    entry_id=existing.id,
                    observed=obs,
                    state=_DISCOVERED,
                    clear_missing=False,
                    kind="changed",
                )
            )

    for row in catalog:
        if (
            row.relative_path not in observed_by_path
            and row.id not in moved_source_ids
            and row.state != _MISSING
        ):
            plan.missing.append(MarkMissing(entry_id=row.id))

    return plan


def _claim_move_source(
    disappeared_by_sha: dict[str, list[CatalogRow]], sha256: str
) -> CatalogRow | None:
    candidates = disappeared_by_sha.get(sha256)
    if candidates:
        return candidates.pop()
    return None
