"""Context Agent – proactive file insights powered by Haiku.

Analyses a file's metadata, content, validation state, and deep links to
surface actionable recommendations.  Streams tokens back via an async
generator so the API layer can serve SSE responses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.tools.file_context import get_file_context

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

CONTEXT_AGENT_SYSTEM_PROMPT = """\
You are a Knowledge Base content advisor embedded in a file review tool.
When the user asks you to analyse a file, call the get_file_context tool
with the file_id to load full file details and deep links.

After receiving the tool result, produce a concise, actionable analysis
using markdown with these sections:

## Quick Summary
One sentence: what this file is, its current status, and overall readiness.

## Actions Needed
Prioritised bullet list of what the reviewer should do next. Consider:
- If status is pending_review: recommend approve or reject based on
  validation_score (≥0.7 is generally good, <0.5 needs work).
- If validation_issues exist: list the top issues and how to fix them.
- If there are pending deep links: recommend reviewing/confirming them.
- If metadata looks incomplete: specify which fields need attention.

## Deep Links
If pending or confirmed deep links exist for this source, summarise them.
Group by status. Recommend which to confirm or dismiss based on URL
patterns and anchor text relevance. If none exist, say so briefly.

## Content Quality
Brief assessment of the markdown body: structure, readability, and any
obvious problems (empty sections, boilerplate, encoding issues).

Rules:
- Be concise — the panel is only 300px wide.
- Use short bullets, not paragraphs.
- Do NOT repeat raw data back verbatim; summarise and interpret.
- If asked follow-up questions, answer them using the context you already
  loaded via the tool. Do NOT call the tool again for follow-ups.
"""


class ContextAgent:
    """Wraps the Strands Agent for contextual file analysis (Haiku-based)."""

    def __init__(self, settings: "Settings") -> None:
        self._model_kwargs = dict(
            model_id=settings.haiku_model_id,
            region_name=settings.aws_region,
            max_tokens=4096,
        )
        self._tools = [get_file_context]

    async def chat(
        self,
        file_id: str,
        conversation: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Stream a context analysis or follow-up response.

        Parameters
        ----------
        file_id:
            UUID of the file to analyse.
        conversation:
            Prior conversation messages ``[{role, content}, ...]``.
            Empty list triggers the initial proactive analysis.

        Yields
        ------
        str
            Streamed text chunks from the agent.
        """
        # Build the prompt
        if not conversation:
            # Initial analysis request
            prompt = f"Analyse file {file_id}. Call get_file_context to load its details, then provide your analysis."
        else:
            # Follow-up — re-send prior conversation + new question
            prompt = conversation[-1]["content"]

        # Collect streamed chunks via the callback
        chunks: list[str] = []
        finished = False

        def _stream_callback(**kwargs: Any) -> None:
            nonlocal finished
            if "data" in kwargs:
                chunks.append(kwargs["data"])
            elif "result" in kwargs:
                finished = True

        # Build message history for follow-ups
        messages: list[dict] = []
        if conversation:
            messages = [{"role": m["role"], "content": m["content"]} for m in conversation[:-1]]

        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=CONTEXT_AGENT_SYSTEM_PROMPT,
            callback_handler=_stream_callback,
            messages=messages if messages else None,
        )

        # Run the agent in a background task and yield chunks as they arrive
        import asyncio

        task = asyncio.create_task(agent.invoke_async(prompt))

        # Yield chunks as they arrive
        while not finished:
            if chunks:
                while chunks:
                    yield chunks.pop(0)
            else:
                await asyncio.sleep(0.05)

        # Yield any remaining chunks
        while chunks:
            yield chunks.pop(0)

        # Ensure the task completed
        await task
