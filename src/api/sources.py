"""Sources API endpoints.

GET  /sources                       – paginated list of all sources.
POST /sources/lookup-urls           – lookup already-processed URLs with stats.
GET  /sources/{source_id}           – source detail with aggregate stats.
GET  /sources/{source_id}/jobs      – paginated jobs for a source.
POST /sources/{source_id}/ingest    – re-ingest an existing source.
"""

from __future__ import annotations

from math import ceil
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.db.queries import (
    get_active_jobs,
    get_source,
    get_source_stats,
    insert_ingestion_job,
    list_jobs_for_source,
    list_sources,
    lookup_sources_by_urls,
    update_source_last_ingested,
)
from src.models.schemas import (
    IngestionJobResponse,
    IngestResponse,
    JobStatus,
    PaginatedResponse,
    SourceDetail,
    SourceSummary,
    SourceUrlStats,
    UrlLookupRequest,
    UrlLookupResponse,
)

router = APIRouter()


@router.get("/sources")
async def list_all_sources(
    request: Request,
    region: str | None = None,
    brand: str | None = None,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[SourceSummary]:
    """Return a paginated list of all ingestion sources."""
    filters: dict[str, str] = {}
    if region is not None:
        filters["region"] = region
    if brand is not None:
        filters["brand"] = brand

    async with request.app.state.session_factory() as session:
        rows, total = await list_sources(session, filters, page, size)
        await session.commit()

    items = [
        SourceSummary(
            id=r["id"],
            url=r["url"],
            region=r["region"],
            brand=r["brand"],
            nav_label=r.get("nav_label"),
            nav_section=r.get("nav_section"),
            last_ingested_at=r.get("last_ingested_at"),
            created_at=r["created_at"],
        )
        for r in rows
    ]
    pages = ceil(total / size) if size > 0 else 0

    return PaginatedResponse[SourceSummary](
        items=items, total=total, page=page, size=size, pages=pages
    )


@router.post("/sources/lookup-urls")
async def lookup_urls(body: UrlLookupRequest, request: Request) -> UrlLookupResponse:
    """Look up which URLs have already been processed and return their stats."""
    async with request.app.state.session_factory() as session:
        rows = await lookup_sources_by_urls(session, body.urls)
        await session.commit()

    sources = {
        row["url"]: SourceUrlStats(
            source_id=row["source_id"],
            url=row["url"],
            last_ingested_at=row.get("last_ingested_at"),
            total_files=row["total_files"],
            approved=row["approved"],
            pending_review=row["pending_review"],
            rejected=row["rejected"],
        )
        for row in rows
    }
    return UrlLookupResponse(sources=sources)


@router.get("/sources/active-jobs")
async def get_active_source_jobs(request: Request) -> dict[str, str]:
    """Return {source_id: job_id} for all sources with in-progress jobs."""
    async with request.app.state.session_factory() as session:
        result = await get_active_jobs(session)
        await session.commit()
    return result


@router.get("/sources/{source_id}")
async def get_source_detail(source_id: UUID, request: Request) -> SourceDetail:
    """Return source detail with aggregate job/file stats."""
    async with request.app.state.session_factory() as session:
        source = await get_source(session, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source not found")

        stats = await get_source_stats(session, source_id)
        await session.commit()

    return SourceDetail(
        id=source["id"],
        url=source["url"],
        region=source["region"],
        brand=source["brand"],
        last_ingested_at=source.get("last_ingested_at"),
        created_at=source["created_at"],
        updated_at=source["updated_at"],
        **stats,
    )


@router.get("/sources/{source_id}/jobs")
async def list_source_jobs(
    source_id: UUID,
    request: Request,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[IngestionJobResponse]:
    """Return paginated ingestion jobs for a specific source."""
    async with request.app.state.session_factory() as session:
        source = await get_source(session, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source not found")

        rows, total = await list_jobs_for_source(session, source_id, page, size)
        await session.commit()

    items = [IngestionJobResponse(**r) for r in rows]
    pages = ceil(total / size) if size > 0 else 0

    return PaginatedResponse[IngestionJobResponse](
        items=items, total=total, page=page, size=size, pages=pages
    )


@router.post("/sources/{source_id}/ingest", status_code=202)
async def reingest_source(
    source_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
) -> IngestResponse:
    """Trigger a new ingestion job for an existing source."""
    pipeline_service = request.app.state.pipeline_service

    async with request.app.state.session_factory() as session:
        source = await get_source(session, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source not found")

        job_id = await insert_ingestion_job(session, source["url"], source_id)
        await update_source_last_ingested(session, source_id)
        await session.commit()

    background_tasks.add_task(
        pipeline_service.run,
        job_id,
        [source["url"]],  # flat URL list
        source_id,
    )

    return IngestResponse(
        source_id=source_id, job_id=job_id, status=JobStatus.IN_PROGRESS
    )
