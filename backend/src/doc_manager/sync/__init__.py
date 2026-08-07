"""Multi-location comparison + sync planning (TECHSTACK 5.14, Phase 7).

Pure, read-only comparison of catalog hashes across source locations into dry-run
plans. No filesystem access and no execution path exist here or downstream — the
MVP compares and plans only.
"""

from doc_manager.sync.compare import (
    ComparisonResult,
    CoverageSummary,
    EntryRow,
    LocationSnapshot,
    SyncAction,
    SyncItem,
    compare_locations,
)

__all__ = [
    "ComparisonResult",
    "CoverageSummary",
    "EntryRow",
    "LocationSnapshot",
    "SyncAction",
    "SyncItem",
    "compare_locations",
]
