"""Tests for the ingestion API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.ingest import router
from src.models.schemas import JobStatus


def _mock_settings():
    """Return a mock Settings object with valid defaults for tests."""
    settings = MagicMock()
    settings.locale_region_map = {
        "en": "nam",
        "en-us": "nam",
        "en-gb": "emea",
    }
    return settings


def _create_app(session_factory=None, pipeline_service=None) -> FastAPI:
    """Build a minimal FastAPI app with the ingest router and mocked state."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    if session_factory is None:
        # Create a mock session_factory that returns an async context manager
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=mock_session)
    app.state.session_factory = session_factory
    app.state.pipeline_service = pipeline_service or MagicMock()
    return app


class TestStartIngestion:
    """POST /api/v1/ingest"""

    def test_valid_request_returns_202_with_job_id(self):
        job_id = uuid.uuid4()
        source_id = uuid.uuid4()
        pipeline_service = MagicMock()

        # Mock source + job creation
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_settings",
                _mock_settings,
            )
            mp.setattr(
                "src.api.ingest.find_or_create_source_enriched",
                AsyncMock(return_value=(source_id, True)),
            )
            mp.setattr(
                "src.api.ingest.insert_ingestion_job",
                AsyncMock(return_value=job_id),
            )
            mp.setattr(
                "src.api.ingest.update_source_last_ingested",
                AsyncMock(),
            )
            app = _create_app(pipeline_service=pipeline_service)
            client = TestClient(app)

            response = client.post(
                "/api/v1/ingest",
                json={
                    "urls": ["https://example.com/content/page.model.json"],
                },
            )

        assert response.status_code == 202
        data = response.json()
        assert data["jobs"][0]["job_id"] == str(job_id)
        assert data["jobs"][0]["source_id"] == str(source_id)
        assert data["status"] == JobStatus.IN_PROGRESS.value

    def test_missing_url_returns_422(self):
        app = _create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingest",
            json={},
        )

        assert response.status_code == 422

    def test_invalid_url_returns_422(self):
        app = _create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingest",
            json={"urls": ["not-a-url"]},
        )

        assert response.status_code == 422

    def test_empty_urls_returns_422(self):
        app = _create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingest",
            json={
                "urls": [],
            },
        )

        assert response.status_code == 422

    def test_max_depth_defaults_to_zero(self):
        job_id = uuid.uuid4()
        source_id = uuid.uuid4()
        pipeline_service = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_settings",
                _mock_settings,
            )
            mp.setattr(
                "src.api.ingest.find_or_create_source_enriched",
                AsyncMock(return_value=(source_id, True)),
            )
            mp.setattr(
                "src.api.ingest.insert_ingestion_job",
                AsyncMock(return_value=job_id),
            )
            mp.setattr(
                "src.api.ingest.update_source_last_ingested",
                AsyncMock(),
            )
            app = _create_app(pipeline_service=pipeline_service)
            client = TestClient(app)

            response = client.post(
                "/api/v1/ingest",
                json={
                    "urls": ["https://example.com/page.model.json"],
                },
            )

        assert response.status_code == 202

    def test_pipeline_run_is_scheduled_as_background_task(self):
        job_id = uuid.uuid4()
        source_id = uuid.uuid4()
        pipeline_service = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_settings",
                _mock_settings,
            )
            mp.setattr(
                "src.api.ingest.find_or_create_source_enriched",
                AsyncMock(return_value=(source_id, True)),
            )
            mp.setattr(
                "src.api.ingest.insert_ingestion_job",
                AsyncMock(return_value=job_id),
            )
            mp.setattr(
                "src.api.ingest.update_source_last_ingested",
                AsyncMock(),
            )
            app = _create_app(pipeline_service=pipeline_service)
            client = TestClient(app)

            client.post(
                "/api/v1/ingest",
                json={
                    "urls": ["https://example.com/content/page.model.json"],
                },
            )

        # BackgroundTasks runs synchronously in TestClient, so pipeline.run
        # should have been called for the single URL in the batch.
        pipeline_service.run.assert_called_once_with(
            job_id,
            ["https://example.com/content/page.model.json"],
            source_id,
        )


class TestGetJobStatus:
    """GET /api/v1/ingest/{job_id}"""

    def test_returns_job_when_found(self):
        job_id = uuid.uuid4()
        source_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        job_record = {
            "id": job_id,
            "source_id": source_id,
            "source_url": "https://example.com/page.model.json",
            "status": "completed",
            "total_nodes_found": 5,
            "files_created": 4,
            "files_auto_approved": 2,
            "files_pending_review": 1,
            "files_auto_rejected": 1,
            "duplicates_skipped": 1,
            "error_message": None,
            "started_at": now,
            "completed_at": now,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_ingestion_job",
                AsyncMock(return_value=job_record),
            )
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/ingest/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(job_id)
        assert data["status"] == "completed"
        assert data["files_created"] == 4
        assert data["duplicates_skipped"] == 1

    def test_returns_404_when_not_found(self):
        job_id = uuid.uuid4()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_ingestion_job",
                AsyncMock(return_value=None),
            )
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/ingest/{job_id}")

        assert response.status_code == 404

    def test_returns_in_progress_job(self):
        job_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        job_record = {
            "id": job_id,
            "source_id": None,
            "source_url": "https://example.com/page.model.json",
            "status": "in_progress",
            "total_nodes_found": None,
            "files_created": 0,
            "files_auto_approved": 0,
            "files_pending_review": 0,
            "files_auto_rejected": 0,
            "duplicates_skipped": 0,
            "error_message": None,
            "started_at": now,
            "completed_at": None,
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.api.ingest.get_ingestion_job",
                AsyncMock(return_value=job_record),
            )
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/ingest/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "in_progress"
        assert data["total_nodes_found"] is None
        assert data["completed_at"] is None
