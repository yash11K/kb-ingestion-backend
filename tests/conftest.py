"""Shared test fixtures and Hypothesis profiles."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import settings as hypothesis_settings

from src.api.router import api_router
from src.config import Settings
from src.services.s3_upload import S3UploadService

# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------
hypothesis_settings.register_profile("ci", max_examples=200)
hypothesis_settings.register_profile("dev", max_examples=100)
hypothesis_settings.load_profile("dev")


# ---------------------------------------------------------------------------
# Configuration fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Return a Settings instance with test-friendly environment variables."""
    env_vars = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/testdb",
        "AWS_REGION": "us-east-1",
        "S3_BUCKET_NAME": "test-bucket",
        "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "AEM_REQUEST_TIMEOUT": "30",
        "AUTO_APPROVE_THRESHOLD": "0.7",
        "AUTO_REJECT_THRESHOLD": "0.2",
        "DENYLIST": '["*/responsivegrid","*/container","*/page"]',
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return Settings()


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_db_pool() -> AsyncMock:
    """Return an AsyncMock standing in for an asyncpg.Pool."""
    pool = AsyncMock()
    pool.acquire = AsyncMock()
    pool.close = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# S3 fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_s3_client() -> MagicMock:
    """Return a MagicMock boto3 S3 client."""
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    return client


@pytest.fixture()
def s3_service(mock_s3_client: MagicMock) -> S3UploadService:
    """Return an S3UploadService wired to the mocked S3 client."""
    return S3UploadService(s3_client=mock_s3_client, bucket_name="test-bucket")


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------
@pytest.fixture()
def test_client(mock_db_pool: AsyncMock, mock_s3_client: MagicMock) -> TestClient:
    """Return a FastAPI TestClient with mocked dependencies on app.state."""
    app = FastAPI()
    app.include_router(api_router)

    app.state.db_pool = mock_db_pool
    app.state.s3_service = S3UploadService(
        s3_client=mock_s3_client, bucket_name="test-bucket"
    )
    app.state.pipeline_service = MagicMock()
    app.state.settings = MagicMock()

    return TestClient(app)
