"""Bounded generation streaming (TECHSTACK 5.13: "bounded timeouts").

Wraps a provider's event stream with an overall deadline. If the provider does
not finish within ``timeout_seconds``, the wrapper closes the underlying stream
and raises ``GenerationError(provider_timeout)``. Cancellation propagates: if the
consumer stops iterating (disconnect), ``aclose`` tears down the provider work —
the Ask service relies on this to cancel on client disconnect with no fallback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from doc_manager.generation.errors import GenerationError, GenerationErrorCode
from doc_manager.generation.events import GenerationEvent


async def stream_with_timeout(
    events: AsyncIterator[GenerationEvent], *, timeout_seconds: float
) -> AsyncIterator[GenerationEvent]:
    """Yield events under an overall deadline; map expiry to ``provider_timeout``."""
    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in events:
                yield event
    except TimeoutError as exc:
        # Tear down the underlying provider stream if it supports it (async
        # generators do), so the provider request is cancelled rather than leaked.
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()
        raise GenerationError(
            GenerationErrorCode.provider_timeout,
            f"provider did not finish within {timeout_seconds:g}s",
        ) from exc
