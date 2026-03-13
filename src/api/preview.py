"""Navigation preview endpoint.

GET /preview/nav — lightweight recursive URL discovery without extraction
or validation.  Returns a tree of child URLs grouped by depth level.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from src.models.schemas import NavPreviewItem, NavPreviewResponse
from src.tools.filter_components import extract_child_urls, filter_by_component_type_direct
from src.utils.url_inference import normalize_url

logger = logging.getLogger(__name__)

router = APIRouter()


async def _fetch_aem_json(url: str, timeout: int) -> dict:
    """Fetch and parse AEM JSON from *url*.

    Raises ``HTTPException(502)`` when the URL is unreachable, returns a
    non-200 status, or the response body is not valid JSON.
    """
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
        raise HTTPException(
            status_code=502,
            detail=f"Timeout fetching AEM URL: {url}",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to reach AEM URL: {url} ({exc})",
        )
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid JSON response from AEM URL: {url}",
        )


@router.get("/preview/nav")
async def preview_nav(
    request: Request,
    url: str = Query(..., description="AEM model.json URL to preview"),
    max_depth: int = Query(default=1, ge=0, description="Maximum depth to discover"),
) -> NavPreviewResponse:
    """Lightweight recursive URL discovery without extraction or validation."""
    settings = request.app.state.settings
    timeout = settings.aem_request_timeout
    allowlist = settings.allowlist
    denylist = settings.denylist

    # BFS state
    queue: deque[tuple[str, int, str | None]] = deque()  # (url, depth, parent_url)
    queue.append((url, 0, None))
    visited: set[str] = set()

    urls_by_depth: dict[int, list[NavPreviewItem]] = defaultdict(list)

    while queue:
        current_url, depth, parent_url = queue.popleft()

        normalized = normalize_url(current_url)
        if normalized in visited:
            continue
        visited.add(normalized)

        urls_by_depth[depth].append(
            NavPreviewItem(url=current_url, depth=depth, parent_url=parent_url)
        )

        # Only fetch children if we haven't reached max_depth
        if depth >= max_depth:
            continue

        try:
            model_json = await _fetch_aem_json(current_url, timeout)
        except HTTPException:
            # For the root URL (depth 0), propagate the error
            if depth == 0:
                raise
            # For child URLs, skip silently and continue
            logger.warning("Failed to fetch child URL during preview: %s", current_url)
            continue

        # Filter components and extract child URLs
        nodes = filter_by_component_type_direct(model_json, allowlist, denylist)
        child_urls = extract_child_urls(nodes, current_url)

        for child_url in child_urls:
            child_normalized = normalize_url(child_url)
            if child_normalized not in visited:
                queue.append((child_url, depth + 1, current_url))

    total_urls = sum(len(items) for items in urls_by_depth.values())
    summary = {depth: len(items) for depth, items in sorted(urls_by_depth.items())}

    return NavPreviewResponse(
        root_url=url,
        total_urls=total_urls,
        urls_by_depth=dict(urls_by_depth),
        summary=summary,
    )
