"""Tests for check_duplicate tool."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.tools.duplicate_checker import check_duplicate, set_db_pool
import src.tools.duplicate_checker as dup_mod


@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset the module-level pool before and after each test."""
    dup_mod._db_pool = None
    yield
    dup_mod._db_pool = None


class TestCheckDuplicateNoPool:
    """Tests when no database pool is configured."""

    @pytest.mark.asyncio
    async def test_returns_not_duplicate_when_pool_is_none(self):
        result = await check_duplicate(content_hash="abc123")

        assert result["is_duplicate"] is False
        assert result["existing_file_id"] is None


class TestCheckDuplicateWithPool:
    """Tests with a mocked database pool."""

    @pytest.mark.asyncio
    @patch("src.tools.duplicate_checker.find_by_content_hash", new_callable=AsyncMock)
    async def test_returns_duplicate_when_hash_exists(self, mock_find):
        existing_id = uuid4()
        mock_find.return_value = {"id": existing_id, "filename": "test.md"}

        fake_pool = AsyncMock()
        dup_mod._db_pool = fake_pool

        result = await check_duplicate(content_hash="deadbeef")

        assert result["is_duplicate"] is True
        assert result["existing_file_id"] == str(existing_id)
        mock_find.assert_called_once_with(fake_pool, "deadbeef")

    @pytest.mark.asyncio
    @patch("src.tools.duplicate_checker.find_by_content_hash", new_callable=AsyncMock)
    async def test_returns_not_duplicate_when_hash_not_found(self, mock_find):
        mock_find.return_value = None

        fake_pool = AsyncMock()
        dup_mod._db_pool = fake_pool

        result = await check_duplicate(content_hash="newcontent")

        assert result["is_duplicate"] is False
        assert result["existing_file_id"] is None
        mock_find.assert_called_once_with(fake_pool, "newcontent")


class TestSetDbPool:
    """Tests for the set_db_pool helper."""

    def test_set_db_pool_updates_module_variable(self):
        fake_pool = AsyncMock()
        set_db_pool(fake_pool)

        assert dup_mod._db_pool is fake_pool
