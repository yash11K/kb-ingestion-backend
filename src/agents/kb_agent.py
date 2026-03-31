"""KB Agent — conversational Strands agent for system introspection.

Provides natural-language access to system stats, file search, source/job
inspection, deep link browsing, and ad-hoc read-only SQL queries.
Streams responses as an async generator for SSE consumption.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.tools.kb_agent_tools import (
    execute_sql_query,
    get_job_details,
    get_source_stats_tool,
    get_system_stats,
    list_deep_links_tool,
    list_recent_jobs,
    list_sources_tool,
    search_files,
)

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

KB_AGENT_SYSTEM_PROMPT = """\
You are the KB System Agent — an operations assistant for the AEM Knowledge \
Base Ingestion System. You help users understand the current state of the \
system by querying the database and presenting results clearly.

You have access to the following tools:

1. **get_system_stats** — Quick overview: total files, pending review, \
approved, rejected, average validation score.

2. **search_files** — Search KB files by free-text query and/or filters \
(status, region, brand, content_type, doc_type). Returns summary fields.

3. **list_sources_tool** — Browse content sources with pagination.

4. **get_source_stats_tool** — Aggregate stats for a specific source \
(job counts, file counts by status).

5. **list_recent_jobs** — List ingestion jobs, optionally filtered by \
status (in_progress, completed, failed).

6. **get_job_details** — Full details of a specific ingestion job.

7. **list_deep_links_tool** — Browse deep links for a source by status \
(pending, confirmed, dismissed, ingested).

8. **execute_sql_query** — Run arbitrary read-only SELECT queries against \
the PostgreSQL database. Use this for ad-hoc questions that the other \
tools don't cover.

## Database Schema Reference

**sources**: id, url, region, brand, nav_root_url, nav_label, nav_section, \
page_path, last_ingested_at, created_at, updated_at

**ingestion_jobs**: id, source_url, source_id, status (in_progress/completed/\
failed), total_nodes_found, files_created, files_skipped_duplicate, \
auto_approved, auto_rejected, pending_review, not_found, error_message, \
started_at, completed_at, max_depth, pages_crawled, current_depth

**kb_files**: id, filename, title, content_type, content_hash, source_url, \
component_type, aem_node_id, md_content, doc_type, modify_date, \
parent_context, region, brand, key, namespace, validation_score, \
validation_breakdown (JSONB), validation_issues (JSONB), status \
(pending_review/approved/auto_rejected/in_s3/rejected), s3_bucket, s3_key, \
s3_uploaded_at, reviewed_by, reviewed_at, review_notes, source_id, job_id, \
search_vector, created_at, updated_at

**deep_links**: id, source_id, url, anchor_text, found_in_page, status \
(pending/confirmed/dismissed/ingested), created_at, updated_at

**revalidation_jobs**: id, status, total_files, processed, improved, \
degraded, unchanged, errors, started_at, completed_at

**nav_tree_cache**: id, root_url, tree_data (JSONB), fetched_at

## Guidelines

- Prefer the specialised tools over raw SQL when they cover the question.
- Use execute_sql_query for complex aggregations, joins, or questions the \
other tools can't answer.
- Present numbers and tables in clean markdown.
- When showing file lists, include id, title, status, and validation_score.
- Be concise. Users want answers, not essays.
- If a query returns no results, say so clearly.
- For large result sets, summarise and offer to drill down.

## CRITICAL — User-Facing Language Rules

You are a friendly assistant that helps users understand their knowledge base. \
NEVER expose implementation details in your responses. The user does not know \
or care about SQL, databases, PostgreSQL, queries, or tables.

NEVER say:
- "Let me run a SQL query…"
- "I'll query the database…"
- "Searching the database…"
- "Let me check the DB…"
- "Running a SELECT…"
- "The query returned…"
- Any mention of SQL, database, tables, rows, columns, or PostgreSQL.

INSTEAD say things like:
- "Let me check the system…"
- "Looking into the current status…"
- "Pulling up the latest information…"
- "Let me look that up for you…"
- "Checking the knowledge base…"
- "Here's what I found…"

Think of yourself as a knowledgeable colleague who just *knows* the system \
state — you don't explain the plumbing behind how you got the answer. \
Be natural, conversational, and focus on the insights, not the mechanism.
"""


class KBAgent:
    """Conversational Strands agent for KB system introspection."""

    def __init__(self, settings: "Settings") -> None:
        self._model_kwargs = dict(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            max_tokens=settings.bedrock_max_tokens,
        )
        self._tools = [
            execute_sql_query,
            get_system_stats,
            list_sources_tool,
            get_source_stats_tool,
            list_recent_jobs,
            get_job_details,
            search_files,
            list_deep_links_tool,
        ]

    async def chat(
        self,
        message: str,
        conversation: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a response to the user's message.

        Parameters
        ----------
        message:
            The latest user message.
        conversation:
            Prior conversation messages ``[{role, content}, ...]``.
            Pass the full history for multi-turn context.

        Yields
        ------
        str
            Streamed text chunks from the agent.
        """
        chunks: list[str] = []
        finished = False

        def _callback(**kwargs: Any) -> None:
            nonlocal finished
            if "data" in kwargs:
                chunks.append(kwargs["data"])
            elif "result" in kwargs:
                finished = True

        # Build message history for multi-turn.
        # Strands expects `messages` to be the *prior* context and the prompt
        # to be the new user turn.  The frontend sends the full conversation
        # history in `conversation` (which does NOT include the current
        # `message`).  We pass conversation as-is for history context.
        # However, Strands Agent needs each message's content wrapped in the
        # Bedrock content-block format: [{"text": "..."}].
        messages: list[dict] | None = None
        if conversation:
            messages = []
            for m in conversation:
                content = m["content"]
                # Strands/Bedrock expects content as a list of blocks
                if isinstance(content, str):
                    content = [{"text": content}]
                messages.append({"role": m["role"], "content": content})

        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=KB_AGENT_SYSTEM_PROMPT,
            callback_handler=_callback,
            messages=messages,
        )

        task = asyncio.create_task(agent.invoke_async(message))

        try:
            while not finished:
                if chunks:
                    while chunks:
                        yield chunks.pop(0)
                else:
                    # Also check if the task errored out
                    if task.done():
                        exc = task.exception()
                        if exc:
                            logger.error("KB Agent task failed: %s", exc)
                            yield f"\n\n⚠️ Something went wrong while processing your request."
                        break
                    await asyncio.sleep(0.05)

            # Drain remaining chunks
            while chunks:
                yield chunks.pop(0)

            await task
        except Exception as exc:
            logger.exception("KB Agent streaming error")
            yield f"\n\n⚠️ An error occurred: {exc}"
