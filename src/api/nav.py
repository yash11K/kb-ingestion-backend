"""Navigation tree and deep link API endpoints.

GET  /nav/tree                       – parse AEM model.json into a navigation tree
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
    list_deep_links,
    bulk_update_deep_link_status,
    upsert_nav_tree_cache,
)
from src.models.schemas import (
    DeepLinkConfirmRequest,
    DeepLinkDismissRequest,
    DeepLinkResponse,
    IngestResponse,
    JobStatus,
    NavTree,
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
    db_pool = request.app.state.db_pool
    settings = request.app.state.settings

    # Check cache first
    if not force_refresh:
        cached = await get_nav_tree_cache(db_pool, url)
        if cached is not None:
            return NavTree(**cached)

    # Fetch and parse
    model_json = await _fetch_aem_json(url, settings.aem_request_timeout)
    nav_tree = parse(model_json, url)

    # Cache for 24 hours
    await upsert_nav_tree_cache(
        db_pool,
        root_url=url,
        brand=nav_tree.brand,
        region=nav_tree.region,
        tree_data=nav_tree.model_dump(),
        ttl_hours=24,
    )

    return nav_tree


@router.get("/deep-links/{source_id}")
async def get_deep_links(
    source_id: UUID,
    request: Request,
    status: str = Query(default="pending", description="Filter by status"),
) -> list[DeepLinkResponse]:
    """Return deep links for a source, filtered by status."""
    db_pool = request.app.state.db_pool
    rows = await list_deep_links(db_pool, source_id, status)
    return [DeepLinkResponse(**r) for r in rows]


@router.post("/deep-links/{source_id}/confirm")
async def confirm_deep_links(
    source_id: UUID,
    body: DeepLinkConfirmRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> IngestResponse:
    """Confirm selected deep links and start ingestion for them."""
    db_pool = request.app.state.db_pool
    pipeline_service = request.app.state.pipeline_service

    # Mark as confirmed
    await bulk_update_deep_link_status(db_pool, body.link_ids, "confirmed")

    # Get the confirmed links' model.json URLs
    job_id, urls = await insert_deep_link_ingestion_jobs(
        db_pool, source_id, body.link_ids,
    )

    # Launch pipeline
    background_tasks.add_task(
        pipeline_service.run, job_id, urls, source_id,
    )

    return IngestResponse(source_id=source_id, job_id=job_id, status=JobStatus.IN_PROGRESS)


@router.post("/deep-links/{source_id}/dismiss")
async def dismiss_deep_links(
    source_id: UUID,
    body: DeepLinkDismissRequest,
    request: Request,
) -> dict:
    """Dismiss selected deep links."""
    db_pool = request.app.state.db_pool
    await bulk_update_deep_link_status(db_pool, body.link_ids, "dismissed")
    return {"dismissed": len(body.link_ids)}
