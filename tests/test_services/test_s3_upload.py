"""Unit tests for S3UploadService."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.models.schemas import MarkdownFile, S3UploadResult
from src.services.s3_upload import S3UploadService


def _make_markdown_file(**overrides) -> MarkdownFile:
    defaults = dict(
        filename="test-file.md",
        title="Test File",
        content_type="faq",
        source_url="https://example.com/content.model.json",
        component_type="text",
        key="/root/text1",
        namespace="general",
        md_content="---\ntitle: Test\n---\nHello world",
        md_body="Hello world",
        content_hash="abc123hash",
        extracted_at=datetime(2025, 3, 20, 10, 30, tzinfo=timezone.utc),
        parent_context="/root",
        region="US",
        brand="TestBrand",
    )
    defaults.update(overrides)
    return MarkdownFile(**defaults)


@pytest.fixture
def s3_client():
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    return client


@pytest.fixture
def service(s3_client):
    return S3UploadService(s3_client=s3_client, bucket_name="my-test-bucket")


@pytest.mark.asyncio
async def test_upload_returns_s3_upload_result(service):
    file = _make_markdown_file()
    file_id = uuid.uuid4()

    result = await service.upload(file, file_id)

    assert isinstance(result, S3UploadResult)
    assert result.s3_bucket == "my-test-bucket"
    assert result.s3_key == "TestBrand/US/general/test-file.md"
    assert result.s3_uploaded_at is not None


@pytest.mark.asyncio
async def test_upload_calls_put_object_with_correct_params(service, s3_client):
    file = _make_markdown_file()
    file_id = uuid.uuid4()

    await service.upload(file, file_id)

    s3_client.put_object.assert_called_once_with(
        Bucket="my-test-bucket",
        Key="TestBrand/US/general/test-file.md",
        Body=file.md_content.encode("utf-8"),
        ContentType="text/markdown",
        Metadata={
            "file_id": str(file_id),
            "content_hash": "abc123hash",
        },
    )


@pytest.mark.asyncio
async def test_upload_key_uses_brand_region_namespace_path(service, s3_client):
    file = _make_markdown_file(
        filename="my-policy.md",
        brand="budget",
        region="emea",
        namespace="faq",
    )
    file_id = uuid.uuid4()

    result = await service.upload(file, file_id)

    assert result.s3_key == "budget/emea/faq/my-policy.md"


@pytest.mark.asyncio
async def test_upload_failure_logs_and_reraises(service, s3_client):
    s3_client.put_object.side_effect = RuntimeError("S3 is down")
    file = _make_markdown_file()
    file_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="S3 is down"):
        await service.upload(file, file_id)


@pytest.mark.asyncio
async def test_upload_sets_content_type_to_text_markdown(service, s3_client):
    file = _make_markdown_file()
    file_id = uuid.uuid4()

    await service.upload(file, file_id)

    call_kwargs = s3_client.put_object.call_args
    assert call_kwargs.kwargs["ContentType"] == "text/markdown"


@pytest.mark.asyncio
async def test_upload_metadata_includes_file_id_and_content_hash(service, s3_client):
    file = _make_markdown_file(content_hash="sha256deadbeef")
    file_id = uuid.uuid4()

    await service.upload(file, file_id)

    call_kwargs = s3_client.put_object.call_args
    assert call_kwargs.kwargs["Metadata"]["file_id"] == str(file_id)
    assert call_kwargs.kwargs["Metadata"]["content_hash"] == "sha256deadbeef"


@pytest.mark.asyncio
async def test_uploaded_at_is_utc(service):
    file = _make_markdown_file()
    file_id = uuid.uuid4()

    result = await service.upload(file, file_id)

    assert result.s3_uploaded_at.tzinfo is not None
