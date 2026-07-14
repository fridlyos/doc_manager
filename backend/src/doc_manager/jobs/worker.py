"""Background worker entrypoint.

Phase 1 provides a boot-healthy idle loop so the ``worker`` container starts,
logs, and shuts down cleanly. Durable job claim/lease/heartbeat/retry logic and
the scheduler arrive in Phase 2 (TECHSTACK section 5.11).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from doc_manager import __version__
from doc_manager.core.config import Settings, get_settings
from doc_manager.core.logging import configure_logging, get_logger


async def _run_loop(settings: Settings) -> None:
    log = get_logger("doc_manager.worker")
    stop = asyncio.Event()

    def _request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Signal handlers are unavailable on some non-POSIX loops; ignore there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    log.info(
        "worker_startup",
        version=__version__,
        concurrency=settings.worker_concurrency,
        lease_seconds=settings.job_lease_seconds,
    )
    # Idle heartbeat until a stop signal. Phase 2 replaces this with job polling.
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30.0)
        except TimeoutError:
            log.debug("worker_heartbeat")
    log.info("worker_shutdown")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env.value != "development")
    asyncio.run(_run_loop(settings))


if __name__ == "__main__":
    run()
