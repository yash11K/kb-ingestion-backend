"""Context Agent endpoint – proactive file analysis with follow-up Q&A.

POST /context/chat  — streams Haiku analysis (or cached result) via SSE.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from src.db.queries import get_kb_file, list_deep_links

logger = logging.getLogger(__name__)

router = APIRouter(tags=["context-agent"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ContextMessage(BaseModel):
    role: str
    content: str


class ContextChatRequest(BaseModel):
    file_id: UUID
    conversation: list[ContextMessage] = Field(default_factory=list)


async def _build_cache_key(
    session, file_id: UUID, cache
) -> tuple[str | None, dict | None, list[dict]]:
    """Fetch current file state and compute a cache key.

    Returns (cache_key, file_record, deep_links).
    """
    record = await get_kb_file(session, file_id)
    if record is None:
        return None, None, []

    deep_links: list[dict] = []
    source_id = record.get("source_id")
    source_url = record.get("source_url")
    if source_id:
        for status in ("pending", "confirmed"):
            deep_links.extend(await list_deep_links(session, source_id, status, found_in_page=source_url))

    key = cache.make_key(
        file_id=str(file_id),
        content_hash=record.get("content_hash", ""),
        validation_score=record.get("validation_score"),
        status=record.get("status", ""),
        deep_link_states=[
            {"url": d.get("url", ""), "status": d.get("status", "")}
            for d in deep_links
        ],
    )
    return key, record, deep_links


async def _stream_cached(analysis: str) -> AsyncGenerator[str, None]:
    """Yield a cached analysis as SSE events (simulates streaming)."""
    # Send in chunks to give a natural feel
    chunk_size = 80
    for i in range(0, len(analysis), chunk_size):
        chunk = analysis[i : i + chunk_size]
        yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"
    yield f"event: done\ndata: {json.dumps({})}\n\n"


async def _stream_agent(
    agent, file_id: str, conversation: list[dict], cache, cache_key: str | None
) -> AsyncGenerator[str, None]:
    """Run the Context Agent and stream tokens as SSE events."""
    full_response: list[str] = []
    async for chunk in agent.chat(file_id, conversation):
        full_response.append(chunk)
        yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"

    # Cache the initial analysis (not follow-ups)
    if not conversation and cache_key:
        cache.set(cache_key, "".join(full_response))

    yield f"event: done\ndata: {json.dumps({})}\n\n"


@router.post("/context/chat")
async def context_chat(body: ContextChatRequest, request: Request) -> StreamingResponse:
    """Stream Context Agent analysis or follow-up response as SSE."""
    agent = request.app.state.context_agent
    cache = request.app.state.context_cache

    # For initial analysis (empty conversation), check cache first
    cache_key = None
    if not body.conversation:
        async with request.app.state.session_factory() as session:
            cache_key, record, deep_links = await _build_cache_key(
                session, body.file_id, cache
            )
            await session.commit()
        if cache_key:
            cached = cache.get(cache_key)
            if cached:
                logger.info("Context agent cache hit for file %s", body.file_id)
                return StreamingResponse(
                    _stream_cached(cached),
                    media_type="text/event-stream",
                    headers=_SSE_HEADERS,
                )

    conversation = [{"role": m.role, "content": m.content} for m in body.conversation]

    return StreamingResponse(
        _stream_agent(agent, str(body.file_id), conversation, cache, cache_key),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
