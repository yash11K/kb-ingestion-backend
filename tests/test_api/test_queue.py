"""Tests for the review queue API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.queue import router
from src.models.schemas import FileStatus


def _create_app(session_factory=None, s3_service=None, pipeline_service=None) -> FastAPI:
    """Build a minimal FastAPI app with the queue router and mocked state."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    if session_factory is None:
        # Create a mock session_factory that returns an async context manager
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=mock_session)
    app.state.session_factory = session_factory
    app.state.s3_service = s3_service or MagicMock()
    app.state.pipeline_service = pipeline_service or MagicMock()
    return app


_NOW = datetime.now(timezone.utc)


def _make_pending_review_record(**overrides) -> dict:
    """Return a realistic kb_file dict with status pending_review."""
    defaults = {
        "id": uuid.uuid4(),
        "filename": "test-file.md",
        "title": "Test File",
        "content_type": "faq",
        "content_hash": "abc123hash",
        "source_url": "https://example.com/page.model.json",
        "component_type": "text",
        "aem_node_id": "/root/items/text_1",
        "md_content": "---\ntitle: Test\n---\nBody content",
        "modify_date": _NOW,
        "parent_context": "/root/items",
        "region": "US",
        "brand": "TestBrand",
        "validation_score": 0.55,
        "validation_breakdown": {
            "metadata_completeness": 0.25,
            "semantic_quality": 0.2,
            "uniqueness": 0.1,
        },
        "validation_issues": ["Minor formatting issue"],
        "status": "pending_review",
        "s3_bucket": None,
        "s3_key": None,
        "s3_uploaded_at": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return defaults


class TestListQueue:
    """GET /api/v1/queue"""

    def test_returns_paginated_pending_review_files(self):
        record = _make_pending_review_record()
        mock_list = AsyncMock(return_value=([record], 1))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.list_review_queue", mock_list)
            app = _create_app()
            client = TestClient(app)

            response = client.get("/api/v1/queue")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["size"] == 20
        assert data["pages"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(record["id"])
        assert data["items"][0]["validation_score"] == 0.55

    def test_passes_filters_to_query(self):
        mock_list = AsyncMock(return_value=([], 0))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.list_review_queue", mock_list)
            app = _create_app()
            client = TestClient(app)

            client.get(
                "/api/v1/queue",
                params={
                    "region": "EU",
                    "brand": "Acme",
                    "content_type": "faq",
                    "component_type": "text",
                    "page": 2,
                    "size": 10,
                },
            )

        mock_list.assert_called_once()
        call_args = mock_list.call_args
        filters = call_args[0][1]
        assert filters == {
            "region": "EU",
            "brand": "Acme",
            "content_type": "faq",
            "component_type": "text",
        }
        assert call_args[0][2] == 2  # page
        assert call_args[0][3] == 10  # size

    def test_empty_queue_returns_empty_list(self):
        mock_list = AsyncMock(return_value=([], 0))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.list_review_queue", mock_list)
            app = _create_app()
            client = TestClient(app)

            response = client.get("/api/v1/queue")

        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["pages"] == 0

    def test_pagination_computes_pages_correctly(self):
        records = [_make_pending_review_record() for _ in range(5)]
        mock_list = AsyncMock(return_value=(records, 23))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.list_review_queue", mock_list)
            app = _create_app()
            client = TestClient(app)

            response = client.get("/api/v1/queue", params={"size": 5})

        data = response.json()
        assert data["total"] == 23
        assert data["pages"] == 5  # ceil(23/5)


class TestGetQueueItem:
    """GET /api/v1/queue/{file_id}"""

    def test_returns_detail_for_pending_review_file(self):
        record = _make_pending_review_record()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/queue/{record['id']}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(record["id"])
        assert data["md_content"] == record["md_content"]
        assert data["validation_score"] == 0.55
        assert data["validation_breakdown"]["metadata_completeness"] == 0.25

    def test_returns_404_when_not_found(self):
        file_id = uuid.uuid4()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=None))
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/queue/{file_id}")

        assert response.status_code == 404

    def test_returns_404_when_not_pending_review(self):
        record = _make_pending_review_record(status="approved")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            app = _create_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/queue/{record['id']}")

        assert response.status_code == 404


class TestAcceptFile:
    """POST /api/v1/queue/{file_id}/accept"""

    def test_accept_sets_status_to_approved(self):
        record = _make_pending_review_record()
        mock_update = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            mp.setattr("src.api.queue.update_kb_file_status", mock_update)
            app = _create_app()
            client = TestClient(app)

            response = client.post(
                f"/api/v1/queue/{record['id']}/accept",
                json={"reviewed_by": "reviewer@example.com"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["message"] == "File accepted and S3 upload triggered"

        # Verify update was called with correct status and reviewer
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[0][1] == record["id"]
        assert call_kwargs[0][2] == "approved"
        assert call_kwargs[1]["reviewed_by"] == "reviewer@example.com"
        assert "reviewed_at" in call_kwargs[1]

    def test_accept_returns_404_when_not_found(self):
        file_id = uuid.uuid4()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=None))
            app = _create_app()
            client = TestClient(app)

            response = client.post(
                f"/api/v1/queue/{file_id}/accept",
                json={"reviewed_by": "reviewer@example.com"},
            )

        assert response.status_code == 404

    def test_accept_returns_404_when_not_pending_review(self):
        record = _make_pending_review_record(status="approved")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            app = _create_app()
            client = TestClient(app)

            response = client.post(
                f"/api/v1/queue/{record['id']}/accept",
                json={"reviewed_by": "reviewer@example.com"},
            )

        assert response.status_code == 404


class TestRejectFile:
    """POST /api/v1/queue/{file_id}/reject"""

    def test_reject_sets_status_to_rejected_with_notes(self):
        record = _make_pending_review_record()
        mock_update = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            mp.setattr("src.api.queue.update_kb_file_status", mock_update)
            app = _create_app()
            client = TestClient(app)

            response = client.post(
                f"/api/v1/queue/{record['id']}/reject",
                json={
                    "reviewed_by": "reviewer@example.com",
                    "review_notes": "Content is too short",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["message"] == "File rejected"

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[0][2] == "rejected"
        assert call_kwargs[1]["reviewed_by"] == "reviewer@example.com"
        assert call_kwargs[1]["review_notes"] == "Content is too short"
        assert "reviewed_at" in call_kwargs[1]

    def test_reject_returns_404_when_not_found(self):
        file_id = uuid.uuid4()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=None))
            app = _create_app()
            client = TestClient(app)

            response = client.post(
                f"/api/v1/queue/{file_id}/reject",
                json={
                    "reviewed_by": "reviewer@example.com",
                    "review_notes": "Bad content",
                },
            )

        assert response.status_code == 404

    def test_reject_returns_422_when_missing_review_notes(self):
        app = _create_app()
        client = TestClient(app)

        response = client.post(
            f"/api/v1/queue/{uuid.uuid4()}/reject",
            json={"reviewed_by": "reviewer@example.com"},
        )

        assert response.status_code == 422


class TestUpdateFile:
    """PUT /api/v1/queue/{file_id}/update"""

    def test_update_recomputes_hash_and_preserves_status(self):
        record = _make_pending_review_record()
        mock_update = AsyncMock()

        new_content = "---\ntitle: Updated\n---\nNew body content"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            mp.setattr("src.api.queue.update_kb_file_status", mock_update)
            app = _create_app()
            client = TestClient(app)

            response = client.put(
                f"/api/v1/queue/{record['id']}/update",
                json={"md_content": new_content},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_review"
        assert data["message"] == "File content updated"

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        # Status should be preserved
        assert call_kwargs[0][2] == "pending_review"
        # md_content and content_hash should be updated
        assert call_kwargs[1]["md_content"] == new_content
        assert "content_hash" in call_kwargs[1]
        # Hash should be computed from body only (not frontmatter)
        assert len(call_kwargs[1]["content_hash"]) == 64  # SHA-256 hex

    def test_update_returns_404_when_not_found(self):
        file_id = uuid.uuid4()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=None))
            app = _create_app()
            client = TestClient(app)

            response = client.put(
                f"/api/v1/queue/{file_id}/update",
                json={"md_content": "---\ntitle: X\n---\nBody"},
            )

        assert response.status_code == 404

    def test_update_hash_changes_with_different_body(self):
        """Verify that different body content produces different hashes."""
        record = _make_pending_review_record()
        hashes = []

        for body in ["Body A", "Body B"]:
            mock_update = AsyncMock()
            content = f"---\ntitle: Test\n---\n{body}"

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
                mp.setattr("src.api.queue.update_kb_file_status", mock_update)
                app = _create_app()
                client = TestClient(app)

                client.put(
                    f"/api/v1/queue/{record['id']}/update",
                    json={"md_content": content},
                )

            hashes.append(mock_update.call_args[1]["content_hash"])

        assert hashes[0] != hashes[1]

    def test_update_preserves_non_pending_review_status(self):
        """Update should work on files with any status, preserving it."""
        record = _make_pending_review_record(status="approved")
        mock_update = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.queue.get_kb_file", AsyncMock(return_value=record))
            mp.setattr("src.api.queue.update_kb_file_status", mock_update)
            app = _create_app()
            client = TestClient(app)

            response = client.put(
                f"/api/v1/queue/{record['id']}/update",
                json={"md_content": "---\ntitle: X\n---\nBody"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
