"""Extractor Agent definition.

Receives pre-discovered content items from the Haiku discovery agent and
converts them into structured markdown files with YAML frontmatter using
a Sonnet-class model via Strands.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

import frontmatter
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import Settings
from src.models.schemas import (
    DeepLink,
    DiscoveredContent,
    ExtractionOutput,
    ExtractionResult,
    MarkdownFile,
)
from src.tools.md_generator import _slugify, compute_content_hash

if TYPE_CHECKING:
    from src.services.stream_manager import StreamManager

logger = logging.getLogger(__name__)


def _make_callback_handler(
    job_id: UUID | None = None,
    stream_manager: "StreamManager | None" = None,
):
    """Create a callback handler that logs and optionally pushes SSE events."""

    last_log: dict[str, str | None] = {"msg": None}

    def handler(**kwargs: Any) -> None:
        if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
            tool_name = kwargs["current_tool_use"]["name"]
            log_msg = f"Agent tool call: {tool_name}"
            if log_msg != last_log["msg"]:
                logger.info("%s", log_msg)
                last_log["msg"] = log_msg
            if stream_manager and job_id:
                stream_manager.publish(job_id, "tool_call", {
                    "agent": "extractor",
                    "tool": tool_name,
                    "message": f"Extractor agent calling tool: {tool_name}",
                })
        elif "data" in kwargs:
            if stream_manager and job_id:
                stream_manager.publish(job_id, "agent_log", {
                    "agent": "extractor",
                    "chunk": kwargs["data"],
                })
        elif "result" in kwargs:
            logger.info("Agent completed response")
        elif "message" in kwargs and kwargs["message"].get("role") == "assistant":
            content = kwargs["message"].get("content", "")
            text = ""
            if isinstance(content, list):
                text = " ".join(
                    block.get("text", "") for block in content if isinstance(block, dict) and "text" in block
                )
            elif isinstance(content, str):
                text = content
            if text:
                logger.info("Extractor agent thinking: %.500s", text)

    return handler


EXTRACTOR_SYSTEM_PROMPT = """\
You are an AEM content extraction agent. You receive pre-identified content \
items extracted from an Adobe Experience Manager (AEM) model.json endpoint. \
Each item has a title, component_type, and cleaned text content.

For each content item (or group of related items), you must:

1. Convert the content into clean, well-structured Markdown. Preserve headings, \
lists, links, and tables. Strip unnecessary formatting.

   CRITICAL — VERBATIM CONTENT RULE: You MUST preserve the original text \
EXACTLY as provided, word for word. Do NOT rephrase, reword, paraphrase, \
summarize, simplify, expand, or alter the source content in ANY way. Every \
single word, phrase, and sentence from the raw content must appear in the \
output UNCHANGED. You are a formatter, not a writer — your only job is to \
apply Markdown structure (headings, lists, bold, links, tables) to the \
original text. If even one word is changed, added, or removed from the \
source content, the output is WRONG.

   CRITICAL — LINK PRESERVATION RULE: You MUST preserve ALL hyperlinks found \
in the content as proper Markdown links: [anchor text](url). This includes \
"Learn More", "Click Here", "Read More", "Go to", "Visit", "See details", \
and any other call-to-action or inline links. If the original content has a \
link with text "Learn more" pointing to "/en/some-page", the output MUST \
contain [Learn more](/en/some-page). Never strip links or convert them to \
plain text. Links are critical content that must be preserved.

2. Refine the `title` if needed — make it descriptive and accurate. The title \
is the ONLY field where you may use your own wording.
3. Infer the `content_type` (e.g. "FAQ", "Product Guide", "Support Article", \
"Navigation", "General Content") based on the content structure and semantics.
4. Decide how to group or split items into logical files. Related items that \
form a single coherent document should be grouped together. Items covering \
distinct topics should remain separate.

Return a JSON array of objects with exactly these fields:
- "title": (string) Descriptive title.
- "content_type": (string) Inferred content type category.
- "markdown_body": (string) The Markdown content. Must not be empty. All links \
must be preserved as [text](url) format.
- "source_nodes": (array of strings) The path values of all content items \
that contributed to this file.
- "component_type": (string) The AEM component type of the primary source item.
- "source_url": (string) The source AEM URL provided in the prompt.
- "parent_context": (string) Empty string unless you can infer a parent relationship.
- "grouping_rationale": (string) Brief explanation of why these items were \
grouped together or kept separate.
- "embedded_links": (array of objects) ALL internal links found in the content. \
Each object: {"url": "/path/to/page", "anchor_text": "Link text"}. Include \
every "Learn More", "Click Here", "Read More", CTA link, and any other \
internal link. Omit external links, anchors (#), mailto:, and tel: links.

Important:
- Process ALL content items provided in the prompt.
- The source_url, region, brand, and namespace values are provided in the user prompt.
- Return ONLY a valid JSON array of the result objects, with no additional text.
- Do NOT call any tools. Perform all reasoning and conversion yourself.
- NEVER change, rephrase, or paraphrase the original content text. The \
markdown_body must contain the EXACT original words from the source. You are \
converting format (HTML/text → Markdown), NOT rewriting content.
"""


class ExtractorAgent:
    """Wraps the Strands Agent for content extraction."""

    def __init__(self, settings: Settings) -> None:
        self._model_kwargs = dict(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            max_tokens=settings.bedrock_max_tokens,
        )
        self.settings = settings

    def _build_prompt(
        self,
        items: list[DiscoveredContent],
        url: str,
        region: str,
        brand: str,
        namespace: str,
    ) -> str:
        """Serialize DiscoveredContent items to JSON and build the user prompt."""
        items_json = json.dumps(
            [item.model_dump() for item in items],
            default=str,
        )
        return (
            f"Process the following pre-identified content items from an AEM endpoint.\n"
            f"URL: {url}\n"
            f"Region: {region}\n"
            f"Brand: {brand}\n"
            f"Namespace: {namespace}\n\n"
            f"Content Items ({len(items)} items):\n"
            f"{items_json}\n\n"
            f"Convert each item's content to clean Markdown, infer metadata, "
            f"decide on grouping, and return a JSON array of result objects "
            f"as described in your instructions."
        )

    @staticmethod
    def _parse_response(response_text: str) -> list[ExtractionResult]:
        """Extract JSON array from response text, validate each element."""
        start = response_text.find("[")
        end = response_text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []

        json_str = response_text[start : end + 1]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON from response: %s", response_text[:500])
            return []

        if not isinstance(data, list):
            return []

        results: list[ExtractionResult] = []
        for i, element in enumerate(data):
            try:
                results.append(ExtractionResult(**element))
            except Exception as exc:
                logger.warning("Skipping invalid element at index %d: %s", i, exc)
        return results

    async def _invoke_agent(
        self,
        prompt: str,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> list[ExtractionResult]:
        """Invoke Bedrock agent, parse JSON response."""
        callback = _make_callback_handler(job_id, stream_manager)
        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=[],
            system_prompt=EXTRACTOR_SYSTEM_PROMPT,
            callback_handler=callback,
        )
        result = await agent.invoke_async(prompt)
        return self._parse_response(str(result))

    async def extract(
        self,
        content_items: list[DiscoveredContent],
        url: str,
        region: str,
        brand: str,
        namespace: str = "",
        parent_url: str | None = None,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> ExtractionOutput:
        """Convert discovered content items into markdown files.

        Args:
            content_items: Pre-identified content from the discovery agent.
            url: The AEM model.json endpoint URL.
            region: Geographic region.
            brand: Brand identifier.
            namespace: URL namespace segment.
            parent_url: Parent page URL for context.
            job_id: Optional job ID for SSE streaming.
            stream_manager: Optional stream manager for SSE events.

        Returns:
            ExtractionOutput containing MarkdownFile objects.
        """
        if not content_items:
            logger.info("No content items to extract for %s", url)
            return ExtractionOutput(files=[], child_urls=[])

        logger.info(
            "Extracting %d content items from %s",
            len(content_items), url,
        )

        batch_threshold = self.settings.batch_threshold
        all_results: list[ExtractionResult] = []

        if len(content_items) > batch_threshold:
            num_batches = math.ceil(len(content_items) / batch_threshold)
            batches = [
                content_items[i * batch_threshold : (i + 1) * batch_threshold]
                for i in range(num_batches)
            ]
            logger.info(
                "Splitting %d items into %d batches (threshold=%d)",
                len(content_items), num_batches, batch_threshold,
            )

            if stream_manager and job_id:
                stream_manager.publish(job_id, "extraction_batching", {
                    "total_batches": num_batches,
                    "total_nodes": len(content_items),
                })

            for batch_index, batch in enumerate(batches):
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "extraction_batch_start", {
                        "batch_index": batch_index + 1,
                        "total_batches": num_batches,
                        "node_count": len(batch),
                    })
                try:
                    prompt = self._build_prompt(batch, url, region, brand, namespace)
                    logger.info(
                        "Invoking agent for batch %d/%d (%d items)",
                        batch_index + 1, num_batches, len(batch),
                    )
                    batch_results = await self._invoke_agent(prompt, job_id, stream_manager)
                    all_results.extend(batch_results)
                except Exception as exc:
                    logger.error(
                        "Batch %d/%d failed, skipping",
                        batch_index + 1, num_batches, exc_info=True,
                    )
        else:
            if stream_manager and job_id:
                stream_manager.publish(job_id, "extraction_batching", {
                    "total_batches": 1,
                    "total_nodes": len(content_items),
                })
            prompt = self._build_prompt(content_items, url, region, brand, namespace)
            logger.info(
                "Invoking Strands agent for %d content items from %s",
                len(content_items), url,
            )
            try:
                all_results = await self._invoke_agent(prompt, job_id, stream_manager)
            except Exception:
                logger.error(
                    "Extraction failed (%d items), skipping",
                    len(content_items), exc_info=True,
                )

        if stream_manager and job_id:
            stream_manager.publish(job_id, "extraction_complete", {
                "total_results": len(all_results),
            })

        logger.info(
            "Extraction complete, %d results from %d items",
            len(all_results), len(content_items),
        )
        files = PostProcessor.process(
            all_results, url, region, brand, namespace, parent_url,
        )

        # Collect embedded links from extraction results and convert to DeepLinks
        embedded_links = PostProcessor.collect_embedded_links(all_results, url)

        return ExtractionOutput(files=files, child_urls=[], embedded_links=embedded_links)


class PostProcessor:
    """Convert ExtractionResults into MarkdownFiles."""

    @staticmethod
    def process(
        results: list[ExtractionResult],
        url: str,
        region: str,
        brand: str,
        namespace: str = "",
        parent_url: str | None = None,
    ) -> list[MarkdownFile]:
        """Convert ExtractionResults into MarkdownFiles."""
        files: list[MarkdownFile] = []
        now = datetime.now(timezone.utc)

        for result in results:
            content_hash = compute_content_hash(result.markdown_body)
            filename = _slugify(result.title) + ".md"

            if result.source_nodes:
                first_node = result.source_nodes[0]
                key = first_node.rsplit("/", 1)[-1] if "/" in first_node else first_node
            else:
                key = ""

            parent_context = parent_url if parent_url else ""

            fm_metadata = {
                "key": key,
                "namespace": namespace,
                "brand": brand,
                "region": region,
                "source_url": url,
                "parent_context": parent_context,
                "title": result.title,
                "content_type": result.content_type,
                "component_type": result.component_type,
            }

            post = frontmatter.Post(result.markdown_body, **fm_metadata)
            md_content = frontmatter.dumps(post)

            files.append(MarkdownFile(
                filename=filename,
                title=result.title,
                content_type=result.content_type,
                source_url=url,
                component_type=result.component_type,
                key=key,
                namespace=namespace,
                md_content=md_content,
                md_body=result.markdown_body,
                content_hash=content_hash,
                extracted_at=now,
                parent_context=parent_context,
                region=region,
                brand=brand,
            ))

        return files

    @staticmethod
    def collect_embedded_links(
        results: list[ExtractionResult],
        source_url: str,
    ) -> list[DeepLink]:
        """Collect embedded links from extraction results into DeepLink objects."""
        parsed = urlparse(source_url)
        base_host = f"{parsed.scheme}://{parsed.netloc}"
        seen: set[str] = set()
        links: list[DeepLink] = []

        for result in results:
            for link in result.embedded_links:
                url = link.url.strip()
                if not url:
                    continue

                # Normalize to path
                if url.startswith("http"):
                    link_parsed = urlparse(url)
                    if link_parsed.netloc and link_parsed.netloc != parsed.netloc:
                        continue  # external
                    url = link_parsed.path

                if not url.startswith("/"):
                    continue

                clean_path = url.rstrip("/")
                if clean_path in seen:
                    continue
                seen.add(clean_path)

                links.append(DeepLink(
                    url=clean_path,
                    model_json_url=f"{base_host}{clean_path}.model.json",
                    anchor_text=link.anchor_text,
                    found_in_node=", ".join(result.source_nodes[:3]),
                    found_in_page=source_url,
                ))

        return links
