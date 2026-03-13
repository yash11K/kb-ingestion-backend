"""asyncpg connection pool management."""

import asyncpg


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Create asyncpg connection pool with SSL required."""
    return await asyncpg.create_pool(
        database_url,
        ssl="require",
        statement_cache_size=0,
    )


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close the connection pool gracefully on shutdown."""
    await pool.close()
