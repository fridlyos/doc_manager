"""Job handlers, keyed by job type. The worker claims only registered types."""

from collections.abc import Awaitable, Callable

from doc_manager.domain.enums import JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.handlers.cleanup import handle_remove_stale_vectors
from doc_manager.jobs.handlers.consistency import handle_catalog_consistency_check
from doc_manager.jobs.handlers.duplicates import handle_build_duplicate_report
from doc_manager.jobs.handlers.index_file import handle_index_file
from doc_manager.jobs.handlers.reindex import handle_reindex_bulk
from doc_manager.jobs.handlers.scan_location import handle_scan_location
from doc_manager.jobs.handlers.sync_plan import handle_build_sync_plan

Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[JobType, Handler] = {
    JobType.scan_location: handle_scan_location,
    JobType.index_file: handle_index_file,
    JobType.catalog_consistency_check: handle_catalog_consistency_check,
    JobType.reindex_all_for_profile: handle_reindex_bulk,
    JobType.remove_stale_vectors: handle_remove_stale_vectors,
    JobType.build_duplicate_report: handle_build_duplicate_report,
    JobType.build_sync_plan: handle_build_sync_plan,
}
