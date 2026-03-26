"""SQLAlchemy async engine and session factory management.

Replaces the raw asyncpg pool in connection.py with SQLAlchemy's
async engine, session maker, and a FastAPI-compatible dependency.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# Module-level session_factory reference, set during app startup.
session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Create an AsyncEngine with the asyncpg dialect.

    Configures SSL and disables the statement cache for compatibility
    with PgBouncer-style connection poolers (e.g. Neon).
    """
    return create_async_engine(
        database_url,
        connect_args={"ssl": "require", "statement_cache_size": 0},
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async session maker bound to *engine*."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a session per request.

    Commits on success, rolls back on exception.
    """
    assert session_factory is not None, "session_factory not initialised — call init_engine first"
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
