"""Tests for database connection pool management."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.db.connection import create_pool, close_pool


@pytest.mark.asyncio
async def test_create_pool_passes_ssl_require():
    """create_pool calls asyncpg.create_pool with ssl='require'."""
    mock_pool = MagicMock()
    with patch("src.db.connection.asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
        pool = await create_pool("postgresql://user:pass@host/db")
        mock_create.assert_called_once_with("postgresql://user:pass@host/db", ssl="require", statement_cache_size=0)
        assert pool is mock_pool


@pytest.mark.asyncio
async def test_close_pool_calls_close():
    """close_pool calls pool.close()."""
    mock_pool = AsyncMock()
    await close_pool(mock_pool)
    mock_pool.close.assert_called_once()
