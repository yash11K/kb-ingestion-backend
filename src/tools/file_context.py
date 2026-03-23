"""get_file_context tool – fetches file details and deep links for the Context Agent."""

from __future__ import annotations

import asyncpg
from strands.tools import tool

from src.db.queries import get_kb_file, list_deep_links

_db_pool: asyncpg.Pool | None = None


def set_db_pool(pool: asyncpg.Pool) -> None:
    """Set the module-level database pool for use by the get_file_context tool."""
    global _db_pool
    _db_pool = pool


@tool
async def get_file_context(file_id: str) -> dict:
    """Fetch complete file details, validation info, and pending deep links for a file.

    Args:
        file_id: UUID string of the file to look up.

    Returns:
        dict with ``file`` (file metadata, scores, content) and ``deep_links``
        (list of pending/confirmed deep links for the source).
    """
    from uuid import UUID

    if _db_pool is None:
        return {"error": "Database pool not initialised"}

    uid = UUID(file_id)
    record = await get_kb_file(_db_pool, uid)
    if record is None:
        return {"error": f"File {file_id} not found"}

    # Build a concise representation for the agent
    file_info = {
        "id": str(record["id"]),
        "title": record.get("title", ""),
        "filename": record.get("filename", ""),
        "status": record.get("status", ""),
        "content_type": record.get("content_type", ""),
        "component_type": record.get("component_type", ""),
        "source_url": record.get("source_url", ""),
        "region": record.get("region", ""),
        "brand": record.get("brand", ""),
        "doc_type": record.get("doc_type"),
        "validation_score": record.get("validation_score"),
        "validation_breakdown": record.get("validation_breakdown"),
        "validation_issues": record.get("validation_issues"),
        "md_content": record.get("md_content", ""),
        "parent_context": record.get("parent_context", ""),
        "aem_node_id": record.get("aem_node_id"),
        "s3_key": record.get("s3_key"),
        "content_hash": record.get("content_hash", ""),
    }

    # Fetch deep links if source_id exists
    deep_links: list[dict] = []
    source_id = record.get("source_id")
    if source_id:
        file_info["source_id"] = str(source_id)
        for status in ("pending", "confirmed"):
            links = await list_deep_links(_db_pool, source_id, status)
            for link in links:
                deep_links.append({
                    "url": link["url"],
                    "anchor_text": link.get("anchor_text", ""),
                    "found_in_page": link.get("found_in_page", ""),
                    "status": link["status"],
                })

    return {"file": file_info, "deep_links": deep_links}
