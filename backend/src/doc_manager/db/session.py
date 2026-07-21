"""Async SQLAlchemy engine and session management.

One engine per process (API or worker), created at startup and disposed at
shutdown. All time comparisons in job/lease logic use PostgreSQL time via
:func:`db_now` so Windows/container clock skew cannot corrupt lease fencing
(state-machine contract section 5).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from doc_manager.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: job rows are read after the claim/transition
    # transaction commits; re-fetching them would race other workers.
    return async_sessionmaker(engine, expire_on_commit=False)


async def db_now(session: AsyncSession) -> datetime:
    """Authoritative current time from PostgreSQL (never the host clock)."""
    result = await session.execute(text("SELECT clock_timestamp()"))
    now = result.scalar_one()
    assert isinstance(now, datetime)
    return now
