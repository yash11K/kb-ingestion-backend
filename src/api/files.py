"""Files listing API endpoints.

GET /files           – paginated list of all files with filters.
GET /files/{file_id} – full FileDetail for a file, 404 if not found.
"""

from __future__ import annotations

from math import ceil
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from src.db.queries import get_kb_file, list_kb_files
from src.models.schemas import (
    FileDetail,
    FileStatus,
    FileSummary,
    PaginatedResponse,
    ValidationBreakdown,
)

router = APIRouter()


@router.get("/files")
async def list_files(
    request: Request,
    status: str | None = None,
    region: str | None = None,
    brand: str | None = None,
    content_type: str | None = None,
    component_type: str | None = None,
    source_id: UUID | None = None,
    page: int = 1,
    size: int = 20,
) -> PaginatedResponse[FileSummary]:
    """Return a paginated list of all files with optional filters."""
    filters: dict = {}
    if status is not None:
        filters["status"] = status
    if region is not None:
        filters["region"] = region
    if brand is not None:
        filters["brand"] = brand
    if content_type is not None:
        filters["content_type"] = content_type
    if component_type is not None:
        filters["component_type"] = component_type
    if source_id is not None:
        filters["source_id"] = source_id

    async with request.app.state.session_factory() as session:
        rows, total = await list_kb_files(session, filters, page, size)
        await session.commit()

    items = [
        FileSummary(
            id=r["id"],
            filename=r["filename"],
            title=r["title"],
            content_type=r["content_type"],
            status=FileStatus(r["status"]),
            source_id=r["source_id"],
            region=r["region"],
            brand=r["brand"],
            validation_score=r.get("validation_score"),
            created_at=r["created_at"],
        )
        for r in rows
    ]

    pages = ceil(total / size) if size > 0 else 0

    return PaginatedResponse[FileSummary](
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/files/{file_id}")
async def get_file(file_id: UUID, request: Request) -> FileDetail:
    """Return the full detail for a file, or 404 if not found."""
    async with request.app.state.session_factory() as session:
        record = await get_kb_file(session, file_id)
        await session.commit()
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Parse validation_breakdown if present
    breakdown = None
    if record.get("validation_breakdown") is not None:
        breakdown = ValidationBreakdown(**record["validation_breakdown"])

    return FileDetail(
        id=record["id"],
        filename=record["filename"],
        title=record["title"],
        content_type=record["content_type"],
        content_hash=record["content_hash"],
        source_url=record["source_url"],
        component_type=record["component_type"],
        source_id=record.get("source_id"),
        aem_node_id=record["aem_node_id"],
        md_content=record["md_content"],
        modify_date=record["modify_date"],
        parent_context=record["parent_context"],
        region=record["region"],
        brand=record["brand"],
        doc_type=record.get("doc_type"),
        validation_score=record.get("validation_score"),
        validation_breakdown=breakdown,
        validation_issues=record.get("validation_issues"),
        status=FileStatus(record["status"]),
        s3_bucket=record.get("s3_bucket"),
        s3_key=record.get("s3_key"),
        s3_uploaded_at=record.get("s3_uploaded_at"),
        reviewed_by=record.get("reviewed_by"),
        reviewed_at=record.get("reviewed_at"),
        review_notes=record.get("review_notes"),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )
