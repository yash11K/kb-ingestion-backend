"""Uniqueness review workbench API endpoints.

Provides the backend for a UI where reviewers can:
- See documents similar to a given file (semantic + local search)
- View individual comparison insights between two documents
- Take actions on similar documents (edit, delete, dismiss)
- Get a full uniqueness review session with ranked similar docs

GET  /files/{file_id}/similar                          – ranked similar documents
GET  /files/{file_id}/similar/{similar_file_id}        – pairwise comparison detail
POST /files/{file_id}/similar/{similar_file_id}/action – act on a similar doc
POST /files/{file_id}/uniqueness-review                – full review session with AI insights
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

import boto3
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import KBFile
from src.db.queries import get_kb_file, update_kb_file_status
from src.models.schemas import FileStatus

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SimilarDocSummary(BaseModel):
    file_id: UUID
    title: str | None
    source_url: str
    content_type: str | None
    doc_type: str | None
    region: str
    brand: str
    status: FileStatus
    similarity_score: float
    content_snippet: str
    match_source: str = "bedrock_kb"


class SimilarDocsResponse(BaseModel):
    file_id: UUID
    file_title: str | None
    total_similar: int
    similar_documents: list[SimilarDocSummary]
    uniqueness_insight: str | None = None


class FileSnapshot(BaseModel):
    file_id: UUID
    title: str | None
    source_url: str
    content_type: str | None
    doc_type: str | None
    region: str
    brand: str
    status: FileStatus
    md_content: str | None
    validation_score: float | None
    uniqueness_insight: str | None


class PairwiseComparison(BaseModel):
    source_file: FileSnapshot
    similar_file: FileSnapshot
    comparison_insight: str
    overlap_areas: list[str]
    unique_to_source: list[str]
    unique_to_similar: list[str]
    recommendation: str  # keep_both | merge | delete_similar | delete_source


class SimilarDocAction(str, Enum):
    DISMISS = "dismiss"
    DELETE = "delete"
    REJECT = "reject"
    KEEP_BOTH = "keep_both"


class SimilarDocActionRequest(BaseModel):
    action: SimilarDocAction
    reviewed_by: str
    notes: str = ""


class SimilarDocActionResponse(BaseModel):
    file_id: UUID
    similar_file_id: UUID
    action: SimilarDocAction
    result: str


class SimilarDocWithInsight(BaseModel):
    file_id: UUID
    title: str | None
    source_url: str
    content_type: str | None
    doc_type: str | None
    region: str
    brand: str
    status: FileStatus
    similarity_score: float
    content_snippet: str
    md_content: str | None
    validation_score: float | None
    comparison_insight: str


class UniquenessReviewSession(BaseModel):
    file_id: UUID
    file_title: str | None
    file_source_url: str
    file_status: FileStatus
    file_md_content: str | None
    file_validation_score: float | None
    file_uniqueness_insight: str | None
    similar_documents: list[SimilarDocWithInsight]
    session_summary: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_s3_uri(uri: str) -> tuple[str, str] | None:
    if not uri.startswith("s3://"):
        return None
    without_prefix = uri[5:]
    slash = without_prefix.find("/")
    if slash <= 0:
        return None
    return without_prefix[:slash], without_prefix[slash + 1:]


async def _find_similar_via_bedrock(
    settings: Settings,
    document_content: str,
    source_url: str,
    limit: int = 10,
) -> list[dict]:
    """Query Bedrock KB for semantically similar documents."""
    if not settings.bedrock_kb_id:
        return []

    query_text = document_content[:2000]
    client = boto3.client("bedrock-agent-runtime", region_name=settings.aws_region)

    try:
        response = await asyncio.to_thread(
            client.retrieve,
            knowledgeBaseId=settings.bedrock_kb_id,
            retrievalQuery={"text": query_text},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": limit}
            },
        )
    except Exception:
        logger.exception("Bedrock KB retrieve error during similarity search")
        return []

    results = []
    for item in response.get("retrievalResults", []):
        content = item.get("content", {}).get("text", "")
        location = item.get("location", {})
        s3_uri = location.get("s3Location", {}).get("uri", "")
        score = float(item.get("score", 0.0))
        metadata = item.get("metadata", {})
        doc_source_url = metadata.get("source_url", "")

        # Skip self-matches
        if source_url and doc_source_url == source_url:
            continue

        results.append({
            "content_snippet": content[:500],
            "similarity_score": score,
            "s3_uri": s3_uri,
            "source_url": doc_source_url,
            "title": metadata.get("title", ""),
        })

    return results


async def _resolve_kb_file_from_s3_key(
    session: AsyncSession, s3_key: str
) -> dict | None:
    """Look up a KBFile by its s3_key."""
    result = await session.execute(
        select(KBFile).where(KBFile.s3_key == s3_key).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


async def _resolve_kb_file_from_source_url(
    session: AsyncSession, source_url: str
) -> dict | None:
    """Look up a KBFile by source_url (best effort)."""
    result = await session.execute(
        select(KBFile)
        .where(KBFile.source_url == source_url)
        .order_by(KBFile.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


async def _resolve_similar_to_kb_files(
    session_factory: async_sessionmaker,
    bedrock_results: list[dict],
) -> list[tuple[dict, dict]]:
    """Match Bedrock KB results back to local KBFile records.

    Returns list of (bedrock_result, kb_file_record) tuples.
    """
    matched = []
    async with session_factory() as session:
        for br in bedrock_results:
            record = None
            # Try S3 key first
            parsed = _parse_s3_uri(br.get("s3_uri", ""))
            if parsed:
                record = await _resolve_kb_file_from_s3_key(session, parsed[1])
            # Fallback to source_url
            if record is None and br.get("source_url"):
                record = await _resolve_kb_file_from_source_url(
                    session, br["source_url"]
                )
            if record is not None:
                matched.append((br, record))
    return matched


def _record_to_snapshot(record: dict) -> FileSnapshot:
    return FileSnapshot(
        file_id=record["id"],
        title=record.get("title"),
        source_url=record["source_url"],
        content_type=record.get("content_type"),
        doc_type=record.get("doc_type"),
        region=record["region"],
        brand=record["brand"],
        status=FileStatus(record["status"]),
        md_content=record.get("md_content"),
        validation_score=record.get("validation_score"),
        uniqueness_insight=record.get("uniqueness_insight"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/files/{file_id}/similar")
async def get_similar_documents(
    file_id: UUID,
    request: Request,
    limit: int = 10,
) -> SimilarDocsResponse:
    """Find documents semantically similar to the given file.

    Uses Bedrock KB vector search when available, falls back to local
    Postgres full-text search.
    """
    settings: Settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        record = await get_kb_file(session, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")

    md_body = record.get("md_content", "") or ""
    source_url = record.get("source_url", "")

    similar_docs: list[SimilarDocSummary] = []

    # --- Bedrock KB semantic search ---
    bedrock_results = await _find_similar_via_bedrock(
        settings, md_body, source_url, limit
    )
    if bedrock_results:
        matched = await _resolve_similar_to_kb_files(session_factory, bedrock_results)
        for br, kb_rec in matched:
            similar_docs.append(SimilarDocSummary(
                file_id=kb_rec["id"],
                title=kb_rec.get("title"),
                source_url=kb_rec["source_url"],
                content_type=kb_rec.get("content_type"),
                doc_type=kb_rec.get("doc_type"),
                region=kb_rec["region"],
                brand=kb_rec["brand"],
                status=FileStatus(kb_rec["status"]),
                similarity_score=br["similarity_score"],
                content_snippet=br["content_snippet"],
                match_source="bedrock_kb",
            ))

    # --- Local fallback if Bedrock returned nothing ---
    if not similar_docs:
        async with session_factory() as session:
            local_results = await _local_similarity_search(
                session, file_id, md_body, limit
            )
        for lr in local_results:
            similar_docs.append(SimilarDocSummary(
                file_id=lr["id"],
                title=lr.get("title"),
                source_url=lr["source_url"],
                content_type=lr.get("content_type"),
                doc_type=lr.get("doc_type"),
                region=lr["region"],
                brand=lr["brand"],
                status=FileStatus(lr["status"]),
                similarity_score=lr.get("rank", 0.0),
                content_snippet=(lr.get("md_content") or "")[:500],
                match_source="local_search",
            ))

    # Sort by similarity descending
    similar_docs.sort(key=lambda d: d.similarity_score, reverse=True)

    return SimilarDocsResponse(
        file_id=file_id,
        file_title=record.get("title"),
        total_similar=len(similar_docs),
        similar_documents=similar_docs,
        uniqueness_insight=record.get("uniqueness_insight"),
    )


async def _local_similarity_search(
    session: AsyncSession,
    exclude_file_id: UUID,
    query_text: str,
    limit: int,
) -> list[dict]:
    """Postgres full-text search fallback for finding similar docs."""
    from sqlalchemy import text as sa_text

    # Use first 500 chars as the search query
    search_query = query_text[:500].replace("'", "''")
    sql = sa_text("""
        SELECT id, title, filename, content_type, component_type, doc_type,
               source_url, region, brand, status, md_content, validation_score,
               ts_rank_cd(search_vector, query) AS rank
        FROM kb_files, plainto_tsquery('english', :query) query
        WHERE search_vector @@ query
          AND id != :exclude_id
        ORDER BY rank DESC
        LIMIT :limit
    """)
    result = await session.execute(sql, {
        "query": search_query,
        "exclude_id": str(exclude_file_id),
        "limit": limit,
    })
    return [dict(row._mapping) for row in result]


@router.get("/files/{file_id}/similar/{similar_file_id}")
async def get_pairwise_comparison(
    file_id: UUID,
    similar_file_id: UUID,
    request: Request,
) -> PairwiseComparison:
    """Get a detailed AI-generated comparison between two documents.

    Calls the validator agent to produce a structured comparison insight
    covering overlap areas, unique aspects, and a recommendation.
    """
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        source_rec = await get_kb_file(session, file_id)
        similar_rec = await get_kb_file(session, similar_file_id)

    if source_rec is None:
        raise HTTPException(status_code=404, detail="Source file not found")
    if similar_rec is None:
        raise HTTPException(status_code=404, detail="Similar file not found")

    # Generate comparison via Bedrock
    settings: Settings = request.app.state.settings
    comparison = await _generate_pairwise_insight(
        settings,
        source_rec.get("md_content", ""),
        similar_rec.get("md_content", ""),
        source_rec.get("title", ""),
        similar_rec.get("title", ""),
    )

    return PairwiseComparison(
        source_file=_record_to_snapshot(source_rec),
        similar_file=_record_to_snapshot(similar_rec),
        comparison_insight=comparison.get("insight", ""),
        overlap_areas=comparison.get("overlap_areas", []),
        unique_to_source=comparison.get("unique_to_source", []),
        unique_to_similar=comparison.get("unique_to_similar", []),
        recommendation=comparison.get("recommendation", "keep_both"),
    )


async def _generate_pairwise_insight(
    settings: Settings,
    source_content: str,
    similar_content: str,
    source_title: str,
    similar_title: str,
) -> dict:
    """Use Bedrock to generate a structured comparison between two documents."""
    import json

    prompt = f"""Compare these two documents and provide a structured analysis.

Document A (source): "{source_title}"
---
{source_content[:3000]}
---

Document B (similar): "{similar_title}"
---
{similar_content[:3000]}
---

Return a JSON object with exactly these keys:
- "insight": 2-3 sentence summary of the relationship between these documents
- "overlap_areas": list of strings describing topics/concepts covered by both
- "unique_to_source": list of strings describing what's only in Document A
- "unique_to_similar": list of strings describing what's only in Document B
- "recommendation": one of "keep_both", "merge", "delete_similar", "delete_source"

Return ONLY the JSON object, no other text."""

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)

    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=settings.haiku_model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
        )
        text = response["output"]["message"]["content"][0]["text"]
        # Extract JSON from response
        return _extract_json(text)
    except Exception:
        logger.exception("Failed to generate pairwise comparison insight")
        return {
            "insight": "Comparison could not be generated.",
            "overlap_areas": [],
            "unique_to_source": [],
            "unique_to_similar": [],
            "recommendation": "keep_both",
        }


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from LLM response."""
    import json
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try finding JSON block
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {}


@router.post("/files/{file_id}/similar/{similar_file_id}/action")
async def act_on_similar_document(
    file_id: UUID,
    similar_file_id: UUID,
    body: SimilarDocActionRequest,
    request: Request,
) -> SimilarDocActionResponse:
    """Take an action on a similar document from the review workbench.

    Actions:
    - dismiss: mark as reviewed, no action needed
    - delete: soft-delete the similar document (set status to rejected)
    - reject: reject the similar document with review notes
    - keep_both: explicitly confirm both documents should coexist
    """
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        source_rec = await get_kb_file(session, file_id)
        similar_rec = await get_kb_file(session, similar_file_id)

    if source_rec is None:
        raise HTTPException(status_code=404, detail="Source file not found")
    if similar_rec is None:
        raise HTTPException(status_code=404, detail="Similar file not found")

    now = datetime.now(timezone.utc)
    result_msg = ""

    async with session_factory() as session:
        if body.action == SimilarDocAction.DISMISS:
            # No status change, just log the review decision
            result_msg = "Similar document dismissed — no action taken"

        elif body.action == SimilarDocAction.DELETE:
            await update_kb_file_status(
                session,
                similar_file_id,
                FileStatus.REJECTED.value,
                reviewed_by=body.reviewed_by,
                reviewed_at=now,
                review_notes=f"Deleted via uniqueness review (similar to {file_id}). {body.notes}".strip(),
            )
            result_msg = "Similar document rejected/deleted"

        elif body.action == SimilarDocAction.REJECT:
            await update_kb_file_status(
                session,
                similar_file_id,
                FileStatus.REJECTED.value,
                reviewed_by=body.reviewed_by,
                reviewed_at=now,
                review_notes=f"Rejected via uniqueness review (similar to {file_id}). {body.notes}".strip(),
            )
            result_msg = "Similar document rejected"

        elif body.action == SimilarDocAction.KEEP_BOTH:
            result_msg = "Both documents confirmed to coexist"

        await session.commit()

    return SimilarDocActionResponse(
        file_id=file_id,
        similar_file_id=similar_file_id,
        action=body.action,
        result=result_msg,
    )


@router.post("/files/{file_id}/uniqueness-review")
async def generate_uniqueness_review_session(
    file_id: UUID,
    request: Request,
    limit: int = 5,
) -> UniquenessReviewSession:
    """Generate a full uniqueness review session.

    Finds similar documents, generates per-document comparison insights,
    and produces an overall session summary. This is the main endpoint
    the review UI calls to populate the workbench.
    """
    settings: Settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        record = await get_kb_file(session, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")

    md_body = record.get("md_content", "") or ""
    source_url = record.get("source_url", "")
    source_title = record.get("title", "")

    # 1. Find similar documents
    bedrock_results = await _find_similar_via_bedrock(
        settings, md_body, source_url, limit
    )
    matched = await _resolve_similar_to_kb_files(session_factory, bedrock_results)

    # Fallback to local search
    if not matched:
        async with session_factory() as session:
            local_results = await _local_similarity_search(
                session, file_id, md_body, limit
            )
        matched = [
            (
                {"similarity_score": lr.get("rank", 0.0), "content_snippet": (lr.get("md_content") or "")[:500]},
                lr,
            )
            for lr in local_results
        ]

    # 2. Generate per-document comparison insights in parallel
    async def _get_insight(br: dict, kb_rec: dict) -> SimilarDocWithInsight:
        comparison = await _generate_pairwise_insight(
            settings,
            md_body,
            kb_rec.get("md_content", ""),
            source_title,
            kb_rec.get("title", ""),
        )
        return SimilarDocWithInsight(
            file_id=kb_rec["id"],
            title=kb_rec.get("title"),
            source_url=kb_rec["source_url"],
            content_type=kb_rec.get("content_type"),
            doc_type=kb_rec.get("doc_type"),
            region=kb_rec["region"],
            brand=kb_rec["brand"],
            status=FileStatus(kb_rec["status"]),
            similarity_score=br.get("similarity_score", 0.0),
            content_snippet=br.get("content_snippet", "")[:500],
            md_content=kb_rec.get("md_content"),
            validation_score=kb_rec.get("validation_score"),
            comparison_insight=comparison.get("insight", ""),
        )

    similar_with_insights = await asyncio.gather(
        *[_get_insight(br, kb_rec) for br, kb_rec in matched]
    )

    # Sort by similarity descending
    similar_with_insights = sorted(
        similar_with_insights, key=lambda d: d.similarity_score, reverse=True
    )

    # 3. Generate session summary
    summary = _build_session_summary(
        source_title, len(similar_with_insights), similar_with_insights
    )

    return UniquenessReviewSession(
        file_id=file_id,
        file_title=record.get("title"),
        file_source_url=source_url,
        file_status=FileStatus(record["status"]),
        file_md_content=record.get("md_content"),
        file_validation_score=record.get("validation_score"),
        file_uniqueness_insight=record.get("uniqueness_insight"),
        similar_documents=list(similar_with_insights),
        session_summary=summary,
    )


def _build_session_summary(
    title: str,
    count: int,
    docs: list[SimilarDocWithInsight],
) -> str:
    """Build a human-readable summary for the review session."""
    if count == 0:
        return f'No similar documents found for "{title}". This content appears to be unique in the knowledge base.'

    high_sim = [d for d in docs if d.similarity_score > 0.7]
    med_sim = [d for d in docs if 0.4 <= d.similarity_score <= 0.7]

    parts = [f'Found {count} similar document(s) for "{title}".']
    if high_sim:
        parts.append(f"{len(high_sim)} with high similarity (>0.7) — review recommended.")
    if med_sim:
        parts.append(f"{len(med_sim)} with moderate similarity — may have partial overlap.")

    return " ".join(parts)
