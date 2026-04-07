"""Tests for check_uniqueness tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.uniqueness_checker import check_uniqueness, set_settings
import src.tools.uniqueness_checker as uniq_mod


@pytest.fixture(autouse=True)
def _reset_settings():
    """Reset the module-level settings before and after each test."""
    uniq_mod._settings = None
    yield
    uniq_mod._settings = None


class TestCheckUniquenessNoSettings:
    """Tests when no settings are configured."""

    @pytest.mark.asyncio
    async def test_returns_kb_unavailable_when_settings_is_none(self):
        result = await check_uniqueness(
            document_content="Some content", source_url="https://example.com"
        )

        assert result["kb_available"] is False
        assert result["similar_documents"] == []


class TestCheckUniquenessNoBedrockKB:
    """Tests when settings exist but no Bedrock KB ID is configured."""

    @pytest.mark.asyncio
    async def test_returns_kb_unavailable_when_kb_id_empty(self):
        mock_settings = MagicMock()
        mock_settings.bedrock_kb_id = ""
        uniq_mod._settings = mock_settings

        result = await check_uniqueness(
            document_content="Some content", source_url="https://example.com"
        )

        assert result["kb_available"] is False
        assert result["similar_documents"] == []


class TestCheckUniquenessWithBedrockKB:
    """Tests with a mocked Bedrock KB."""

    @pytest.mark.asyncio
    @patch("src.tools.uniqueness_checker.boto3")
    @patch("src.tools.uniqueness_checker.asyncio")
    async def test_returns_similar_documents(self, mock_asyncio, mock_boto3):
        mock_settings = MagicMock()
        mock_settings.bedrock_kb_id = "kb-123"
        mock_settings.aws_region = "us-east-1"
        uniq_mod._settings = mock_settings

        mock_asyncio.to_thread = AsyncMock(return_value={
            "retrievalResults": [
                {
                    "content": {"text": "Similar content here"},
                    "location": {"s3Location": {"uri": "s3://bucket/key"}},
                    "score": 0.85,
                    "metadata": {"title": "Existing Doc", "source_url": "https://other.com"},
                }
            ]
        })

        result = await check_uniqueness(
            document_content="Test content", source_url="https://example.com"
        )

        assert result["kb_available"] is True
        assert len(result["similar_documents"]) == 1
        assert result["similar_documents"][0]["similarity_score"] == 0.85

    @pytest.mark.asyncio
    @patch("src.tools.uniqueness_checker.boto3")
    @patch("src.tools.uniqueness_checker.asyncio")
    async def test_excludes_self_matches(self, mock_asyncio, mock_boto3):
        mock_settings = MagicMock()
        mock_settings.bedrock_kb_id = "kb-123"
        mock_settings.aws_region = "us-east-1"
        uniq_mod._settings = mock_settings

        mock_asyncio.to_thread = AsyncMock(return_value={
            "retrievalResults": [
                {
                    "content": {"text": "Same doc content"},
                    "location": {"s3Location": {"uri": "s3://bucket/self"}},
                    "score": 0.99,
                    "metadata": {"source_url": "https://example.com"},
                },
                {
                    "content": {"text": "Different doc"},
                    "location": {"s3Location": {"uri": "s3://bucket/other"}},
                    "score": 0.70,
                    "metadata": {"source_url": "https://other.com"},
                },
            ]
        })

        result = await check_uniqueness(
            document_content="Test content", source_url="https://example.com"
        )

        assert result["kb_available"] is True
        assert len(result["similar_documents"]) == 1
        assert result["similar_documents"][0]["source_url"] == "https://other.com"


class TestSetSettings:
    """Tests for the set_settings helper."""

    def test_set_settings_updates_module_variable(self):
        mock_settings = MagicMock()
        set_settings(mock_settings)

        assert uniq_mod._settings is mock_settings
