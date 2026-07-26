"""SQLAlchemy models. Importing this package registers all tables on Base."""

from doc_manager.db.models.base import Base
from doc_manager.db.models.catalog import CatalogEntry, ScanObservation
from doc_manager.db.models.content import Chunk, ContentObject, FileVersion
from doc_manager.db.models.duplicates import DuplicateGroup, DuplicateMember
from doc_manager.db.models.jobs import (
    IdempotencyRecord,
    IngestionJob,
    IngestionJobAttempt,
    JobCheckpoint,
    JobEvent,
    SchedulerState,
)
from doc_manager.db.models.locations import SourceLocation

__all__ = [
    "Base",
    "CatalogEntry",
    "Chunk",
    "ContentObject",
    "DuplicateGroup",
    "DuplicateMember",
    "FileVersion",
    "IdempotencyRecord",
    "IngestionJob",
    "IngestionJobAttempt",
    "JobCheckpoint",
    "JobEvent",
    "ScanObservation",
    "SchedulerState",
    "SourceLocation",
]
