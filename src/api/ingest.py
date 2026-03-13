"""Ingestion API endpoints.

POST /ingest              – start a new ingestion job (returns 202).
GET  /ingest/{job_id}     – retrieve job status and counters.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.db.queries import (
    find_or_create_source,
    get_ingestion_job,
    insert_ingestion_job,
    list_ingestion_jobs,
    update_source_last_ingested,
)
from src.models.schemas import (
    IngestionJobResponse,
    IngestRequest,
    IngestResponse,
    JobStatus,
    PaginatedResponse,
)

router = APIRouter()


@router.post("/ingest", status_code=202)
async def start_ingestion(
    body: IngestRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> IngestResponse:
    """Validate an ingest request, find-or-create the source, create a job,
    and launch the pipeline."""
    db_pool = request.app.state.db_pool
    pipeline_service = request.app.state.pipeline_service

    # Brand and region will be inferred from URL inside pipeline.run();
    # use placeholders for source lookup only.
    region = "unknown"
    brand = "unknown"

    # Find or create the source
    source_id, _created = await find_or_create_source(
        db_pool, str(body.url), region, brand
    )

    # Create the ingestion job linked to the source
    job_id = await insert_ingestion_job(db_pool, str(body.url), source_id)

    # Touch last_ingested_at
    await update_source_last_ingested(db_pool, source_id)

    # Launch the pipeline as a background task
    background_tasks.add_task(
        pipeline_service.run, job_id, str(body.url),
        body.max_depth, body.confirmed_urls,
        source_id,
    )

    return IngestResponse(
        source_id=source_id, job_id=job_id, status=JobStatus.IN_PROGRESS
    )


@router.get("/jobs")
async def list_jobs(
    request: Request,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[IngestionJobResponse]:
    """Return a paginated list of ingestion jobs."""
    from math import ceil

    db_pool = request.app.state.db_pool
    rows, total = await list_ingestion_jobs(db_pool, page, size)
    items = [IngestionJobResponse(**r) for r in rows]
    pages = ceil(total / size) if size > 0 else 0

    return PaginatedResponse[IngestionJobResponse](
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/ingest/{job_id}")
async def get_job_status(job_id: UUID, request: Request) -> IngestionJobResponse:
    """Return the full ingestion job record, or 404 if not found."""
    db_pool = request.app.state.db_pool

    job = await get_ingestion_job(db_pool, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    return IngestionJobResponse(**job)
