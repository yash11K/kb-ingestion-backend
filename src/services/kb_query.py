"""Knowledge base query service — retrieval and RAG generation.

Provides two streaming flows:
1. search(): full-text search returning ranked KB chunks as SSE events
2. chat(): retrieval-augmented generation streaming Bedrock tokens as SSE
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import asyncpg
import boto3

from src.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_SEARCH_SQL = """
SELECT id, title, filename, content_type, component_type, doc_type,
       source_url, region, brand, md_content,
       ts_rank_cd(search_vector, query) AS rank
FROM kb_files, plainto_tsquery('english', $1) query
WHERE search_vector @@ query
  AND status IN ('approved', 'in_s3')
ORDER BY rank DESC
LIMIT $2
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class KBQueryService:
    """Stateless service — receives dependencies per call."""

    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings

    # ----- search (retrieval only) -----

    async def search(self, query: str, limit: int = 10) -> AsyncIterator[str]:
        """Stream search results as SSE events."""
        rows = await self._pool.fetch(_SEARCH_SQL, query, limit)

        yield _sse("search_start", {"query": query, "total": len(rows)})

        for row in rows:
            yield _sse("result", {
                "id": str(row["id"]),
                "title": row["title"],
                "filename": row["filename"],
                "content_type": row["content_type"],
                "component_type": row["component_type"],
                "doc_type": row["doc_type"],
                "source_url": row["source_url"],
                "region": row["region"],
                "brand": row["brand"],
                "md_content": row["md_content"],
                "rank": float(row["rank"]),
            })

        yield _sse("search_end", {"query": query, "total": len(rows)})

    # ----- chat (RAG) -----

    async def chat(self, query: str, limit: int = 5) -> AsyncIterator[str]:
        """Retrieve context then stream Bedrock generation as SSE tokens."""
        # 1. Retrieve relevant docs
        rows = await self._pool.fetch(_SEARCH_SQL, query, limit)

        sources = []
        context_parts: list[str] = []
        for row in rows:
            sources.append({
                "id": str(row["id"]),
                "title": row["title"],
                "source_url": row["source_url"],
            })
            context_parts.append(
                f"### {row['title']}\nSource: {row['source_url']}\n\n{row['md_content']}"
            )

        yield _sse("sources", {"query": query, "sources": sources})

        if not context_parts:
            yield _sse("token", {"text": "I couldn't find any relevant information in the knowledge base for your query."})
            yield _sse("done", {"query": query})
            return

        context_block = "\n\n---\n\n".join(context_parts)

        # 2. Stream from Bedrock via converse_stream
        async for chunk in self._stream_bedrock(query, context_block):
            yield chunk

        yield _sse("done", {"query": query})
