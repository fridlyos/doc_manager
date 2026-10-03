"""Backup retention selection (TECHSTACK 11.6, Phase 8.c). Pure.

Grandfather-father-son: keep the newest set for each of the most recent ``daily``
days, ``weekly`` ISO-weeks, and ``monthly`` months. A set that qualifies in more
than one bucket is kept once. Everything not selected is pruned. Pure so the policy
is unit-testable independently of the filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime


def select_retained(
    dated_ids: Iterable[tuple[str, datetime]],
    *,
    daily: int,
    weekly: int,
    monthly: int,
) -> set[str]:
    """Return the backup ids to keep. Newest-first within each bucket."""
    items = sorted(dated_ids, key=lambda pair: pair[1], reverse=True)
    kept: set[str] = set()

    def keep_newest_per_bucket(key: Callable[[datetime], object], count: int) -> None:
        seen: dict[object, str] = {}
        for backup_id, when in items:
            bucket = key(when)
            if bucket in seen:
                continue
            if len(seen) >= count:
                continue
            seen[bucket] = backup_id
            kept.add(backup_id)

    keep_newest_per_bucket(lambda d: d.date(), daily)
    keep_newest_per_bucket(lambda d: d.isocalendar()[:2], weekly)
    keep_newest_per_bucket(lambda d: (d.year, d.month), monthly)
    return kept


def select_pruned(
    dated_ids: Iterable[tuple[str, datetime]],
    *,
    daily: int,
    weekly: int,
    monthly: int,
) -> list[str]:
    """Ids to delete: everything not retained, oldest first."""
    items = list(dated_ids)
    kept = select_retained(items, daily=daily, weekly=weekly, monthly=monthly)
    pruned = [bid for bid, _ in items if bid not in kept]
    order = dict(items)
    return sorted(pruned, key=lambda bid: order[bid])
