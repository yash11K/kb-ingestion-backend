"""Review queue API endpoints.

GET  /queue              – paginated list of pending_review files with filters.
GET  /queue/{file_id}    – full QueueItemDetail for a pending_review file.
POST /queue/{file_id}/accept  – approve a pending_review file and trigger S3 upload.
POST /queue/{file_id}/reject  – reject a pending_review file with notes.
PUT  /queue/{file_id}/update  – update md_content and recompute content_hash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from math import ceil
from uuid import UUID

import frontmatter
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.db.queries import get_kb_file, list_review_queue, update_kb_file_status
from src.models.schemas import (
    AcceptRequest,
    FileStatus,
    PaginatedResponse,
    QueueActionResponse,
    QueueItemDetail,
    QueueItemSummary,
    RejectRequest,
    UpdateRequest,
    ValidationBreakdown,
)
from src.tools.md_generator import compute_content_hash

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/queue")
async def list_queue(
    request: Request,
    region: str | None = None,
    brand: str | None = None,
    content_type: str | None = None,
    component_type: str | None = None,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[QueueItemSummary]:
    """Return a paginated list of pending_review files with optional filters."""
    pool = request.app.state.db_pool

    filters: dict[str, str] = {}
    if region is not None:
        filters["region"] = region
    if brand is not None:
        filters["brand"] = brand
    if content_type is not None:
        filters["content_type"] = content_type
    if component_type is not None:
        filters["component_type"] = component_type

    rows, total = await list_review_queue(pool, filters, page, size)

    items = [
        QueueItemSummary(
            id=r["id"],
            filename=r["filename"],
            title=r["title"],
            content_type=r["content_type"],
            component_type=r["component_type"],
            region=r["region"],
            brand=r["brand"],
            validation_score=r["validation_score"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

    pages = ceil(total / size) if size > 0 else 0

    return PaginatedResponse[QueueItemSummary](
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/queue/{file_id}")
async def get_queue_item(file_id: UUID, request: Request) -> QueueItemDetail:
    """Return the full detail for a pending_review file, or 404."""
    pool = request.app.state.db_pool

    record = await get_kb_file(pool, file_id)
    if record is None or record["status"] != FileStatus.PENDING_REVIEW.value:
        raise HTTPException(status_code=404, detail="File not found in review queue")

    return QueueItemDetail(
        id=record["id"],
        filename=record["filename"],
        title=record["title"],
        content_type=record["content_type"],
        component_type=record["component_type"],
        source_url=record["source_url"],
        aem_node_id=record["aem_node_id"],
        md_content=record["md_content"],
        region=record["region"],
        brand=record["brand"],
        validation_score=record["validation_score"],
        validation_breakdown=ValidationBreakdown(**record["validation_breakdown"]) if record["validation_breakdown"] else None,
        validation_issues=record["validation_issues"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


@router.post("/queue/{file_id}/accept")
async def accept_file(
    file_id: UUID,
    body: AcceptRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> QueueActionResponse:
    """Accept a pending_review file: set to approved and trigger S3 upload."""
    pool = request.app.state.db_pool

    record = await get_kb_file(pool, file_id)
    if record is None or record["status"] != FileStatus.PENDING_REVIEW.value:
        raise HTTPException(status_code=404, detail="File not found in review queue")

    now = datetime.now(timezone.utc)
    await update_kb_file_status(
        pool,
        file_id,
        FileStatus.APPROVED.value,
        reviewed_by=body.reviewed_by,
        reviewed_at=now,
    )

    # Trigger S3 upload in background
    s3_service = request.app.state.s3_service
    pipeline_service = request.app.state.pipeline_service
    background_tasks.add_task(_upload_accepted_file, pool, s3_service, file_id, record)

    return QueueActionResponse(
        file_id=file_id,
        status=FileStatus.APPROVED,
        message="File accepted and S3 upload triggered",
    )


@router.post("/queue/{file_id}/reject")
async def reject_file(
    file_id: UUID,
    body: RejectRequest,
    request: Request,
) -> QueueActionResponse:
    """Reject a pending_review file with review notes."""
    pool = request.app.state.db_pool

    record = await get_kb_file(pool, file_id)
    if record is None or record["status"] != FileStatus.PENDING_REVIEW.value:
        raise HTTPException(status_code=404, detail="File not found in review queue")

    now = datetime.now(timezone.utc)
    await update_kb_file_status(
        pool,
        file_id,
        FileStatus.REJECTED.value,
        reviewed_by=body.reviewed_by,
        reviewed_at=now,
        review_notes=body.review_notes,
    )

    return QueueActionResponse(
        file_id=file_id,
        status=FileStatus.REJECTED,
        message="File rejected",
    )


@router.put("/queue/{file_id}/update")
async def update_file(
    file_id: UUID,
    body: UpdateRequest,
    request: Request,
) -> QueueActionResponse:
    """Update md_content, recompute content_hash, preserve current status."""
    pool = request.app.state.db_pool

    record = await get_kb_file(pool, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Extract body from the new md_content (strip frontmatter) and recompute hash
    post = frontmatter.loads(body.md_content)
    new_hash = compute_content_hash(post.content)

    current_status = record["status"]
    await update_kb_file_status(
        pool,
        file_id,
        current_status,
        md_content=body.md_content,
        content_hash=new_hash,
    )

    return QueueActionResponse(
        file_id=file_id,
        status=FileStatus(current_status),
        message="File content updated",
    )


async def _upload_accepted_file(
    pool, s3_service, file_id: UUID, record: dict
) -> None:
    """Background task: upload an accepted file to S3 and update status to in_s3."""
    from src.models.schemas import MarkdownFile

    try:
        md_file = MarkdownFile(
            filename=record["filename"],
            title=record["title"],
            content_type=record["content_type"],
            source_url=record["source_url"],
            component_type=record["component_type"],
            aem_node_id=record["aem_node_id"],
            md_content=record["md_content"],
            md_body="",  # not needed for upload
            content_hash=record["content_hash"],
            modify_date=record["modify_date"] or datetime.now(timezone.utc),
            extracted_at=record.get("extracted_at") or record["created_at"],
            parent_context=record.get("parent_context", ""),
            region=record["region"],
            brand=record["brand"],
        )

        result = await s3_service.upload(md_file, file_id)
        await update_kb_file_status(
            pool,
            file_id,
            status=FileStatus.IN_S3.value,
            s3_bucket=result.s3_bucket,
            s3_key=result.s3_key,
            s3_uploaded_at=result.s3_uploaded_at,
        )
    except Exception:
        logger.error(
            "S3 upload failed for accepted file_id=%s; retaining approved status",
            file_id,
            exc_info=True,
        )
