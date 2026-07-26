"""Job handlers, keyed by job type. The worker claims only registered types."""

from collections.abc import Awaitable, Callable

from doc_manager.domain.enums import JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.handlers.consistency import handle_catalog_consistency_check
from doc_manager.jobs.handlers.index_file import handle_index_file
from doc_manager.jobs.handlers.reindex import handle_reindex_bulk
from doc_manager.jobs.handlers.scan_location import handle_scan_location

Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[JobType, Handler] = {
    JobType.scan_location: handle_scan_location,
    JobType.index_file: handle_index_file,
    JobType.catalog_consistency_check: handle_catalog_consistency_check,
    JobType.reindex_all_for_profile: handle_reindex_bulk,
}
