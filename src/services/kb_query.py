"""Knowledge base query service — retrieval and RAG generation.

Provides two modes:
1. Local PostgreSQL full-text search (fallback when no Bedrock KB configured)
2. AWS Bedrock Knowledge Base via Retrieve / RetrieveAndGenerate APIs

Streaming flows:
- search(): ranked KB chunks as SSE events
- chat(): retrieval-augmented generation streaming tokens as SSE
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import boto3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL (local fallback)
# ---------------------------------------------------------------------------

_SEARCH_SQL = """
SELECT id, title, filename, content_type, component_type, doc_type,
       source_url, region, brand, md_content,
       ts_rank_cd(search_vector, query) AS rank
FROM kb_files, plainto_tsquery('english', :query) query
WHERE search_vector @@ query
  AND status IN ('approved', 'in_s3')
ORDER BY rank DESC
LIMIT :limit
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
    """Query service supporting both local Postgres and Bedrock KB modes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._use_bedrock_kb = bool(settings.bedrock_kb_id)

    def _resolve_model_arn(self) -> str:
        """Build the correct ARN for RetrieveAndGenerate.

        Uses haiku_model_id for RAG queries (cheaper/faster).
        Inference profile IDs (prefixed with ``us.`` or ``global.``) need
        the ``inference-profile/`` ARN format with an account ID, while
        plain foundation model IDs use ``foundation-model/``.
        """
        model_id = self._settings.haiku_model_id
        region = self._settings.aws_region

        if model_id.startswith(("us.", "eu.", "ap.", "global.")):
            sts = boto3.client("sts", region_name=region)
            account_id = sts.get_caller_identity()["Account"]
            return f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{model_id}"

        return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"

    # =====================================================================
    # search
    # =====================================================================

    async def search(self, query: str, limit: int = 10) -> AsyncIterator[str]:
        """Stream search results as SSE events."""
        if self._use_bedrock_kb:
            async for chunk in self._bedrock_kb_search(query, limit):
                yield chunk
        else:
            async for chunk in self._local_search(query, limit):
                yield chunk

    # =====================================================================
    # chat
    # =====================================================================

    async def chat(self, query: str, limit: int = 5) -> AsyncIterator[str]:
        """RAG endpoint — retrieve context then stream generation as SSE."""
        if self._use_bedrock_kb:
            async for chunk in self._bedrock_kb_chat(query, limit):
                yield chunk
        else:
            async for chunk in self._local_chat(query, limit):
                yield chunk

    # =====================================================================
    # Bedrock Knowledge Base — Retrieve
    # =====================================================================

    async def _bedrock_kb_search(self, query: str, limit: int) -> AsyncIterator[str]:
        """Use Bedrock KB Retrieve API for semantic search."""
        client = boto3.client(
            "bedrock-agent-runtime", region_name=self._settings.aws_region
        )

        try:
            response = await asyncio.to_thread(
                client.retrieve,
                knowledgeBaseId=self._settings.bedrock_kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": limit}
                },
            )
        except Exception as exc:
            logger.exception("Bedrock KB Retrieve error")
            yield _sse("error", {"message": str(exc)})
            return

        results = response.get("retrievalResults", [])
        yield _sse("search_start", {"query": query, "total": len(results)})

        for result in results:
            content = result.get("content", {}).get("text", "")
            location = result.get("location", {})
            s3_uri = location.get("s3Location", {}).get("uri", "")
            score = result.get("score", 0.0)
            metadata = result.get("metadata", {})

            yield _sse("result", {
                "content": content,
                "s3_uri": s3_uri,
                "score": float(score),
                "metadata": metadata,
            })

        yield _sse("search_end", {"query": query, "total": len(results)})

    # =====================================================================
    # Bedrock Knowledge Base — RetrieveAndGenerate
    # =====================================================================

    async def _bedrock_kb_chat(self, query: str, limit: int) -> AsyncIterator[str]:
        """Stream RAG response from Bedrock KB using retrieve_and_generate_stream."""
        client = boto3.client(
            "bedrock-agent-runtime", region_name=self._settings.aws_region
        )

        try:
            response = await asyncio.to_thread(
                client.retrieve_and_generate_stream,
                input={"text": query},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": self._settings.bedrock_kb_id,
                        "modelArn": self._resolve_model_arn(),
                        "retrievalConfiguration": {
                            "vectorSearchConfiguration": {
                                "numberOfResults": limit,
                            }
                        },
                    },
                },
            )
        except Exception as exc:
            logger.exception("Bedrock KB RetrieveAndGenerateStream error")
            yield _sse("error", {"message": str(exc)})
            return

        stream = response.get("stream")
        if stream is None:
            yield _sse("error", {"message": "No stream returned from Bedrock KB"})
            return

        sources_emitted = False
        try:
            for event in stream:
                # Citation events carry the retrieved source references
                if "citation" in event:
                    if not sources_emitted:
                        sources = []
                    citation = event["citation"]
                    for ref in citation.get("retrievedReferences", []):
                        loc = ref.get("location", {})
                        s3_uri = loc.get("s3Location", {}).get("uri", "")
                        sources.append({
                            "s3_uri": s3_uri,
                            "content": ref.get("content", {}).get("text", "")[:200],
                        })
                    if not sources_emitted:
                        yield _sse("sources", {"query": query, "sources": sources})
                        sources_emitted = True

                # Output events carry streamed text chunks
                if "output" in event:
                    text = event["output"].get("text", "")
                    if text:
                        yield _sse("token", {"text": text})

        except Exception as exc:
            logger.exception("Error reading Bedrock KB stream")
            yield _sse("error", {"message": str(exc)})
            return

        # Emit sources even if no citation events came through
        if not sources_emitted:
            yield _sse("sources", {"query": query, "sources": []})

        yield _sse("done", {"query": query})


    # =====================================================================
    # Local Postgres fallback — search
    # =====================================================================

    async def _local_search(self, query: str, limit: int) -> AsyncIterator[str]:
        """Stream search results from local Postgres full-text search."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(_SEARCH_SQL), {"query": query, "limit": limit}
            )
            rows = result.mappings().all()

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

    # =====================================================================
    # Local Postgres fallback — chat
    # =====================================================================

    async def _local_chat(self, query: str, limit: int) -> AsyncIterator[str]:
        """Retrieve from Postgres then stream Bedrock converse_stream as SSE."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(_SEARCH_SQL), {"query": query, "limit": limit}
            )
            rows = result.mappings().all()

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

        async for chunk in self._stream_bedrock(query, context_block):
            yield chunk

        yield _sse("done", {"query": query})

    async def _stream_bedrock(self, query: str, context: str) -> AsyncIterator[str]:
        """Call Bedrock converse_stream and yield SSE token events."""
        system_prompt = (
            "You are a helpful assistant for a knowledge base. "
            "Answer the user's question using ONLY the provided context. "
            "If the context doesn't contain enough information, say so. "
            "Cite the source URLs when referencing specific information."
        )
        user_message = (
            f"Context from knowledge base:\n\n{context}\n\n---\n\n"
            f"User question: {query}"
        )

        bedrock = boto3.client(
            "bedrock-runtime", region_name=self._settings.aws_region
        )

        try:
            response = await asyncio.to_thread(
                bedrock.converse_stream,
                modelId=self._settings.bedrock_model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={
                    "maxTokens": self._settings.bedrock_max_tokens,
                    "temperature": 0.3,
                },
            )

            stream = response.get("stream")
            if stream is None:
                yield _sse("error", {"message": "No stream returned from Bedrock"})
                return

            for event in stream:
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        yield _sse("token", {"text": text})
                elif "messageStop" in event:
                    break

        except Exception as exc:
            logger.exception("Bedrock streaming error")
            yield _sse("error", {"message": str(exc)})
