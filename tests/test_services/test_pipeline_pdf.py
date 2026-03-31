"""Unit tests for PipelineService._process_pdf_link."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.models.schemas import S3UploadResult
from src.services.pipeline import PipelineService
from src.services.stream_manager import StreamManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline_service(
    session_factory=None,
    s3_service=None,
    settings=None,
    stream_manager=None,
) -> PipelineService:
    """Build a PipelineService with mocked collaborators."""
    discovery = MagicMock()
    extractor = MagicMock()
    validator = MagicMock()

    if session_factory is None:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=mock_session)
        session_factory._mock_session = mock_session

    if s3_service is None:
        s3_service = AsyncMock()
        s3_service.upload_pdf = AsyncMock(return_value=S3UploadResult(
            s3_bucket="test-bucket",
            s3_key="brand/region/ns/abcd1234_report.pdf",
            s3_uploaded_at=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        ))

    if settings is None:
        settings = MagicMock()
        settings.aem_request_timeout = 30
        settings.max_concurrent_jobs = 3

    if stream_manager is None:
        stream_manager = MagicMock(spec=StreamManager)

    return PipelineService(
        discovery=discovery,
        extractor=extractor,
        validator=validator,
        session_factory=session_factory,
        s3_service=s3_service,
        settings=settings,
        stream_manager=stream_manager,
    )


PDF_BYTES = b"%PDF-1.4 fake pdf content for testing"
PDF_HASH = hashlib.sha256(PDF_BYTES).hexdigest()
FAKE_FILE_ID = uuid.uuid4()


def _mock_httpx_get_success(url, timeout=None):
    """Return a fake httpx.Response with PDF bytes."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.content = PDF_BYTES
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=_mock_httpx_get_success)
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock, return_value=FAKE_FILE_ID)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_happy_path(mock_update, mock_insert, mock_get):
    """Full success path: download → hash → insert → upload → update → SSE."""
    svc = _make_pipeline_service()
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    await svc._process_pdf_link(
        url="https://example.com/docs/report.pdf",
        brand="TestBrand",
        region="US",
        namespace="docs",
        job_id=job_id,
        source_id=source_id,
    )

    # HTTP GET was called
    mock_get.assert_called_once_with(
        "https://example.com/docs/report.pdf", timeout=30,
    )

    # kb_files record inserted with correct fields
    mock_insert.assert_called_once()
    file_dict = mock_insert.call_args[0][1]
    assert file_dict["file_type"] == "pdf"
    assert file_dict["status"] == "approved"
    assert file_dict["content_hash"] == PDF_HASH
    assert file_dict["filename"] == f"{PDF_HASH[:8]}_report.pdf"
    assert file_dict["source_url"] == "https://example.com/docs/report.pdf"
    assert file_dict["brand"] == "TestBrand"
    assert file_dict["region"] == "US"
    assert file_dict["namespace"] == "docs"
    assert file_dict["md_content"] is None
    assert file_dict["title"] is None
    assert file_dict["validation_score"] is None

    # S3 upload called
    svc.s3_service.upload_pdf.assert_called_once_with(
        pdf_bytes=PDF_BYTES,
        filename=f"{PDF_HASH[:8]}_report.pdf",
        brand="TestBrand",
        region="US",
        namespace="docs",
        file_id=FAKE_FILE_ID,
        content_hash=PDF_HASH,
    )

    # kb_files updated with S3 metadata
    mock_update.assert_called_once()

    # SSE events emitted: pdf_download and pdf_upload_complete
    sm = svc.stream_manager
    calls = sm.publish.call_args_list
    stages = [c[0][2]["stage"] for c in calls if c[0][1] == "progress"]
    assert "pdf_download" in stages
    assert "pdf_upload_complete" in stages


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=httpx.HTTPError("Connection refused"))
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_download_error_emits_sse_and_returns(
    mock_update, mock_insert, mock_get,
):
    """On HTTP error: log, emit pdf_download_error SSE, no DB insert, no raise."""
    svc = _make_pipeline_service()
    job_id = uuid.uuid4()

    # Should NOT raise
    await svc._process_pdf_link(
        url="https://example.com/broken.pdf",
        brand="b",
        region="r",
        namespace="n",
        job_id=job_id,
        source_id=None,
    )

    # No DB insert or update
    mock_insert.assert_not_called()
    mock_update.assert_not_called()

    # SSE: pdf_download then pdf_download_error
    sm = svc.stream_manager
    calls = sm.publish.call_args_list
    stages = [c[0][2]["stage"] for c in calls if c[0][1] == "progress"]
    assert "pdf_download" in stages
    assert "pdf_download_error" in stages


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=_mock_httpx_get_success)
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock, return_value=FAKE_FILE_ID)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_s3_failure_emits_error_sse(
    mock_update, mock_insert, mock_get,
):
    """When S3 upload fails, the error is caught and pdf_download_error SSE emitted."""
    s3_service = AsyncMock()
    s3_service.upload_pdf = AsyncMock(side_effect=RuntimeError("S3 is down"))
    svc = _make_pipeline_service(s3_service=s3_service)
    job_id = uuid.uuid4()

    await svc._process_pdf_link(
        url="https://example.com/doc.pdf",
        brand="b",
        region="r",
        namespace="n",
        job_id=job_id,
        source_id=None,
    )

    # DB insert happened (before S3 upload)
    mock_insert.assert_called_once()

    # No DB update (S3 failed before we could update)
    mock_update.assert_not_called()

    # SSE includes pdf_download_error
    sm = svc.stream_manager
    calls = sm.publish.call_args_list
    stages = [c[0][2]["stage"] for c in calls if c[0][1] == "progress"]
    assert "pdf_download_error" in stages


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=_mock_httpx_get_success)
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock, return_value=FAKE_FILE_ID)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_filename_from_url(mock_update, mock_insert, mock_get):
    """Filename is built as {hash[:8]}_{stem}.pdf from the URL path."""
    svc = _make_pipeline_service()

    await svc._process_pdf_link(
        url="https://cdn.example.com/assets/my-policy-document.pdf",
        brand="b",
        region="r",
        namespace="n",
        job_id=uuid.uuid4(),
        source_id=None,
    )

    file_dict = mock_insert.call_args[0][1]
    assert file_dict["filename"].endswith("_my-policy-document.pdf")
    assert file_dict["filename"].startswith(PDF_HASH[:8])


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=_mock_httpx_get_success)
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock, return_value=FAKE_FILE_ID)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_url_with_query_params(mock_update, mock_insert, mock_get):
    """Query params in URL are stripped when extracting the original filename."""
    svc = _make_pipeline_service()

    await svc._process_pdf_link(
        url="https://example.com/docs/guide.pdf?v=2&token=abc",
        brand="b",
        region="r",
        namespace="n",
        job_id=uuid.uuid4(),
        source_id=None,
    )

    file_dict = mock_insert.call_args[0][1]
    # urlparse strips query params; PurePosixPath gives stem="guide"
    assert file_dict["filename"] == f"{PDF_HASH[:8]}_guide.pdf"


@pytest.mark.asyncio
@patch("src.services.pipeline.httpx.get", side_effect=_mock_httpx_get_success)
@patch("src.services.pipeline.insert_kb_file", new_callable=AsyncMock, return_value=FAKE_FILE_ID)
@patch("src.services.pipeline.update_kb_file_status", new_callable=AsyncMock)
async def test_process_pdf_link_update_has_s3_metadata(mock_update, mock_insert, mock_get):
    """After S3 upload, the kb_files record is updated with S3 bucket/key/timestamp."""
    svc = _make_pipeline_service()

    await svc._process_pdf_link(
        url="https://example.com/file.pdf",
        brand="b",
        region="r",
        namespace="n",
        job_id=uuid.uuid4(),
        source_id=None,
    )

    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args
    assert call_kwargs[1]["s3_bucket"] == "test-bucket"
    assert call_kwargs[1]["s3_key"] == "brand/region/ns/abcd1234_report.pdf"
    assert call_kwargs[1]["s3_uploaded_at"] is not None


# ---------------------------------------------------------------------------
# Branching logic tests (Task 4.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.services.pipeline.update_ingestion_job", new_callable=AsyncMock)
async def test_run_pipeline_routes_pdf_url_to_process_pdf_link(mock_update_job):
    """When a URL is a PDF, _run_pipeline calls _process_pdf_link instead of _process_single_url."""
    svc = _make_pipeline_service()
    svc._process_pdf_link = AsyncMock()
    svc._process_single_url = AsyncMock(return_value=({"files_created": 1}, 0))

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    await svc._run_pipeline(
        job_id=job_id,
        urls=["https://example.com/docs/report.pdf"],
        source_id=source_id,
    )

    svc._process_pdf_link.assert_called_once_with(
        "https://example.com/docs/report.pdf",
        "example",  # infer_brand result
        "unknown",  # infer_region result (no locale map match)
        "general",  # infer_namespace result (no namespace match)
        job_id,
        source_id,
    )
    svc._process_single_url.assert_not_called()


@pytest.mark.asyncio
@patch("src.services.pipeline.update_ingestion_job", new_callable=AsyncMock)
async def test_run_pipeline_routes_non_pdf_url_to_process_single_url(mock_update_job):
    """When a URL is NOT a PDF, _run_pipeline calls _process_single_url."""
    svc = _make_pipeline_service()
    svc._process_pdf_link = AsyncMock()
    svc._process_single_url = AsyncMock(return_value=({"files_created": 1}, 0))

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    await svc._run_pipeline(
        job_id=job_id,
        urls=["https://example.com/en/products.model.json"],
        source_id=source_id,
    )

    svc._process_single_url.assert_called_once()
    svc._process_pdf_link.assert_not_called()


@pytest.mark.asyncio
@patch("src.services.pipeline.update_ingestion_job", new_callable=AsyncMock)
async def test_run_pipeline_mixed_urls_routes_correctly(mock_update_job):
    """A mix of PDF and non-PDF URLs are routed to the correct handlers."""
    svc = _make_pipeline_service()
    svc._process_pdf_link = AsyncMock()
    svc._process_single_url = AsyncMock(return_value=({"files_created": 1}, 2))

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    await svc._run_pipeline(
        job_id=job_id,
        urls=[
            "https://example.com/docs/guide.pdf",
            "https://example.com/en/products.model.json",
            "https://example.com/assets/policy.PDF",
        ],
        source_id=source_id,
    )

    assert svc._process_pdf_link.call_count == 2
    assert svc._process_single_url.call_count == 1

    # Verify the PDF URLs went to _process_pdf_link
    pdf_urls = [call[0][0] for call in svc._process_pdf_link.call_args_list]
    assert "https://example.com/docs/guide.pdf" in pdf_urls
    assert "https://example.com/assets/policy.PDF" in pdf_urls

    # Verify the non-PDF URL went to _process_single_url
    non_pdf_url = svc._process_single_url.call_args[0][0]
    assert non_pdf_url == "https://example.com/en/products.model.json"
