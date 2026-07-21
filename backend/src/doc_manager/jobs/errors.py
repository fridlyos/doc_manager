"""Job-handler error classification (state-machine contract section 6).

Handlers raise these to classify failures; everything else is
`internal_unclassified` and retried only within the attempt budget. Messages
must be safe: no document text, paths beyond what the API may display, or
credentials.
"""

from __future__ import annotations


class JobError(Exception):
    """Base class carrying a stable machine code and a safe message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TransientJobError(JobError):
    """May succeed on retry without changing the request (NAS blip, timeout)."""


class PermanentJobError(JobError):
    """Requires changed input/configuration or an operator decision."""


class LeaseLostError(Exception):
    """A fenced write affected zero rows: this attempt lost authority.

    The worker must stop immediately without publishing anything further.
    """


class CancelObservedError(Exception):
    """Cancellation was observed at a safe boundary; stop without publishing."""


class ShutdownRequestedError(Exception):
    """Graceful worker shutdown: release the job at the nearest checkpoint."""
