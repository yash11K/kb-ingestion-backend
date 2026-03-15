"""Knowledge base query endpoints — search and RAG chat, both SSE-streamed.

POST /kb/search  — full-text retrieval, streams ranked results
POST /kb/chat    — retrieval + Bedrock generation, streams tokens
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

router = APIRouter(tags=["knowledge-base"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class KBSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)


class KBChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    context_limit: int = Field(default=5, ge=1, le=20)


@router.post("/kb/search")
async def kb_search(body: KBSearchRequest, request: Request) -> StreamingResponse:
    """Stream search results from the knowledge base as SSE events."""
    svc = request.app.state.kb_query_service
    return StreamingResponse(
        svc.search(body.query, limit=body.limit),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/kb/chat")
async def kb_chat(body: KBChatRequest, request: Request) -> StreamingResponse:
    """RAG endpoint — retrieve context then stream Bedrock generation as SSE."""
    svc = request.app.state.kb_query_service
    return StreamingResponse(
        svc.chat(body.query, limit=body.context_limit),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
