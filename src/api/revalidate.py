"""Revalidation API endpoints.

POST /files/{file_id}/revalidate – synchronous single-file revalidation.
POST /revalidate                 – start a batch revalidation job (returns 202).
GET  /revalidate/{job_id}        – retrieve batch job status.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.db.queries import get_revalidation_job, insert_revalidation_job
from src.models.schemas import (
    FileDetail,
    FileStatus,
    JobStatus,
    RevalidateRequest,
    RevalidateResponse,
    RevalidationJobResponse,
    ValidationBreakdown,
)

router = APIRouter()


@router.post("/files/{file_id}/revalidate")
async def revalidate_single_file(file_id: UUID, request: Request) -> FileDetail:
    """Re-run validation on a single KB file and return the updated detail."""
    revalidation_service = request.app.state.revalidation_service

    try:
        record = await revalidation_service.revalidate_single(file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except RuntimeError:
        raise HTTPException(status_code=502, detail="Validation service unavailable")

    # Build FileDetail from the updated record (same pattern as GET /files/{file_id})
    breakdown = None
    if record.get("validation_breakdown") is not None:
        breakdown = ValidationBreakdown(**record["validation_breakdown"])

    return FileDetail(
        id=record["id"],
        filename=record["filename"],
        title=record["title"],
        content_type=record["content_type"],
        content_hash=record["content_hash"],
        source_url=record["source_url"],
        component_type=record["component_type"],
        source_id=record.get("source_id"),
        aem_node_id=record["aem_node_id"],
        md_content=record["md_content"],
        modify_date=record["modify_date"],
        parent_context=record["parent_context"],
        region=record["region"],
        brand=record["brand"],
        doc_type=record.get("doc_type"),
        validation_score=record.get("validation_score"),
        validation_breakdown=breakdown,
        validation_issues=record.get("validation_issues"),
        status=FileStatus(record["status"]),
        s3_bucket=record.get("s3_bucket"),
        s3_key=record.get("s3_key"),
        s3_uploaded_at=record.get("s3_uploaded_at"),
        reviewed_by=record.get("reviewed_by"),
        reviewed_at=record.get("reviewed_at"),
        review_notes=record.get("review_notes"),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


@router.post("/revalidate", status_code=202)
async def start_batch_revalidation(
    body: RevalidateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> RevalidateResponse:
    """Accept a batch revalidation request and process in the background."""
    revalidation_service = request.app.state.revalidation_service

    async with request.app.state.session_factory() as session:
        job_id = await insert_revalidation_job(session, len(body.file_ids))
        await session.commit()

    background_tasks.add_task(
        revalidation_service.revalidate_batch, job_id, body.file_ids
    )

    return RevalidateResponse(job_id=job_id, status=JobStatus.IN_PROGRESS)


@router.get("/revalidate/{job_id}")
async def get_revalidation_job_status(
    job_id: UUID, request: Request
) -> RevalidationJobResponse:
    """Return the full revalidation job record, or 404 if not found."""
    async with request.app.state.session_factory() as session:
        job = await get_revalidation_job(session, job_id)
        await session.commit()

    if job is None:
        raise HTTPException(status_code=404, detail="Revalidation job not found")

    return RevalidationJobResponse(**job)
