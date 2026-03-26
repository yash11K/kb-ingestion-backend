"""Tests for check_duplicate tool."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.tools.duplicate_checker import check_duplicate, set_session_factory
import src.tools.duplicate_checker as dup_mod


@pytest.fixture(autouse=True)
def _reset_session_factory():
    """Reset the module-level session factory before and after each test."""
    dup_mod._session_factory = None
    yield
    dup_mod._session_factory = None


class TestCheckDuplicateNoSessionFactory:
    """Tests when no session factory is configured."""

    @pytest.mark.asyncio
    async def test_returns_not_duplicate_when_session_factory_is_none(self):
        result = await check_duplicate(content_hash="abc123")

        assert result["is_duplicate"] is False
        assert result["existing_file_id"] is None


class TestCheckDuplicateWithSessionFactory:
    """Tests with a mocked session factory."""

    @pytest.mark.asyncio
    @patch("src.tools.duplicate_checker.find_by_content_hash", new_callable=AsyncMock)
    async def test_returns_not_duplicate_stub(self, mock_find):
        """The tool currently always returns not duplicate (TODO stub)."""
        fake_factory = AsyncMock()
        dup_mod._session_factory = fake_factory

        result = await check_duplicate(content_hash="deadbeef")

        assert result["is_duplicate"] is False
        assert result["existing_file_id"] is None


class TestSetSessionFactory:
    """Tests for the set_session_factory helper."""

    def test_set_session_factory_updates_module_variable(self):
        fake_factory = AsyncMock()
        set_session_factory(fake_factory)

        assert dup_mod._session_factory is fake_factory
