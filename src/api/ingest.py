"""Ingestion API endpoints.

POST /ingest              – start a new ingestion job (returns 202).
GET  /ingest/{job_id}     – retrieve job status and counters.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.db.queries import (
    find_or_create_source,
    find_or_create_source_enriched,
    get_ingestion_job,
    insert_ingestion_job,
    list_ingestion_jobs,
    update_source_last_ingested,
)
from src.models.schemas import (
    BatchIngestItem,
    BatchIngestResponse,
    IngestionJobResponse,
    IngestRequest,
    IngestResponse,
    JobStatus,
    PaginatedResponse,
)
from src.utils.url_inference import infer_brand, infer_region
from src.config import get_settings

router = APIRouter()


@router.post("/ingest", status_code=202)
async def start_ingestion(
    body: IngestRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> BatchIngestResponse:
    """Accept a list of model.json URLs and launch one ingestion job per URL."""
    pipeline_service = request.app.state.pipeline_service
    settings = get_settings()

    url_strings = [str(u) for u in body.urls]
    nav_meta = body.nav_metadata or {}

    # Create one job per URL, each with its own source
    jobs: list[BatchIngestItem] = []
    async with request.app.state.session_factory() as session:
        for url in url_strings:
            brand = infer_brand(url)
            region = infer_region(url, settings.locale_region_map)
            meta = nav_meta.get(url, {})

            source_id, _created = await find_or_create_source_enriched(
                session, url, region, brand,
                nav_root_url=body.nav_root_url,
                nav_label=meta.get("label"),
                nav_section=meta.get("section"),
                page_path=meta.get("page_path"),
            )
            await update_source_last_ingested(session, source_id)

            job_id = await insert_ingestion_job(session, url, source_id)

            jobs.append(BatchIngestItem(
                source_id=source_id, job_id=job_id, url=url,
            ))
        await session.commit()

    # Launch each job as a separate background task
    for job_item in jobs:
        background_tasks.add_task(
            pipeline_service.run, job_item.job_id, [job_item.url], job_item.source_id,
        )

    first_source_id = jobs[0].source_id if jobs else None
    return BatchIngestResponse(
        jobs=jobs, status=JobStatus.IN_PROGRESS, source_id=first_source_id,
    )


@router.get("/jobs")
async def list_jobs(
    request: Request,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[IngestionJobResponse]:
    """Return a paginated list of ingestion jobs."""
    from math import ceil

    async with request.app.state.session_factory() as session:
        rows, total = await list_ingestion_jobs(session, page, size)
        await session.commit()

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
    async with request.app.state.session_factory() as session:
        job = await get_ingestion_job(session, job_id)
        await session.commit()

    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    return IngestionJobResponse(**job)
