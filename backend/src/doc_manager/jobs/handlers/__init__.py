"""Job handlers, keyed by job type. The worker claims only registered types."""

from collections.abc import Awaitable, Callable

from doc_manager.domain.enums import JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.handlers.scan_location import handle_scan_location

Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[JobType, Handler] = {
    JobType.scan_location: handle_scan_location,
}
