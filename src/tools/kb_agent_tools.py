"""Tools for the KB Agent — read-only SQL, system stats, source/job/file inspection."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strands.tools import tool

logger = logging.getLogger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None

_MAX_ROWS = 100

# Patterns that indicate a write/DDL statement
_FORBIDDEN_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|"
    r"MERGE|UPSERT|REPLACE|CALL|EXEC|EXECUTE|SET |VACUUM|REINDEX|CLUSTER|"
    r"COMMENT|LOCK|DISCARD|REASSIGN|SECURITY|LOAD)\b",
    re.IGNORECASE,
)


def set_session_factory(sf: async_sessionmaker[AsyncSession]) -> None:
    """Inject the async session factory at app startup."""
    global _session_factory
    _session_factory = sf


def _serialise(value):
    """Convert non-JSON-serialisable types to strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Tool 1: Raw read-only SQL
# ---------------------------------------------------------------------------

@tool
async def execute_sql_query(query: str) -> dict:
    """Execute a read-only SQL SELECT query against the PostgreSQL database.

    Only SELECT statements are allowed. Any write/DDL operations will be
    rejected. Results are capped at 100 rows.

    Args:
        query: A SQL SELECT statement to execute.

    Returns:
        dict with ``columns`` (list of column names), ``rows`` (list of row
        dicts), and ``row_count``.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    stripped = query.strip().rstrip(";").strip()

    # Must start with SELECT or WITH (CTE)
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return {"error": "Only SELECT (or WITH … SELECT) queries are allowed."}

    # Reject any forbidden keywords
    if _FORBIDDEN_PATTERNS.search(stripped):
        return {"error": "Query contains forbidden write/DDL keywords."}

    try:
        async with _session_factory() as session:
            result = await session.execute(text(stripped))
            columns = list(result.keys())
            rows = [
                {col: _serialise(val) for col, val in zip(columns, row)}
                for row in result.fetchmany(_MAX_ROWS)
            ]
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:
        logger.exception("SQL query failed: %s", stripped[:200])
        return {"error": f"Query execution failed: {exc}"}


# ---------------------------------------------------------------------------
# Tool 2: System stats
# ---------------------------------------------------------------------------

@tool
async def get_system_stats() -> dict:
    """Get aggregate system statistics: total files, pending review, approved,
    rejected counts, and average validation score.

    Returns:
        dict with total_files, pending_review, approved, rejected, avg_score.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import get_stats

    async with _session_factory() as session:
        data = await get_stats(session)
        return {
            "total_files": data["total_files"],
            "pending_review": data["pending_review"],
            "approved": data["approved"],
            "rejected": data["rejected"],
            "avg_score": round(float(data["avg_score"]), 2) if data["avg_score"] else 0.0,
        }


# ---------------------------------------------------------------------------
# Tool 3: Source explorer
# ---------------------------------------------------------------------------

@tool
async def list_sources_tool(page: int = 1, size: int = 20) -> dict:
    """List all content sources with pagination.

    Args:
        page: Page number (1-based). Default 1.
        size: Items per page. Default 20.

    Returns:
        dict with ``sources`` list and ``total`` count.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import list_sources

    async with _session_factory() as session:
        sources, total = await list_sources(session, page, size)
        for s in sources:
            for k, v in s.items():
                s[k] = _serialise(v)
        return {"sources": sources, "total": total}


@tool
async def get_source_stats_tool(source_id: str) -> dict:
    """Get aggregate stats for a specific source: job counts and file counts by status.

    Args:
        source_id: UUID string of the source.

    Returns:
        dict with total_jobs, completed_jobs, failed_jobs, active_jobs,
        total_files, pending_review, approved, rejected.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import get_source_stats

    async with _session_factory() as session:
        data = await get_source_stats(session, UUID(source_id))
        return data


# ---------------------------------------------------------------------------
# Tool 4: Job inspector
# ---------------------------------------------------------------------------

@tool
async def list_recent_jobs(status: str = "", page: int = 1, size: int = 10) -> dict:
    """List recent ingestion jobs, optionally filtered by status.

    Args:
        status: Filter by job status (in_progress, completed, failed). Empty string for all.
        page: Page number (1-based). Default 1.
        size: Items per page. Default 10.

    Returns:
        dict with ``jobs`` list and ``total`` count.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import list_ingestion_jobs

    filters = {}
    if status:
        filters["status"] = status

    async with _session_factory() as session:
        jobs, total = await list_ingestion_jobs(session, filters, page, size)
        for j in jobs:
            for k, v in j.items():
                j[k] = _serialise(v)
        return {"jobs": jobs, "total": total}


@tool
async def get_job_details(job_id: str) -> dict:
    """Get full details of a specific ingestion job.

    Args:
        job_id: UUID string of the ingestion job.

    Returns:
        dict with all job fields, or error if not found.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import get_ingestion_job

    async with _session_factory() as session:
        job = await get_ingestion_job(session, UUID(job_id))
        if job is None:
            return {"error": f"Job {job_id} not found"}
        return {k: _serialise(v) for k, v in job.items()}


# ---------------------------------------------------------------------------
# Tool 5: File search
# ---------------------------------------------------------------------------

@tool
async def search_files(
    query: str = "",
    status: str = "",
    region: str = "",
    brand: str = "",
    content_type: str = "",
    doc_type: str = "",
    page: int = 1,
    size: int = 20,
) -> dict:
    """Search KB files using full-text search and/or filters.

    Args:
        query: Free-text search query (uses PostgreSQL full-text search). Optional.
        status: Filter by file status (pending_review, approved, rejected, in_s3, auto_rejected). Optional.
        region: Filter by region. Optional.
        brand: Filter by brand. Optional.
        content_type: Filter by content_type. Optional.
        doc_type: Filter by doc_type (TnC, FAQ, ProductGuide, Support, Marketing, General). Optional.
        page: Page number (1-based). Default 1.
        size: Items per page. Default 20.

    Returns:
        dict with ``files`` list (summary fields) and ``total`` count.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import list_kb_files

    filters = {}
    if status:
        filters["status"] = status
    if region:
        filters["region"] = region
    if brand:
        filters["brand"] = brand
    if content_type:
        filters["content_type"] = content_type

    async with _session_factory() as session:
        # If there's a free-text query, use the search_vector approach
        if query:
            from sqlalchemy import text as sa_text

            sql = """
                SELECT id, title, filename, content_type, component_type, doc_type,
                       source_url, region, brand, status, validation_score,
                       ts_rank_cd(search_vector, plainto_tsquery('english', :query)) AS rank
                FROM kb_files
                WHERE search_vector @@ plainto_tsquery('english', :query)
            """
            conditions = []
            params: dict = {"query": query}
            if status:
                conditions.append("status = :status")
                params["status"] = status
            if region:
                conditions.append("region = :region")
                params["region"] = region
            if brand:
                conditions.append("brand = :brand")
                params["brand"] = brand
            if content_type:
                conditions.append("content_type = :content_type")
                params["content_type"] = content_type
            if doc_type:
                conditions.append("doc_type = :doc_type")
                params["doc_type"] = doc_type

            if conditions:
                sql += " AND " + " AND ".join(conditions)
            sql += " ORDER BY rank DESC LIMIT :limit OFFSET :offset"
            params["limit"] = size
            params["offset"] = (page - 1) * size

            result = await session.execute(sa_text(sql), params)
            columns = list(result.keys())
            rows = [
                {col: _serialise(val) for col, val in zip(columns, row)}
                for row in result.fetchall()
            ]
            return {"files": rows, "total": len(rows)}
        else:
            # Use the ORM-based list with filters
            if doc_type:
                # doc_type isn't in the standard _ALLOWED_FILTERS, use SQL
                from sqlalchemy import text as sa_text

                sql = "SELECT id, title, filename, content_type, doc_type, region, brand, status, validation_score FROM kb_files WHERE 1=1"
                params = {}
                for key, val in filters.items():
                    sql += f" AND {key} = :{key}"
                    params[key] = val
                if doc_type:
                    sql += " AND doc_type = :doc_type"
                    params["doc_type"] = doc_type
                sql += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                params["limit"] = size
                params["offset"] = (page - 1) * size
                result = await session.execute(sa_text(sql), params)
                columns = list(result.keys())
                rows = [
                    {col: _serialise(val) for col, val in zip(columns, row)}
                    for row in result.fetchall()
                ]
                return {"files": rows, "total": len(rows)}

            files, total = await list_kb_files(session, filters, page, size)
            # Return summary fields only
            summary_fields = [
                "id", "title", "filename", "content_type", "doc_type",
                "region", "brand", "status", "validation_score",
            ]
            slim = []
            for f in files:
                slim.append({k: _serialise(f.get(k)) for k in summary_fields})
            return {"files": slim, "total": total}


# ---------------------------------------------------------------------------
# Tool 6: Deep link inspector
# ---------------------------------------------------------------------------

@tool
async def list_deep_links_tool(
    source_id: str,
    status: str = "pending",
    page: int = 1,
    size: int = 50,
) -> dict:
    """List deep links for a source, filtered by status.

    Args:
        source_id: UUID string of the source.
        status: Link status filter (pending, confirmed, dismissed, ingested). Default pending.
        page: Page number (1-based). Default 1.
        size: Items per page. Default 50.

    Returns:
        dict with ``links`` list and ``total`` count.
    """
    if _session_factory is None:
        return {"error": "Database session factory not initialised"}

    from src.db.queries import list_deep_links

    async with _session_factory() as session:
        links = await list_deep_links(session, UUID(source_id), status)
        total = len(links)
        start = (page - 1) * size
        page_links = links[start : start + size]
        for link in page_links:
            for k, v in link.items():
                link[k] = _serialise(v)
        return {"links": page_links, "total": total}
