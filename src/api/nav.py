"""Navigation tree and deep link API endpoints.

GET  /nav/tree                       – parse AEM model.json into a navigation tree
GET  /deep-links                     – list all deep links (paginated, filterable)
GET  /deep-links/{source_id}         – list pending deep links for a source
POST /deep-links/{source_id}/confirm – confirm deep links for ingestion
POST /deep-links/{source_id}/dismiss – dismiss deep links
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from src.db.queries import (
    get_nav_tree_cache,
    insert_deep_link_ingestion_jobs,
    list_all_deep_links,
    list_deep_links,
    bulk_update_deep_link_status,
    upsert_nav_tree_cache,
)
from src.models.schemas import (
    BatchIngestItem,
    BatchIngestResponse,
    DeepLinkConfirmRequest,
    DeepLinkDismissRequest,
    DeepLinkResponse,
    JobStatus,
    NavTree,
    PaginatedResponse,
)
from src.services.nav_parser import parse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _fetch_aem_json(url: str, timeout: int) -> dict:
    """Fetch and parse AEM JSON from *url*."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"AEM URL returned HTTP {resp.status_code}: {url}",
                )
            return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail=f"Timeout fetching AEM URL: {url}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Unable to reach AEM URL: {url} ({exc})")
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Invalid JSON response from AEM URL: {url}")


@router.get("/nav/tree")
async def get_nav_tree(
    request: Request,
    url: str = Query(..., description="AEM model.json URL (e.g. home page)"),
    force_refresh: bool = Query(default=False, description="Bypass cache"),
) -> NavTree:
    """Parse an AEM model.json into a navigation tree for source selection."""
    settings = request.app.state.settings

    # Check cache first
    async with request.app.state.session_factory() as session:
        if not force_refresh:
            cached = await get_nav_tree_cache(session, url)
            if cached is not None:
                await session.commit()
                return NavTree(**cached)

        # Fetch and parse
        model_json = await _fetch_aem_json(url, settings.aem_request_timeout)
        nav_tree = parse(model_json, url)

        # Cache for 24 hours
        await upsert_nav_tree_cache(
            session,
            root_url=url,
            brand=nav_tree.brand,
            region=nav_tree.region,
            tree_data=nav_tree.model_dump(),
            ttl_hours=24,
        )
        await session.commit()

    return nav_tree


@router.get("/deep-links")
async def get_all_deep_links(
    request: Request,
    status: str | None = Query(default=None, description="Filter by status: pending, confirmed, dismissed, ingested"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
) -> PaginatedResponse[DeepLinkResponse]:
    """List all deep links across all sources, with optional status filter and pagination."""
    async with request.app.state.session_factory() as session:
        rows, total = await list_all_deep_links(session, status=status, page=page, size=size)
        await session.commit()
    items = [DeepLinkResponse(**r) for r in rows]
    pages = max(1, -(-total // size))  # ceil division
    return PaginatedResponse(items=items, total=total, page=page, size=size, pages=pages)


@router.get("/deep-links/{source_id}")
async def get_deep_links(
    source_id: UUID,
    request: Request,
    status: str = Query(default="pending", description="Filter by status"),
    found_in_page: str | None = Query(default=None, description="Filter by the page URL where the link was discovered (file source_url)"),
) -> list[DeepLinkResponse]:
    """Return deep links for a source, filtered by status.

    Pass ``found_in_page`` (the file's source_url) to get only the deep
    links discovered on that specific page — useful for file previews.
    """
    async with request.app.state.session_factory() as session:
        rows = await list_deep_links(session, source_id, status, found_in_page=found_in_page)
        await session.commit()
    return [DeepLinkResponse(**r) for r in rows]


@router.post("/deep-links/{source_id}/confirm")
async def confirm_deep_links(
    source_id: UUID,
    body: DeepLinkConfirmRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> BatchIngestResponse:
    """Confirm selected deep links and start one ingestion job per link."""
    pipeline_service = request.app.state.pipeline_service

    async with request.app.state.session_factory() as session:
        # Mark as confirmed
        await bulk_update_deep_link_status(session, body.link_ids, "confirmed")

        # Create one job per deep link
        job_url_pairs = await insert_deep_link_ingestion_jobs(
            session, source_id, body.link_ids,
        )
        await session.commit()

    # Launch a separate pipeline per job
    jobs: list[BatchIngestItem] = []
    for job_id, url in job_url_pairs:
        background_tasks.add_task(
            pipeline_service.run, job_id, [url], source_id,
        )
        jobs.append(BatchIngestItem(source_id=source_id, job_id=job_id, url=url))

    return BatchIngestResponse(jobs=jobs, status=JobStatus.IN_PROGRESS)


@router.post("/deep-links/{source_id}/dismiss")
async def dismiss_deep_links(
    source_id: UUID,
    body: DeepLinkDismissRequest,
    request: Request,
) -> dict:
    """Dismiss selected deep links."""
    async with request.app.state.session_factory() as session:
        await bulk_update_deep_link_status(session, body.link_ids, "dismissed")
        await session.commit()
    return {"dismissed": len(body.link_ids)}
