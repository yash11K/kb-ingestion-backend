"""SQLAlchemy ORM query functions for kb_files, ingestion_jobs, sources,
revalidation_jobs, nav_tree_cache, and deep_links tables."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    DeepLink,
    IngestionJob,
    KBFile,
    NavTreeCache,
    RevalidationJob,
    Source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_FILTERS = {"status", "region", "brand", "content_type", "component_type", "source_id"}


def _model_to_dict(instance) -> dict:
    """Convert a SQLAlchemy ORM model instance to a plain dict.

    JSONB columns (validation_breakdown, validation_issues, tree_data) are
    already deserialized by SQLAlchemy — no json.loads needed.
    """
    d = {}
    for col in instance.__table__.columns:
        d[col.name] = getattr(instance, col.name)
    return d


# ---------------------------------------------------------------------------
# kb_files queries
# ---------------------------------------------------------------------------


async def insert_kb_file(session: AsyncSession, file: dict) -> UUID:
    """Insert a new kb_file record and return its UUID.

    *file* must contain keys matching the kb_files columns (excluding id,
    created_at, updated_at which have defaults).
    """
    kb_file = KBFile(
        filename=file["filename"],
        title=file["title"],
        content_type=file["content_type"],
        content_hash=file["content_hash"],
        source_url=file["source_url"],
        component_type=file["component_type"],
        md_content=file["md_content"],
        parent_context=file.get("parent_context"),
        region=file["region"],
        brand=file["brand"],
        key=file.get("key", ""),
        namespace=file.get("namespace", ""),
        validation_score=file.get("validation_score"),
        validation_breakdown=file.get("validation_breakdown"),
        validation_issues=file.get("validation_issues"),
        status=file.get("status", "pending_review"),
        source_id=file.get("source_id"),
        job_id=file.get("job_id"),
    )
    session.add(kb_file)
    await session.flush()
    return kb_file.id


async def update_kb_file_status(
    session: AsyncSession, file_id: UUID, status: str, **kwargs
) -> None:
    """Update a kb_file's status and updated_at, plus any extra fields.

    Common extra kwargs: s3_bucket, s3_key, s3_uploaded_at, reviewed_by,
    reviewed_at, review_notes, validation_score, validation_breakdown,
    validation_issues, content_hash, md_content.
    """
    values = {"status": status, "updated_at": func.now()}
    for col, val in kwargs.items():
        values[col] = val

    stmt = update(KBFile).where(KBFile.id == file_id).values(**values)
    await session.execute(stmt)


async def get_kb_file(session: AsyncSession, file_id: UUID) -> dict | None:
    """Return a single kb_file record as a dict, or None."""
    result = await session.execute(select(KBFile).where(KBFile.id == file_id))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)


async def list_kb_files(
    session: AsyncSession, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated, filtered list of kb_files and the total count.

    Supported filter keys: status, region, brand, content_type, component_type, source_id.
    """
    conditions = _build_conditions(KBFile, filters)

    # Total count
    count_stmt = select(func.count()).select_from(KBFile)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = (await session.execute(count_stmt)).scalar_one()

    # Paginated results
    offset = (page - 1) * size
    data_stmt = select(KBFile).order_by(KBFile.created_at.desc()).limit(size).offset(offset)
    for cond in conditions:
        data_stmt = data_stmt.where(cond)
    result = await session.execute(data_stmt)
    rows = result.scalars().all()
    return [_model_to_dict(r) for r in rows], total


async def find_by_content_hash(session: AsyncSession, content_hash: str) -> dict | None:
    """Find a kb_file by its content_hash. Returns the first match or None."""
    result = await session.execute(
        select(KBFile).where(KBFile.content_hash == content_hash).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)


# ---------------------------------------------------------------------------
# Review queue query
# ---------------------------------------------------------------------------


async def list_review_queue(
    session: AsyncSession, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return paginated pending_review files with optional filters.

    Always filters by status='pending_review'. Additional filter keys:
    region, brand, content_type, component_type.
    """
    combined = {**filters, "status": "pending_review"}
    return await list_kb_files(session, combined, page, size)


# ---------------------------------------------------------------------------
# sources queries
# ---------------------------------------------------------------------------


async def find_or_create_source(
    session: AsyncSession, url: str, region: str, brand: str
) -> tuple[UUID, bool]:
    """Find an existing source by URL or create a new one.

    Returns (source_id, created) where *created* is True if a new row was
    inserted.
    """
    result = await session.execute(
        select(Source.id).where(Source.url == url)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing, False

    stmt = (
        pg_insert(Source)
        .values(url=url, region=region, brand=brand)
        .on_conflict_do_update(
            index_elements=["url"],
            set_={"updated_at": func.now()},
        )
        .returning(Source.id)
    )
    result = await session.execute(stmt)
    source_id = result.scalar_one()
    return source_id, True


async def find_or_create_source_enriched(
    session: AsyncSession,
    url: str,
    region: str,
    brand: str,
    nav_root_url: str | None = None,
    nav_label: str | None = None,
    nav_section: str | None = None,
    page_path: str | None = None,
) -> tuple[UUID, bool]:
    """Find or create a source with optional nav context enrichment."""
    result = await session.execute(
        select(Source.id).where(Source.url == url)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        # Update nav context if provided
        if nav_label or nav_section:
            stmt = (
                update(Source)
                .where(Source.id == existing)
                .values(
                    nav_root_url=func.coalesce(nav_root_url, Source.nav_root_url),
                    nav_label=func.coalesce(nav_label, Source.nav_label),
                    nav_section=func.coalesce(nav_section, Source.nav_section),
                    page_path=func.coalesce(page_path, Source.page_path),
                    updated_at=func.now(),
                )
            )
            await session.execute(stmt)
        return existing, False

    stmt = (
        pg_insert(Source)
        .values(
            url=url,
            region=region,
            brand=brand,
            nav_root_url=nav_root_url,
            nav_label=nav_label,
            nav_section=nav_section,
            page_path=page_path,
        )
        .on_conflict_do_update(
            index_elements=["url"],
            set_={"updated_at": func.now()},
        )
        .returning(Source.id)
    )
    result = await session.execute(stmt)
    source_id = result.scalar_one()
    return source_id, True


async def get_source(session: AsyncSession, source_id: UUID) -> dict | None:
    """Return a single source record as a dict, or None."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)


async def list_sources(
    session: AsyncSession, filters: dict, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated, filtered list of sources and the total count.

    Supported filter keys: region, brand.
    """
    allowed = {"region", "brand"}
    conditions = []
    for key, val in filters.items():
        if key in allowed and val is not None:
            conditions.append(getattr(Source, key) == val)

    count_stmt = select(func.count()).select_from(Source)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = (await session.execute(count_stmt)).scalar_one()

    offset = (page - 1) * size
    data_stmt = select(Source).order_by(Source.created_at.desc()).limit(size).offset(offset)
    for cond in conditions:
        data_stmt = data_stmt.where(cond)
    result = await session.execute(data_stmt)
    rows = result.scalars().all()
    return [_model_to_dict(r) for r in rows], total


async def update_source_last_ingested(session: AsyncSession, source_id: UUID) -> None:
    """Touch the last_ingested_at timestamp on a source."""
    stmt = (
        update(Source)
        .where(Source.id == source_id)
        .values(last_ingested_at=func.now(), updated_at=func.now())
    )
    await session.execute(stmt)


async def list_jobs_for_source(
    session: AsyncSession, source_id: UUID, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated list of ingestion jobs for a specific source."""
    count_stmt = (
        select(func.count())
        .select_from(IngestionJob)
        .where(IngestionJob.source_id == source_id)
    )
    total = (await session.execute(count_stmt)).scalar_one()

    offset = (page - 1) * size
    data_stmt = (
        select(IngestionJob)
        .where(IngestionJob.source_id == source_id)
        .order_by(IngestionJob.started_at.desc())
        .limit(size)
        .offset(offset)
    )
    result = await session.execute(data_stmt)
    rows = result.scalars().all()
    return [_model_to_dict(r) for r in rows], total


async def get_source_stats(session: AsyncSession, source_id: UUID) -> dict:
    """Return aggregate stats for a single source."""
    job_stmt = select(
        func.count().label("total_jobs"),
        func.count().filter(IngestionJob.status == "completed").label("completed_jobs"),
        func.count().filter(IngestionJob.status == "failed").label("failed_jobs"),
        func.count().filter(IngestionJob.status == "in_progress").label("active_jobs"),
    ).where(IngestionJob.source_id == source_id)
    job_row = (await session.execute(job_stmt)).one()

    file_stmt = select(
        func.count().label("total_files"),
        func.count().filter(KBFile.status == "pending_review").label("pending_review"),
        func.count().filter(KBFile.status.in_(["approved", "in_s3"])).label("approved"),
        func.count().filter(KBFile.status.in_(["rejected", "auto_rejected"])).label("rejected"),
    ).where(KBFile.source_id == source_id)
    file_row = (await session.execute(file_stmt)).one()

    return {**job_row._asdict(), **file_row._asdict()}


# ---------------------------------------------------------------------------
# ingestion_jobs queries
# ---------------------------------------------------------------------------


async def insert_ingestion_job(
    session: AsyncSession,
    source_url: str,
    source_id: UUID | None = None,
    max_depth: int = 0,
) -> UUID:
    """Create a new ingestion job with status 'in_progress' and return its id.

    *max_depth* is the effective (clamped) crawl depth for this job.
    """
    job = IngestionJob(
        source_url=source_url,
        source_id=source_id,
        status="in_progress",
        max_depth=max_depth,
    )
    session.add(job)
    await session.flush()
    return job.id


async def update_ingestion_job(
    session: AsyncSession, job_id: UUID, **kwargs
) -> None:
    """Update any provided fields on an ingestion job."""
    if not kwargs:
        return

    stmt = update(IngestionJob).where(IngestionJob.id == job_id).values(**kwargs)
    await session.execute(stmt)


async def update_crawl_progress(
    session: AsyncSession, job_id: UUID, pages_crawled: int, current_depth: int
) -> None:
    """Update crawl progress counters on an ingestion job.

    Convenience wrapper around update_ingestion_job for the BFS crawl loop.
    """
    stmt = (
        update(IngestionJob)
        .where(IngestionJob.id == job_id)
        .values(pages_crawled=pages_crawled, current_depth=current_depth)
    )
    await session.execute(stmt)


async def get_ingestion_job(session: AsyncSession, job_id: UUID) -> dict | None:
    """Return a single ingestion_job record as a dict, or None."""
    result = await session.execute(
        select(IngestionJob).where(IngestionJob.id == job_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)


async def list_ingestion_jobs(
    session: AsyncSession, page: int, size: int
) -> tuple[list[dict], int]:
    """Return a paginated list of ingestion jobs ordered by started_at DESC."""
    count_stmt = select(func.count()).select_from(IngestionJob)
    total = (await session.execute(count_stmt)).scalar_one()

    offset = (page - 1) * size
    data_stmt = (
        select(IngestionJob)
        .order_by(IngestionJob.started_at.desc())
        .limit(size)
        .offset(offset)
    )
    result = await session.execute(data_stmt)
    rows = result.scalars().all()
    return [_model_to_dict(r) for r in rows], total


async def get_active_jobs(session: AsyncSession) -> dict[str, str]:
    """Return {source_id: job_id} for all in-progress ingestion jobs."""
    stmt = (
        select(IngestionJob.source_id, IngestionJob.id)
        .where(IngestionJob.status == "in_progress")
        .where(IngestionJob.source_id.is_not(None))
        .order_by(IngestionJob.started_at.desc())
    )
    result = await session.execute(stmt)
    rows = result.all()
    return {str(r[0]): str(r[1]) for r in rows}


# ---------------------------------------------------------------------------
# revalidation_jobs queries
# ---------------------------------------------------------------------------


async def insert_revalidation_job(session: AsyncSession, total_files: int) -> UUID:
    """Create a new revalidation job with status 'in_progress' and return its id."""
    job = RevalidationJob(
        total_files=total_files,
        status="in_progress",
    )
    session.add(job)
    await session.flush()
    return job.id


async def update_revalidation_job(
    session: AsyncSession, job_id: UUID, **kwargs
) -> None:
    """Update any provided fields on a revalidation job."""
    if not kwargs:
        return

    stmt = (
        update(RevalidationJob)
        .where(RevalidationJob.id == job_id)
        .values(**kwargs)
    )
    await session.execute(stmt)


async def get_revalidation_job(session: AsyncSession, job_id: UUID) -> dict | None:
    """Return a single revalidation_job record as a dict, or None."""
    result = await session.execute(
        select(RevalidationJob).where(RevalidationJob.id == job_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)


# ---------------------------------------------------------------------------
# nav_tree_cache queries
# ---------------------------------------------------------------------------


async def upsert_nav_tree_cache(
    session: AsyncSession,
    root_url: str,
    brand: str,
    region: str,
    tree_data: dict,
    ttl_hours: int = 24,
) -> None:
    """Insert or update a cached navigation tree."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    stmt = (
        pg_insert(NavTreeCache)
        .values(
            root_url=root_url,
            brand=brand,
            region=region,
            tree_data=tree_data,
            fetched_at=func.now(),
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["root_url"],
            set_={
                "brand": brand,
                "region": region,
                "tree_data": tree_data,
                "fetched_at": func.now(),
                "expires_at": expires_at,
            },
        )
    )
    await session.execute(stmt)


async def get_nav_tree_cache(session: AsyncSession, root_url: str) -> dict | None:
    """Return cached nav tree data if present and not expired, else None."""
    result = await session.execute(
        select(NavTreeCache.tree_data).where(
            NavTreeCache.root_url == root_url,
            NavTreeCache.expires_at > func.now(),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row


# ---------------------------------------------------------------------------
# deep_links queries
# ---------------------------------------------------------------------------


async def insert_deep_links(session: AsyncSession, links: list[dict]) -> None:
    """Batch insert discovered deep links, skipping duplicates per source."""
    if not links:
        return
    records = [
        {
            "source_id": link.get("source_id"),
            "job_id": link.get("job_id"),
            "url": link["url"],
            "model_json_url": link["model_json_url"],
            "anchor_text": link.get("anchor_text", ""),
            "found_in_node": link.get("found_in_node", ""),
            "found_in_page": link["found_in_page"],
            "status": "pending",
        }
        for link in links
    ]
    stmt = pg_insert(DeepLink).values(records)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_deep_links_source_url")
    await session.execute(stmt)


async def list_deep_links(
    session: AsyncSession,
    source_id: UUID,
    status: str = "pending",
) -> list[dict]:
    """Return deep links for a source filtered by status."""
    stmt = (
        select(
            DeepLink.id,
            DeepLink.url,
            DeepLink.model_json_url,
            DeepLink.anchor_text,
            DeepLink.found_in_node,
            DeepLink.found_in_page,
            DeepLink.status,
            DeepLink.created_at,
        )
        .where(DeepLink.source_id == source_id, DeepLink.status == status)
        .order_by(DeepLink.created_at.desc())
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [r._asdict() for r in rows]


async def list_all_deep_links(
    session: AsyncSession,
    status: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[dict], int]:
    """Return all deep links across all sources, optionally filtered by status.

    Returns (rows, total_count) for pagination.
    """
    conditions = []
    if status:
        conditions.append(DeepLink.status == status)

    count_stmt = select(func.count()).select_from(DeepLink)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = (await session.execute(count_stmt)).scalar_one()

    offset = (page - 1) * size
    data_stmt = (
        select(
            DeepLink.id,
            DeepLink.source_id,
            DeepLink.url,
            DeepLink.model_json_url,
            DeepLink.anchor_text,
            DeepLink.found_in_node,
            DeepLink.found_in_page,
            DeepLink.status,
            DeepLink.created_at,
        )
        .order_by(DeepLink.created_at.desc())
        .limit(size)
        .offset(offset)
    )
    for cond in conditions:
        data_stmt = data_stmt.where(cond)
    result = await session.execute(data_stmt)
    rows = result.all()
    return [r._asdict() for r in rows], total


async def bulk_update_deep_link_status(
    session: AsyncSession,
    link_ids: list[UUID],
    status: str,
) -> None:
    """Update status for multiple deep links."""
    stmt = (
        update(DeepLink)
        .where(DeepLink.id.in_(link_ids))
        .values(status=status)
    )
    await session.execute(stmt)


async def insert_deep_link_ingestion_jobs(
    session: AsyncSession,
    source_id: UUID,
    link_ids: list[UUID],
) -> list[tuple[UUID, str]]:
    """Create one ingestion job per confirmed deep link.

    Returns a list of (job_id, model_json_url) tuples — one per link.
    """
    # Get the model.json URLs for confirmed links
    result = await session.execute(
        select(DeepLink.id, DeepLink.model_json_url).where(
            DeepLink.id.in_(link_ids)
        )
    )
    rows = result.all()

    if not rows:
        raise ValueError("No valid deep link URLs found for the given IDs")

    results: list[tuple[UUID, str]] = []

    for row in rows:
        link_id = row[0]
        url = row[1]

        # Create a dedicated job for this link
        job = IngestionJob(
            source_url=url,
            source_id=source_id,
            status="in_progress",
        )
        session.add(job)
        await session.flush()

        # Mark this individual link as ingested
        await bulk_update_deep_link_status(session, [link_id], "ingested")

        results.append((job.id, url))

    return results


# ---------------------------------------------------------------------------
# Bulk source lookup by URL
# ---------------------------------------------------------------------------


async def lookup_sources_by_urls(
    session: AsyncSession, urls: list[str]
) -> list[dict]:
    """Look up sources by their URLs and return each with aggregate file stats.

    Returns a list of dicts with keys: source_id, url, last_ingested_at,
    total_files, approved, pending_review, rejected.
    Only returns entries for URLs that have a matching source.
    """
    if not urls:
        return []

    # Join sources with file counts in a single query
    stmt = (
        select(
            Source.id.label("source_id"),
            Source.url,
            Source.last_ingested_at,
            func.count(KBFile.id).label("total_files"),
            func.count().filter(KBFile.status.in_(["approved", "in_s3"])).label("approved"),
            func.count().filter(KBFile.status == "pending_review").label("pending_review"),
            func.count().filter(KBFile.status.in_(["rejected", "auto_rejected"])).label("rejected"),
        )
        .outerjoin(KBFile, KBFile.source_id == Source.id)
        .where(Source.url.in_(urls))
        .group_by(Source.id)
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [r._asdict() for r in rows]


# ---------------------------------------------------------------------------
# Stats query
# ---------------------------------------------------------------------------


async def get_stats(session: AsyncSession) -> dict:
    """Return aggregate stats across all kb_files."""
    stmt = select(
        func.count().label("total_files"),
        func.count().filter(KBFile.status == "pending_review").label("pending_review"),
        func.count().filter(KBFile.status.in_(["approved", "in_s3"])).label("approved"),
        func.count().filter(KBFile.status.in_(["rejected", "auto_rejected"])).label("rejected"),
        func.coalesce(func.avg(KBFile.validation_score), 0.0).label("avg_score"),
    ).select_from(KBFile)
    row = (await session.execute(stmt)).one()
    return row._asdict()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_conditions(model, filters: dict) -> list:
    """Build SQLAlchemy WHERE conditions from a filter dict."""
    conditions = []
    for key, val in filters.items():
        if key in _ALLOWED_FILTERS and val is not None:
            conditions.append(getattr(model, key) == val)
    return conditions
