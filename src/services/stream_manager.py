"""SSE stream manager for broadcasting pipeline events to connected clients.

Manages per-job event queues with support for multiple subscribers and
late-joiner replay via a rolling event buffer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SSEEvent(BaseModel):
    """A single server-sent event."""

    event: str  # agent_log, tool_call, progress, complete, error
    data: dict
    timestamp: datetime


class _JobStream:
    """Internal state for a single job's event stream."""

    def __init__(self, buffer_size: int = 200) -> None:
        self.subscribers: list[asyncio.Queue[SSEEvent]] = []
        self.buffer: list[SSEEvent] = []
        self.buffer_size = buffer_size
        self.finished = False

    def add_subscriber(self) -> asyncio.Queue[SSEEvent]:
        q: asyncio.Queue[SSEEvent] = asyncio.Queue()
        # Replay buffered events for late joiners
        for evt in self.buffer:
            q.put_nowait(evt)
        self.subscribers.append(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue[SSEEvent]) -> None:
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    def publish(self, event: SSEEvent) -> None:
        # Buffer for late joiners
        self.buffer.append(event)
        if len(self.buffer) > self.buffer_size:
            self.buffer = self.buffer[-self.buffer_size :]
        # Fan out to all subscribers
        for q in self.subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full, dropping event")


class StreamManager:
    """Manages SSE streams for all active pipeline jobs."""

    def __init__(self) -> None:
        self._streams: dict[UUID, _JobStream] = {}

    def register(self, job_id: UUID) -> None:
        """Register a new job stream. Called when a pipeline starts."""
        self._streams[job_id] = _JobStream()
        logger.info("Registered SSE stream for job_id=%s", job_id)

    def subscribe(self, job_id: UUID) -> asyncio.Queue[SSEEvent] | None:
        """Subscribe to a job's event stream. Returns None if job not found."""
        stream = self._streams.get(job_id)
        if stream is None:
            return None
        return stream.add_subscriber()

    def unsubscribe(self, job_id: UUID, q: asyncio.Queue[SSEEvent]) -> None:
        """Remove a subscriber from a job's stream."""
        stream = self._streams.get(job_id)
        if stream is not None:
            stream.remove_subscriber(q)

    def publish(self, job_id: UUID, event: str, data: dict) -> None:
        """Publish an event to all subscribers of a job."""
        stream = self._streams.get(job_id)
        if stream is None:
            return
        sse_event = SSEEvent(
            event=event,
            data=data,
            timestamp=datetime.now(timezone.utc),
        )
        stream.publish(sse_event)

    def finish(self, job_id: UUID) -> None:
        """Mark a job stream as finished. Cleanup after a grace period."""
        stream = self._streams.get(job_id)
        if stream is not None:
            stream.finished = True

    def cleanup(self, job_id: UUID) -> None:
        """Remove a job stream entirely."""
        self._streams.pop(job_id, None)

    def is_active(self, job_id: UUID) -> bool:
        return job_id in self._streams
