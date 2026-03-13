"""SSE streaming endpoint for real-time pipeline events.

GET /ingest/{job_id}/stream — streams agent logs, tool calls, and progress
events as Server-Sent Events until the pipeline completes or the client
disconnects.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ingest/{job_id}/stream")
async def stream_job(job_id: UUID, request: Request) -> StreamingResponse:
    """Stream pipeline events for a job as SSE."""
    stream_manager = request.app.state.stream_manager
    queue = stream_manager.subscribe(job_id)

    if queue is None:
        raise HTTPException(
            status_code=404,
            detail="No active stream for this job. The job may have already completed.",
        )

    async def event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
                    continue

                payload = {
                    **event.data,
                    "timestamp": event.timestamp.isoformat(),
                }
                yield f"event: {event.event}\ndata: {json.dumps(payload)}\n\n"

                if event.event in ("complete", "error"):
                    break
        finally:
            stream_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
