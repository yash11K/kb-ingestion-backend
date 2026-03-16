"""Deep link extractor for AEM content.

Scans extracted ContentNode HTML for embedded <a href="..."> links that point
to internal pages (e.g. "click here to read our smoking policy").  These links
are surfaced for user confirmation, NOT auto-followed.
"""

from __future__ import annotations

import re
import logging
from html.parser import HTMLParser
from urllib.parse import urlparse

from src.models.schemas import ContentNode, DeepLink

logger = logging.getLogger(__name__)

# Regex for extracting href from <a> tags
_HREF_PATTERN = re.compile(
    r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from a string to get plain anchor text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def extract_deep_links(
    nodes: list[ContentNode],
    source_page_url: str,
    base_host: str,
    url_denylist_patterns: list[str],
    known_nav_urls: set[str] | None = None,
) -> list[DeepLink]:
    """Extract embedded internal links from content node HTML.

    Args:
        nodes: ContentNode list from extraction.
        source_page_url: The URL of the page being processed.
        base_host: Scheme + host (e.g. "https://www.avis.com").
        url_denylist_patterns: Path patterns to exclude (e.g. "/reservation", "/login").
        known_nav_urls: Set of URLs already present in the nav tree (to avoid duplicates).

    Returns:
        Deduplicated list of DeepLink objects.
    """
    if known_nav_urls is None:
        known_nav_urls = set()

    seen_urls: set[str] = set()
    deep_links: list[DeepLink] = []

    for node in nodes:
        if not node.html_content:
            continue

        for match in _HREF_PATTERN.finditer(node.html_content):
            href = match.group(1).strip()
            anchor_text = _strip_html_tags(match.group(2))

            # Skip anchors, empty, mailto, tel
            if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue

            # Determine if internal
            is_internal = False
            clean_path = ""

            if href.startswith("/"):
                is_internal = True
                clean_path = href.rstrip("/")
            elif href.startswith("http://") or href.startswith("https://"):
                parsed = urlparse(href)
                base_parsed = urlparse(base_host)
                if parsed.netloc == base_parsed.netloc:
                    is_internal = True
                    clean_path = parsed.path.rstrip("/")

            if not is_internal or not clean_path:
                continue

            # Check denylist patterns
            if any(pattern in clean_path.lower() for pattern in url_denylist_patterns):
                continue

            # Skip if already in nav tree
            if clean_path in known_nav_urls:
                continue

            # Deduplicate
            if clean_path in seen_urls:
                continue
            seen_urls.add(clean_path)

            model_json_url = f"{base_host}{clean_path}.model.json"

            deep_links.append(DeepLink(
                url=clean_path,
                model_json_url=model_json_url,
                anchor_text=anchor_text,
                found_in_node=node.aem_node_id,
                found_in_page=source_page_url,
            ))

    logger.info(
        "Extracted %d deep links from %d nodes on page %s",
        len(deep_links), len(nodes), source_page_url,
    )
    return deep_links
