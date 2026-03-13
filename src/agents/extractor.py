"""Extractor Agent definition.

Wraps a Strands Agent with BedrockModel and extraction tools to fetch AEM JSON,
filter content nodes, convert HTML to markdown, and generate markdown files.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import frontmatter
import httpx
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import Settings
from src.models.schemas import ContentNode, ExtractionOutput, ExtractionResult, MarkdownFile
from src.tools.fetch_aem import ToolError
from src.tools.filter_components import extract_child_urls, filter_by_component_type_direct
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
            # Streaming text chunk
            if stream_manager and job_id:
                stream_manager.publish(job_id, "agent_log", {
                    "agent": "extractor",
                    "chunk": kwargs["data"],
                })
        elif "result" in kwargs:
            logger.info("Agent completed response")
            if stream_manager and job_id:
                stream_manager.publish(job_id, "agent_log", {
                    "agent": "extractor",
                    "message": "Extractor agent completed response",
                })
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
You are an AEM content extraction agent. You receive pre-filtered content nodes \
extracted from an Adobe Experience Manager (AEM) model.json endpoint as raw JSON. \
Your job is to perform all content understanding and return structured JSON output.

For each content node (or group of related nodes), you must:

1. Convert the HTML content to clean, well-structured Markdown. Strip \
unnecessary tags, preserve headings, lists, links, and tables.
2. Infer a descriptive `title` from the content itself.
3. Infer the `content_type` (e.g. "FAQ", "Product Guide", "Support Article", \
"Navigation", "General Content") based on the content structure and semantics.
4. Decide how to group or split nodes into logical files. Related nodes that \
form a single coherent document should be grouped together. Nodes covering \
distinct topics should remain separate.

Return a JSON array of objects with exactly these fields:
- "title": (string) Descriptive title inferred from the content.
- "content_type": (string) Inferred content type category.
- "markdown_body": (string) The converted Markdown content. Must not be empty.
- "source_nodes": (array of strings) The aem_node_id values of all content \
nodes that contributed to this file.
- "component_type": (string) The AEM component type of the primary source node.
- "source_url": (string) The source AEM URL provided in the prompt.
- "parent_context": (string) The parent node path context from the source nodes.
- "grouping_rationale": (string) Brief explanation of why these nodes were \
grouped together or kept separate.

Important:
- Process ALL content nodes provided in the prompt.
- The source_url, region, and brand values are provided in the user prompt.
- Return ONLY a valid JSON array of the result objects, with no additional text.
- Do NOT call any tools. Perform all reasoning and conversion yourself.
"""


class ExtractorAgent:
    """Wraps the Strands Agent with extraction tools."""

    def __init__(self, settings: Settings) -> None:
        self._model_kwargs = dict(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            max_tokens=settings.bedrock_max_tokens,
        )
        self._tools = []
        self.settings = settings

    def _build_prompt(
        self,
        nodes: list[ContentNode],
        url: str,
        region: str,
        brand: str,
    ) -> str:
        """Serialize ContentNodes to JSON and build the user prompt.

        The prompt contains the raw ContentNode JSON data along with
        contextual information (URL, region, brand) so the LLM can
        perform content understanding directly.
        """
        nodes_json = json.dumps(
            [node.model_dump() for node in nodes],
            default=str,
        )
        return (
            f"Process the following pre-filtered content nodes extracted from "
            f"an AEM endpoint.\n"
            f"URL: {url}\n"
            f"Region: {region}\n"
            f"Brand: {brand}\n\n"
            f"Content Nodes ({len(nodes)} nodes):\n"
            f"{nodes_json}\n\n"
            f"Convert each node's HTML to clean Markdown, infer metadata, "
            f"decide on grouping, and return a JSON array of result objects "
            f"as described in your instructions."
        )

    @staticmethod
    def _parse_response(response_text: str) -> list[ExtractionResult]:
        """Extract JSON array from response text, validate each element
        against ExtractionResult Pydantic model. Handles preamble/postamble
        text around the JSON array. Returns empty list on parse failure."""
        start = response_text.find("[")
        end = response_text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []

        json_str = response_text[start : end + 1]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON from response: %s", response_text)
            return []

        if not isinstance(data, list):
            logger.error("Parsed JSON is not a list: %s", response_text)
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
        """Invoke Bedrock agent with zero tools, parse JSON response.

        Creates a Strands Agent with BedrockModel and the system prompt,
        invokes it with the given prompt, and parses the response into
        a list of ExtractionResult objects.
        """
        callback = _make_callback_handler(job_id, stream_manager)
        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=EXTRACTOR_SYSTEM_PROMPT,
            callback_handler=callback,
        )
        result = await agent.invoke_async(prompt)
        response_text = str(result)
        return self._parse_response(response_text)


    async def extract(
        self,
        url: str,
        region: str,
        brand: str,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> ExtractionOutput:
        """Fetch AEM JSON and extract content nodes into markdown files.

        Fetches the AEM JSON directly via HTTP and pre-filters content nodes
        using ``filter_by_component_type_direct`` before passing them to the
        Strands agent.  This avoids sending the full (potentially huge) JSON
        through the agent's LLM context window, preventing
        ``MaxTokensReachedException`` for large payloads.

        Args:
            url: The AEM model.json endpoint URL.
            region: Geographic region for the content (e.g. US, EU, APAC).
            brand: Brand identifier for the content.

        Returns:
            ExtractionOutput containing MarkdownFile objects and a list of
            internal child-page URLs discovered from link fields (e.g.
            ``ctaLink``) in the filtered content nodes.  Child URLs are full
            ``*.model.json`` URLs ready for a follow-up ingestion pass.
        """
        # --- Step 1: Fetch AEM JSON directly (not through the agent) ---
        try:
            response = httpx.get(url, timeout=self.settings.aem_request_timeout)
        except httpx.TimeoutException:
            raise ToolError(f"Request to {url} timed out after {self.settings.aem_request_timeout} seconds")
        except httpx.RequestError as exc:
            raise ToolError(f"Request to {url} failed: {exc}")

        if response.status_code != 200:
            raise ToolError(
                f"AEM endpoint returned HTTP {response.status_code}: {response.text}"
            )

        try:
            aem_json = response.json()
        except ValueError as exc:
            raise ToolError(f"Invalid JSON response from {url}: {exc}")

        # Log raw payload size and estimated token count
        payload_bytes = len(json.dumps(aem_json))
        estimated_tokens = payload_bytes // 4
        logger.info(
            "Raw AEM JSON payload: %d bytes, estimated %d tokens",
            payload_bytes,
            estimated_tokens,
        )
        if payload_bytes > self.settings.max_payload_bytes:
            logger.warning(
                "Raw JSON payload (%d bytes) exceeds max_payload_bytes threshold (%d bytes)",
                payload_bytes,
                self.settings.max_payload_bytes,
            )

        # --- Step 2: Pre-filter content nodes directly in Python ---
        content_nodes = filter_by_component_type_direct(
            aem_json,
            self.settings.allowlist,
            self.settings.denylist,
        )

        # Discover internal child page URLs from link fields (e.g. ctaLink)
        child_urls = extract_child_urls(content_nodes, url)
        if child_urls:
            logger.info(
                "Discovered %d child page URLs from content nodes at %s",
                len(child_urls),
                url,
            )

        # Log filtered results
        filtered_payload_bytes = len(json.dumps([n.model_dump() for n in content_nodes], default=str))
        filtered_estimated_tokens = filtered_payload_bytes // 4
        logger.info(
            "After filtering: %d content nodes, filtered payload %d bytes, estimated %d tokens",
            len(content_nodes),
            filtered_payload_bytes,
            filtered_estimated_tokens,
        )

        # --- Step 3: Batch and invoke agent ---
        batch_threshold = self.settings.batch_threshold
        all_results: list[ExtractionResult] = []

        if len(content_nodes) > batch_threshold:
            # Split into batches of batch_threshold size
            num_batches = math.ceil(len(content_nodes) / batch_threshold)
            batches = [
                content_nodes[i * batch_threshold : (i + 1) * batch_threshold]
                for i in range(num_batches)
            ]
            logger.info(
                "Splitting %d nodes into %d batches (threshold=%d)",
                len(content_nodes),
                num_batches,
                batch_threshold,
            )

            if stream_manager and job_id:
                stream_manager.publish(job_id, "extraction_batching", {
                    "total_batches": num_batches,
                    "total_nodes": len(content_nodes),
                })

            for batch_index, batch in enumerate(batches):
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "extraction_batch_start", {
                        "batch_index": batch_index + 1,
                        "total_batches": num_batches,
                        "node_count": len(batch),
                    })
                try:
                    prompt = self._build_prompt(batch, url, region, brand)
                    logger.info(
                        "Invoking agent for batch %d/%d (%d nodes)",
                        batch_index + 1,
                        num_batches,
                        len(batch),
                    )
                    batch_results = await self._invoke_agent(prompt, job_id, stream_manager)
                    all_results.extend(batch_results)
                    if stream_manager and job_id:
                        stream_manager.publish(job_id, "extraction_batch_complete", {
                            "batch_index": batch_index + 1,
                            "total_batches": num_batches,
                            "result_count": len(batch_results),
                        })
                except Exception as exc:
                    logger.error(
                        "Batch %d/%d failed (%d nodes), skipping",
                        batch_index + 1,
                        num_batches,
                        len(batch),
                        exc_info=True,
                    )
                    if stream_manager and job_id:
                        stream_manager.publish(job_id, "extraction_batch_error", {
                            "batch_index": batch_index + 1,
                            "error": str(exc),
                        })
        else:
            # Process all nodes in a single invocation
            num_batches = 1
            if stream_manager and job_id:
                stream_manager.publish(job_id, "extraction_batching", {
                    "total_batches": 1,
                    "total_nodes": len(content_nodes),
                })
                stream_manager.publish(job_id, "extraction_batch_start", {
                    "batch_index": 1,
                    "total_batches": 1,
                    "node_count": len(content_nodes),
                })
            prompt = self._build_prompt(content_nodes, url, region, brand)
            logger.info(
                "Invoking Strands agent for %d content nodes from %s",
                len(content_nodes),
                url,
            )
            try:
                all_results = await self._invoke_agent(prompt, job_id, stream_manager)
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "extraction_batch_complete", {
                        "batch_index": 1,
                        "total_batches": 1,
                        "result_count": len(all_results),
                    })
            except Exception as exc:
                logger.error(
                    "Single-batch extraction failed (%d nodes), skipping",
                    len(content_nodes),
                    exc_info=True,
                )
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "extraction_batch_error", {
                        "batch_index": 1,
                        "error": str(exc),
                    })

        if stream_manager and job_id:
            stream_manager.publish(job_id, "extraction_complete", {
                "total_results": len(all_results),
            })

        logger.info("Extraction complete, %d results from %d nodes", len(all_results), len(content_nodes))
        files = PostProcessor.process(all_results, url, region, brand)
        return ExtractionOutput(files=files, child_urls=child_urls)



class PostProcessor:
    """Convert ExtractionResults into MarkdownFiles.

    Handles all deterministic post-processing: SHA-256 hashing,
    filename slugification, YAML frontmatter assembly, and
    MarkdownFile construction.
    """

    @staticmethod
    def process(
        results: list[ExtractionResult],
        url: str,
        region: str,
        brand: str,
        namespace: str = "",
        parent_url: str | None = None,
    ) -> list[MarkdownFile]:
        """Convert ExtractionResults into MarkdownFiles.

        For each ExtractionResult:
        1. Compute SHA-256 hash of markdown_body
        2. Slugify title → filename.md
        3. Assemble YAML frontmatter with revised metadata (no AEM-specific fields)
        4. Build MarkdownFile
        """
        files: list[MarkdownFile] = []
        now = datetime.now(timezone.utc)

        for result in results:
            content_hash = compute_content_hash(result.markdown_body)
            filename = _slugify(result.title) + ".md"

            # Derive key from source_nodes: use last segment of first source node
            if result.source_nodes:
                first_node = result.source_nodes[0]
                key = first_node.rsplit("/", 1)[-1] if "/" in first_node else first_node
            else:
                key = ""

            parent_context = parent_url if parent_url else ""

            # Revised frontmatter — only required fields, no AEM-specific references
            fm_metadata = {
                "key": key,
                "namespace": namespace,
                "brand": brand,
                "region": region,
                "source_url": url,
                "parent_context": parent_context,
                "title": result.title,
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




