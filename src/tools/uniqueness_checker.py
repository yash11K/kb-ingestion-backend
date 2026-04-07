"""check_uniqueness tool – queries Bedrock KB vector store for semantic similarity."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import boto3
from strands.tools import tool

from src.config import Settings

logger = logging.getLogger(__name__)

_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Set the module-level settings for use by the check_uniqueness tool."""
    global _settings
    _settings = settings


@tool
async def check_uniqueness(document_content: str, source_url: str = "") -> dict:
    """Query the Bedrock Knowledge Base for documents semantically similar to the given content.

    Retrieves the top-5 most similar documents from the KB vector store so the
    validator agent can reason about conceptual overlap and assign a uniqueness
    score.

    Args:
        document_content: The markdown body of the document to check.
        source_url: The source URL of the document (used to exclude self-matches).

    Returns:
        dict with ``similar_documents`` list and ``kb_available`` flag.
        Each similar document contains: content snippet, score, s3_uri, metadata.
    """
    if not _settings or not _settings.bedrock_kb_id:
        logger.info("Bedrock KB not configured — skipping uniqueness check")
        return {
            "kb_available": False,
            "similar_documents": [],
            "message": "Bedrock Knowledge Base not configured. Cannot assess uniqueness against existing KB content.",
        }

    # Truncate content for the retrieval query — first ~2000 chars captures
    # the core meaning without blowing up the API call.
    query_text = document_content[:2000]

    client = boto3.client(
        "bedrock-agent-runtime", region_name=_settings.aws_region
    )

    try:
        response = await asyncio.to_thread(
            client.retrieve,
            knowledgeBaseId=_settings.bedrock_kb_id,
            retrievalQuery={"text": query_text},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5}
            },
        )
    except Exception as exc:
        logger.exception("Bedrock KB Retrieve error during uniqueness check")
        return {
            "kb_available": False,
            "similar_documents": [],
            "message": f"Error querying KB: {exc}",
        }

    results = response.get("retrievalResults", [])
    similar_docs: list[dict[str, Any]] = []

    for result in results:
        content = result.get("content", {}).get("text", "")
        location = result.get("location", {})
        s3_uri = location.get("s3Location", {}).get("uri", "")
        score = float(result.get("score", 0.0))
        metadata = result.get("metadata", {})

        # Skip self-matches by source_url if available
        doc_source_url = metadata.get("source_url", "")
        if source_url and doc_source_url == source_url:
            continue

        similar_docs.append({
            "content_snippet": content[:500],
            "similarity_score": score,
            "s3_uri": s3_uri,
            "title": metadata.get("title", ""),
            "source_url": doc_source_url,
        })

    return {
        "kb_available": True,
        "similar_documents": similar_docs,
        "message": f"Found {len(similar_docs)} similar documents in the KB.",
    }
