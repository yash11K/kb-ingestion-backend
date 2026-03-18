"""Haiku-based discovery agent for AEM model.json content.

Receives raw AEM JSON, identifies meaningful content components and internal
links in a single Haiku call. No Python-based filtering or allowlists/denylists.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import Settings
from src.models.schemas import DeepLink, DiscoveredContent, DiscoveryResult
from src.tools.aem_pruner import prune_aem_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an AEM content discovery agent. You receive raw JSON from an Adobe \
Experience Manager (AEM) model.json endpoint.

Your job is to walk the entire JSON tree (recursively through all `:items` \
objects) and:

1. **Identify content components** — components that contain meaningful text \
worth extracting for a knowledge base. Judge by the SUBSTANCE of field values, \
not field names. Examples of content:
   - Product descriptions, feature guides, coverage details
   - FAQ entries, Q&A content
   - Policy text, terms and conditions
   - Articles, blog posts, support documentation
   - Any component with headline, bodyContent, text, description, richText, \
or similar fields containing real descriptive text

   IGNORE these (not content):
   - Pure navigation (menus, breadcrumbs, header/footer link lists)
   - Decorative elements (dividers, spacers, separators, ghost components)
   - Login/booking/search widgets
   - Image-only components with no text
   - Empty components or those with only structural metadata

2. **Extract internal links** — find all links pointing to internal pages \
within the same site. Look in:
   - `ctaLink` fields on content cards
   - `href` attributes inside HTML in `bodyContent`, `text`, `description` etc.
   - Any field that contains an internal URL path (starting with `/`)

   IGNORE external links, anchors (#), mailto:, tel:, and links to \
/reservation, /login, /account, /search, /booking, /checkout, /payment paths.

For each content component found, return:
- `path`: the component's location in the JSON tree (e.g. "/root/responsivegrid/container/text")
- `component_type`: the `:type` value
- `title`: infer a descriptive title from the content
- `content`: concatenate all meaningful text fields into clean text. Strip HTML \
tags. Include headings, body text, descriptions. Do NOT include navigation labels \
or CTA button text as the primary content.
- `modify_date`: extract from `dataLayer` → `repo:modifyDate` if present in or \
near the component, otherwise null

Return a JSON object with this exact structure:
{
  "content_items": [
    {"path": "...", "component_type": "...", "title": "...", "content": "...", "modify_date": "..."}
  ],
  "deep_links": [
    {"url": "/path/to/page", "anchor_text": "Link text"}
  ]
}

Important:
- Process the ENTIRE JSON tree. Do not stop at the first level.
- When uncertain whether something is content, INCLUDE it — false positives \
are cheaper than missed content.
- Return ONLY the JSON object. No explanation, no markdown fences.
- If the JSON contains no meaningful content, return {"content_items": [], "deep_links": []}.
"""


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ~ 4 bytes."""
    return len(text.encode("utf-8")) // 4


def _split_by_top_level_items(aem_json: dict) -> list[dict]:
    """Split a large AEM JSON into chunks by top-level :items children.

    Each chunk is a valid AEM-like dict with a single top-level item,
    preserving the original structure so Haiku can still walk :items.
    """
    items = aem_json.get(":items")
    if not isinstance(items, dict) or len(items) <= 1:
        return [aem_json]

    chunks = []
    base = {k: v for k, v in aem_json.items() if k != ":items"}
    for key, value in items.items():
        chunk = {**base, ":items": {key: value}}
        chunks.append(chunk)
    return chunks


class DiscoveryAgent:
    """Uses Haiku to identify content and links from raw AEM JSON."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model_kwargs = dict(
            model_id=settings.haiku_model_id,
            region_name=settings.aws_region,
            max_tokens=8192,
        )

    async def discover(
        self,
        aem_json: dict,
        source_url: str,
    ) -> DiscoveryResult:
        """Identify content items and deep links from raw AEM JSON.

        If the JSON exceeds the token limit, splits by top-level :items
        children and merges results from multiple Haiku calls.
        """
        # Pre-prune noise (header/footer chrome, login modals, i18n, etc.)
        aem_json = prune_aem_json(aem_json)

        raw = json.dumps(aem_json, default=str)
        estimated_tokens = _estimate_tokens(raw)

        logger.info(
            "Discovery agent: %d bytes, ~%d tokens from %s",
            len(raw), estimated_tokens, source_url,
        )

        max_tokens = self.settings.haiku_max_input_tokens
        if estimated_tokens <= max_tokens:
            return await self._invoke_haiku(raw, source_url)

        # Split and merge
        chunks = _split_by_top_level_items(aem_json)
        logger.info(
            "Payload exceeds %d tokens, splitting into %d chunks",
            max_tokens, len(chunks),
        )

        all_items: list[DiscoveredContent] = []
        all_links: list[DeepLink] = []
        seen_urls: set[str] = set()

        for i, chunk in enumerate(chunks):
            chunk_json = json.dumps(chunk, default=str)
            if _estimate_tokens(chunk_json) > max_tokens:
                logger.warning(
                    "Chunk %d still exceeds token limit (%d tokens), skipping",
                    i, _estimate_tokens(chunk_json),
                )
                continue

            try:
                result = await self._invoke_haiku(chunk_json, source_url)
                all_items.extend(result.content_items)
                for link in result.deep_links:
                    if link.url not in seen_urls:
                        seen_urls.add(link.url)
                        all_links.append(link)
            except Exception:
                logger.warning("Chunk %d failed, skipping", i, exc_info=True)

        return DiscoveryResult(content_items=all_items, deep_links=all_links)

    async def _invoke_haiku(
        self,
        json_payload: str,
        source_url: str,
    ) -> DiscoveryResult:
        """Single Haiku invocation on a JSON payload."""
        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=[],
            system_prompt=_SYSTEM_PROMPT,
        )

        user_msg = (
            f"Extract content and links from this AEM JSON.\n"
            f"Source URL: {source_url}\n\n"
            f"{json_payload}"
        )

        result = await agent.invoke_async(user_msg)
        return self._parse_response(str(result), source_url)

    def _parse_response(
        self,
        response_text: str,
        source_url: str,
    ) -> DiscoveryResult:
        """Parse Haiku response into DiscoveryResult."""
        # Find JSON object in response
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.warning("No valid JSON in discovery response")
            return DiscoveryResult(content_items=[], deep_links=[])

        try:
            data = json.loads(response_text[start:end + 1])
        except json.JSONDecodeError:
            logger.warning("Failed to parse discovery response as JSON")
            return DiscoveryResult(content_items=[], deep_links=[])

        # Parse content items
        content_items: list[DiscoveredContent] = []
        for item in data.get("content_items", []):
            if not isinstance(item, dict):
                continue
            try:
                content_items.append(DiscoveredContent(
                    path=item.get("path", ""),
                    component_type=item.get("component_type", ""),
                    title=item.get("title", "Untitled"),
                    content=item.get("content", ""),
                    modify_date=item.get("modify_date"),
                ))
            except Exception as exc:
                logger.debug("Skipping invalid content item: %s", exc)

        # Parse deep links
        parsed_url = urlparse(source_url)
        base_host = f"{parsed_url.scheme}://{parsed_url.netloc}"
        deep_links: list[DeepLink] = []
        seen_urls: set[str] = set()

        for link in data.get("deep_links", []):
            if not isinstance(link, dict):
                continue
            url = link.get("url", "").strip()
            if not url or url in seen_urls:
                continue

            # Normalize: ensure it starts with /
            if url.startswith("http"):
                link_parsed = urlparse(url)
                if link_parsed.netloc != parsed_url.netloc:
                    continue  # external
                url = link_parsed.path

            if not url.startswith("/"):
                continue

            clean_path = url.rstrip("/")
            seen_urls.add(clean_path)

            deep_links.append(DeepLink(
                url=clean_path,
                model_json_url=f"{base_host}{clean_path}.model.json",
                anchor_text=link.get("anchor_text", ""),
                found_in_node="",
                found_in_page=source_url,
            ))

        logger.info(
            "Discovery: %d content items, %d deep links from %s",
            len(content_items), len(deep_links), source_url,
        )

        return DiscoveryResult(content_items=content_items, deep_links=deep_links)
