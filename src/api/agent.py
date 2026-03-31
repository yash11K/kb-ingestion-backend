"""KB Agent endpoint — conversational system introspection via SSE.

POST /agent/chat  — streams agent responses as SSE events.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kb-agent"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class AgentMessage(BaseModel):
    role: str
    content: str


class AgentChatRequest(BaseModel):
    message: str
    conversation: list[AgentMessage] = Field(default_factory=list)


async def _stream_agent(agent, message: str, conversation: list[dict]):
    """Run the KB Agent and yield SSE events."""
    async for chunk in agent.chat(message, conversation or None):
        yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"
    yield f"event: done\ndata: {json.dumps({})}\n\n"


@router.post("/agent/chat")
async def agent_chat(body: AgentChatRequest, request: Request) -> StreamingResponse:
    """Stream KB Agent response as SSE."""
    agent = request.app.state.kb_agent
    conversation = [{"role": m.role, "content": m.content} for m in body.conversation]

    return StreamingResponse(
        _stream_agent(agent, body.message, conversation),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
