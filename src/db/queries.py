"""SQL query functions for kb_files, ingestion_jobs, nav_tree_cache, and deep_links tables."""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg


# ---------------------------------------------------------------------------
# kb_files queries
# ---------------------------------------------------------------------------


async def insert_kb_file(pool: asyncpg.Pool, file: dict) -> UUID:
    """Insert a new kb_file record and return its UUID.

    *file* must contain keys matching the kb_files columns (excluding id,
    created_at, updated_at which have defaults).
    """
    row = await pool.fetchrow(
        """
        INSERT INTO kb_files (
            filename, title, content_type, content_hash, source_url,
            component_type, md_content,
            parent_context, region, brand, key, namespace,
            validation_score, validation_breakdown, validation_issues,
            status, source_id, job_id
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, $7,
            $8, $9, $10, $11, $12,
            $13, $14, $15,
            $16, $17, $18
        )
        RETURNING id
        """,
        file["filename"],
        file["title"],
        file["content_type"],
        file["content_hash"],
        file["source_url"],
        file["component_type"],
        file["md_content"],
        file.get("parent_context"),
        file["region"],
        file["brand"],
        file.get("key", ""),
        file.get("namespace", ""),
        file.get("validation_score"),
        json.dumps(file["validation_breakdown"]) if file.get("validation_breakdown") is not None else None,
        json.dumps(file["validation_issues"]) if file.get("validation_issues") is not None else None,
        file.get("status", "pending_review"),
        file.get("source_id"),
        file.get("job_id"),
    )
    return row["id"]


async def update_kb_file_status(
    pool: asyncpg.Pool, file_id: UUID, status: str, **kwargs
) -> None:
    """Update a kb_file's status and updated_at, plus any extra fields.

    Common extra kwargs: s3_bucket, s3_key, s3_uploaded_at, reviewed_by,
    reviewed_at, review_notes, validation_score, validation_breakdown,
    validation_issues, content_hash, md_content.
    """
    # Build SET clause dynamically from kwargs
    sets = ["status = $1", "updated_at = NOW()"]
    values: list = [status]
    idx = 2  # next parameter index

    json_columns = {"validation_breakdown", "validation_issues"}

    for col, val in kwargs.items():
        if col in json_columns and val is not None:
            val = json.dumps(val)
        sets.append(f"{col} = ${idx}")
        values.append(val)
        idx += 1

    values.append(file_id)
    query = f"UPDATE kb_files SET {', '.join(sets)} WHERE id = ${idx}"
    await pool.execute(query, *values)


async def get_kb_file(pool: asyncpg.Pool, file_id: UUID) -> dict | None:
    """Return a single kb_file record as a dict, or None."""
    row = await pool.fetchrow("SELECT * FROM kb_files WHERE id = $1", file_id)
    if row is None:
        return None
    return _row_to_dict(row)


async def list_kb_files(
    pool: asyncpg.Pool, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated, filtered list of kb_files and the total count.

    Supported filter keys: status, region, brand, content_type, component_type.
    """
    where_clauses, values = _build_where(filters)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Total count
    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS cnt FROM kb_files {where_sql}", *values
    )
    total = count_row["cnt"]

    # Paginated results
    offset = (page - 1) * size
    idx = len(values) + 1
    rows = await pool.fetch(
        f"SELECT * FROM kb_files {where_sql} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
        *values,
        size,
        offset,
    )
    return [_row_to_dict(r) for r in rows], total


async def find_by_content_hash(pool: asyncpg.Pool, content_hash: str) -> dict | None:
    """Find a kb_file by its content_hash. Returns the first match or None."""
    row = await pool.fetchrow(
        "SELECT * FROM kb_files WHERE content_hash = $1 LIMIT 1", content_hash
    )
    if row is None:
        return None
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# sources queries
# ---------------------------------------------------------------------------


async def find_or_create_source(
    pool: asyncpg.Pool, url: str, region: str, brand: str
) -> tuple[UUID, bool]:
    """Find an existing source by URL or create a new one.

    Returns (source_id, created) where *created* is True if a new row was
    inserted.
    """
    # Try to find existing source
    row = await pool.fetchrow(
        "SELECT id FROM sources WHERE url = $1", url
    )
    if row is not None:
        return row["id"], False

    # Create new source
    row = await pool.fetchrow(
        """
        INSERT INTO sources (url, region, brand)
        VALUES ($1, $2, $3)
        ON CONFLICT (url) DO UPDATE SET updated_at = NOW()
        RETURNING id
        """,
        url,
        region,
        brand,
    )
    return row["id"], True


async def get_source(pool: asyncpg.Pool, source_id: UUID) -> dict | None:
    """Return a single source record as a dict, or None."""
    row = await pool.fetchrow("SELECT * FROM sources WHERE id = $1", source_id)
    if row is None:
        return None
    return _row_to_dict(row)


async def list_sources(
    pool: asyncpg.Pool, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated, filtered list of sources and the total count.

    Supported filter keys: region, brand.
    """
    allowed = {"region", "brand"}
    clauses, values = [], []
    idx = 1
    for key, val in filters.items():
        if key in allowed and val is not None:
            clauses.append(f"{key} = ${idx}")
            values.append(val)
            idx += 1

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    count_row = await pool.fetchrow(
        f"SELECT COUNT(*)::int AS cnt FROM sources {where_sql}", *values
    )
    total = count_row["cnt"]

    offset = (page - 1) * size
    rows = await pool.fetch(
        f"SELECT * FROM sources {where_sql} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
        *values,
        size,
        offset,
    )
    return [_row_to_dict(r) for r in rows], total


async def update_source_last_ingested(pool: asyncpg.Pool, source_id: UUID) -> None:
    """Touch the last_ingested_at timestamp on a source."""
    await pool.execute(
        "UPDATE sources SET last_ingested_at = NOW(), updated_at = NOW() WHERE id = $1",
        source_id,
    )


async def list_jobs_for_source(
    pool: asyncpg.Pool, source_id: UUID, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated list of ingestion jobs for a specific source."""
    count_row = await pool.fetchrow(
        "SELECT COUNT(*)::int AS cnt FROM ingestion_jobs WHERE source_id = $1",
        source_id,
    )
    total = count_row["cnt"]

    offset = (page - 1) * size
    rows = await pool.fetch(
        "SELECT * FROM ingestion_jobs WHERE source_id = $1 ORDER BY started_at DESC LIMIT $2 OFFSET $3",
        source_id,
        size,
        offset,
    )
    return [_row_to_dict(r) for r in rows], total


async def get_source_stats(pool: asyncpg.Pool, source_id: UUID) -> dict:
    """Return aggregate stats for a single source."""
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)::int AS total_jobs,
            COUNT(*) FILTER (WHERE status = 'completed')::int AS completed_jobs,
            COUNT(*) FILTER (WHERE status = 'failed')::int AS failed_jobs,
            COUNT(*) FILTER (WHERE status = 'in_progress')::int AS active_jobs
        FROM ingestion_jobs
        WHERE source_id = $1
        """,
        source_id,
    )
    file_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)::int AS total_files,
            COUNT(*) FILTER (WHERE status = 'pending_review')::int AS pending_review,
            COUNT(*) FILTER (WHERE status IN ('approved', 'in_s3'))::int AS approved,
            COUNT(*) FILTER (WHERE status IN ('rejected', 'auto_rejected'))::int AS rejected
        FROM kb_files
        WHERE source_id = $1
        """,
        source_id,
    )
    return {**dict(row), **dict(file_row)}


# ---------------------------------------------------------------------------
# ingestion_jobs queries
# ---------------------------------------------------------------------------


async def insert_ingestion_job(
    pool: asyncpg.Pool,
    source_url: str,
    source_id: UUID | None = None,
    max_depth: int = 0,
) -> UUID:
    """Create a new ingestion job with status 'in_progress' and return its id.

    *max_depth* is the effective (clamped) crawl depth for this job.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_jobs (source_url, source_id, status, started_at, max_depth)
        VALUES ($1, $2, 'in_progress', NOW(), $3)
        RETURNING id
        """,
        source_url,
        source_id,
        max_depth,
    )
    return row["id"]



async def update_ingestion_job(
    pool: asyncpg.Pool, job_id: UUID, **kwargs
) -> None:
    """Update any provided fields on an ingestion job."""
    if not kwargs:
        return

    sets: list[str] = []
    values: list = []
    idx = 1

    for col, val in kwargs.items():
        sets.append(f"{col} = ${idx}")
        values.append(val)
        idx += 1

    values.append(job_id)
    query = f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = ${idx}"
    await pool.execute(query, *values)


async def update_crawl_progress(
    pool: asyncpg.Pool, job_id: UUID, pages_crawled: int, current_depth: int
) -> None:
    """Update crawl progress counters on an ingestion job.

    Convenience wrapper around update_ingestion_job for the BFS crawl loop.
    """
    await pool.execute(
        "UPDATE ingestion_jobs SET pages_crawled = $1, current_depth = $2 WHERE id = $3",
        pages_crawled,
        current_depth,
        job_id,
    )


async def get_ingestion_job(pool: asyncpg.Pool, job_id: UUID) -> dict | None:
    """Return a single ingestion_job record as a dict, or None."""
    row = await pool.fetchrow(
        "SELECT * FROM ingestion_jobs WHERE id = $1", job_id
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def list_ingestion_jobs(
    pool: asyncpg.Pool, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated list of ingestion jobs ordered by started_at DESC."""
    count_row = await pool.fetchrow("SELECT COUNT(*)::int AS cnt FROM ingestion_jobs")
    total = count_row["cnt"]

    offset = (page - 1) * size
    rows = await pool.fetch(
        "SELECT * FROM ingestion_jobs ORDER BY started_at DESC LIMIT $1 OFFSET $2",
        size,
        offset,
    )
    return [_row_to_dict(r) for r in rows], total


async def get_active_jobs(pool: asyncpg.Pool) -> dict[str, str]:
    """Return {source_id: job_id} for all in-progress ingestion jobs."""
    rows = await pool.fetch(
        """
        SELECT source_id, id AS job_id
        FROM ingestion_jobs
        WHERE status = 'in_progress' AND source_id IS NOT NULL
        ORDER BY started_at DESC
        """
    )
    return {str(r["source_id"]): str(r["job_id"]) for r in rows}


# ---------------------------------------------------------------------------
# revalidation_jobs queries
# ---------------------------------------------------------------------------


async def insert_revalidation_job(pool: asyncpg.Pool, total_files: int) -> UUID:
    """Create a new revalidation job with status 'in_progress' and return its id."""
    row = await pool.fetchrow(
        """
        INSERT INTO revalidation_jobs (total_files, status, started_at)
        VALUES ($1, 'in_progress', NOW())
        RETURNING id
        """,
        total_files,
    )
    return row["id"]


async def update_revalidation_job(
    pool: asyncpg.Pool, job_id: UUID, **kwargs
) -> None:
    """Update any provided fields on a revalidation job."""
    if not kwargs:
        return

    sets: list[str] = []
    values: list = []
    idx = 1

    for col, val in kwargs.items():
        sets.append(f"{col} = ${idx}")
        values.append(val)
        idx += 1

    values.append(job_id)
    query = f"UPDATE revalidation_jobs SET {', '.join(sets)} WHERE id = ${idx}"
    await pool.execute(query, *values)


async def get_revalidation_job(pool: asyncpg.Pool, job_id: UUID) -> dict | None:
    """Return a single revalidation_job record as a dict, or None."""
    row = await pool.fetchrow(
        "SELECT * FROM revalidation_jobs WHERE id = $1", job_id
    )
    if row is None:
        return None
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Review queue query
# ---------------------------------------------------------------------------


async def list_review_queue(
    pool: asyncpg.Pool, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return paginated pending_review files with optional filters.

    Always filters by status='pending_review'. Additional filter keys:
    region, brand, content_type, component_type.
    """
    # Force status filter to pending_review
    combined = {**filters, "status": "pending_review"}
    return await list_kb_files(pool, combined, page, size)


# ---------------------------------------------------------------------------
# Stats query
# ---------------------------------------------------------------------------


async def get_stats(pool: asyncpg.Pool) -> dict:
    """Return aggregate stats across all kb_files."""
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)::int AS total_files,
            COUNT(*) FILTER (WHERE status = 'pending_review')::int AS pending_review,
            COUNT(*) FILTER (WHERE status IN ('approved', 'in_s3'))::int AS approved,
            COUNT(*) FILTER (WHERE status IN ('rejected', 'auto_rejected'))::int AS rejected,
            COALESCE(AVG(validation_score), 0.0) AS avg_score
        FROM kb_files
        """
    )
    return dict(row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_FILTERS = {"status", "region", "brand", "content_type", "component_type"}


def _build_where(filters: dict) -> tuple[list[str], list]:
    """Build WHERE clause fragments and parameter values from a filter dict."""
    clauses: list[str] = []
    values: list = []
    idx = 1
    for key, val in filters.items():
        if key in _ALLOWED_FILTERS and val is not None:
            clauses.append(f"{key} = ${idx}")
            values.append(val)
            idx += 1
    return clauses, values


def _row_to_dict(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a plain dict, deserialising JSONB cols."""
    d = dict(row)
    # asyncpg returns JSONB columns as strings; parse them back
    for col in ("validation_breakdown", "validation_issues", "tree_data"):
        if col in d and isinstance(d[col], str):
            d[col] = json.loads(d[col])
    return d


# ---------------------------------------------------------------------------
# nav_tree_cache queries
# ---------------------------------------------------------------------------


async def upsert_nav_tree_cache(
    pool: asyncpg.Pool,
    root_url: str,
    brand: str,
    region: str,
    tree_data: dict,
    ttl_hours: int = 24,
) -> None:
    """Insert or update a cached navigation tree."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    await pool.execute(
        """
        INSERT INTO nav_tree_cache (root_url, brand, region, tree_data, fetched_at, expires_at)
        VALUES ($1, $2, $3, $4, NOW(), $5)
        ON CONFLICT (root_url) DO UPDATE
          SET brand = $2, region = $3, tree_data = $4, fetched_at = NOW(), expires_at = $5
        """,
        root_url,
        brand,
        region,
        json.dumps(tree_data),
        expires_at,
    )


async def get_nav_tree_cache(pool: asyncpg.Pool, root_url: str) -> dict | None:
    """Return cached nav tree data if present and not expired, else None."""
    row = await pool.fetchrow(
        "SELECT tree_data FROM nav_tree_cache WHERE root_url = $1 AND expires_at > NOW()",
        root_url,
    )
    if row is None:
        return None
    data = row["tree_data"]
    if isinstance(data, str):
        return json.loads(data)
    return data


# ---------------------------------------------------------------------------
# deep_links queries
# ---------------------------------------------------------------------------


async def insert_deep_links(pool: asyncpg.Pool, links: list[dict]) -> None:
    """Batch insert discovered deep links."""
    if not links:
        return
    await pool.executemany(
        """
        INSERT INTO deep_links (source_id, job_id, url, model_json_url, anchor_text,
                                found_in_node, found_in_page, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
        """,
        [
            (
                link.get("source_id"),
                link.get("job_id"),
                link["url"],
                link["model_json_url"],
                link.get("anchor_text", ""),
                link.get("found_in_node", ""),
                link["found_in_page"],
            )
            for link in links
        ],
    )


async def list_deep_links(
    pool: asyncpg.Pool,
    source_id: UUID,
    status: str = "pending",
) -> list[dict]:
    """Return deep links for a source filtered by status."""
    rows = await pool.fetch(
        """
        SELECT id, url, model_json_url, anchor_text, found_in_node,
               found_in_page, status, created_at
        FROM deep_links
        WHERE source_id = $1 AND status = $2
        ORDER BY created_at DESC
        """,
        source_id,
        status,
    )
    return [dict(r) for r in rows]


async def bulk_update_deep_link_status(
    pool: asyncpg.Pool,
    link_ids: list[UUID],
    status: str,
) -> None:
    """Update status for multiple deep links."""
    await pool.execute(
        "UPDATE deep_links SET status = $1 WHERE id = ANY($2::uuid[])",
        status,
        link_ids,
    )


async def insert_deep_link_ingestion_jobs(
    pool: asyncpg.Pool,
    source_id: UUID,
    link_ids: list[UUID],
) -> tuple[UUID, list[str]]:
    """Create an ingestion job for confirmed deep links.

    Returns (job_id, list_of_model_json_urls).
    """
    # Get the model.json URLs for confirmed links
    rows = await pool.fetch(
        "SELECT model_json_url FROM deep_links WHERE id = ANY($1::uuid[])",
        link_ids,
    )
    urls = [r["model_json_url"] for r in rows]

    if not urls:
        raise ValueError("No valid deep link URLs found for the given IDs")

    # Create job
    job_row = await pool.fetchrow(
        """
        INSERT INTO ingestion_jobs (source_url, source_id, status, started_at)
        VALUES ($1, $2, 'in_progress', NOW())
        RETURNING id
        """,
        urls[0],
        source_id,
    )
    job_id = job_row["id"]

    # Mark links as ingested
    await bulk_update_deep_link_status(pool, link_ids, "ingested")

    return job_id, urls


# ---------------------------------------------------------------------------
# Enhanced source queries
# ---------------------------------------------------------------------------


async def find_or_create_source_enriched(
    pool: asyncpg.Pool,
    url: str,
    region: str,
    brand: str,
    nav_root_url: str | None = None,
    nav_label: str | None = None,
    nav_section: str | None = None,
    page_path: str | None = None,
) -> tuple[UUID, bool]:
    """Find or create a source with optional nav context enrichment."""
    row = await pool.fetchrow("SELECT id FROM sources WHERE url = $1", url)
    if row is not None:
        # Update nav context if provided
        if nav_label or nav_section:
            await pool.execute(
                """
                UPDATE sources
                SET nav_root_url = COALESCE($2, nav_root_url),
                    nav_label = COALESCE($3, nav_label),
                    nav_section = COALESCE($4, nav_section),
                    page_path = COALESCE($5, page_path),
                    updated_at = NOW()
                WHERE id = $1
                """,
                row["id"], nav_root_url, nav_label, nav_section, page_path,
            )
        return row["id"], False

    row = await pool.fetchrow(
        """
        INSERT INTO sources (url, region, brand, nav_root_url, nav_label, nav_section, page_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (url) DO UPDATE SET updated_at = NOW()
        RETURNING id
        """,
        url, region, brand, nav_root_url, nav_label, nav_section, page_path,
    )
    return row["id"], True
