"""generate_md_file tool – generates a Markdown file with YAML frontmatter."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import frontmatter


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug for use as a filename."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-") or "untitled"


def compute_content_hash(md_body: str) -> str:
    """SHA-256 of markdown body only, excluding frontmatter."""
    return hashlib.sha256(md_body.encode("utf-8")).hexdigest()


def generate_md_file(
    content: str,
    metadata: dict,
    region: str,
    brand: str,
) -> dict:
    """Generate a complete Markdown file with YAML frontmatter for the knowledge base.

    Combines the converted markdown body with structured YAML frontmatter
    containing all required metadata fields. Computes a SHA-256 content hash
    of the body for duplicate detection and generates a slug-based filename.

    Args:
        content: The markdown body content (already converted from HTML via
            the ``html_to_markdown`` tool).
        metadata: Dict containing source metadata. Expected keys:
            ``title``, ``content_type``, ``source_url``, ``component_type``,
            ``aem_node_id``, ``parent_context``, and optionally
            ``modify_date`` (ISO 8601 string from AEM dataLayer).
        region: Geographic region for the content (e.g. US, EU, APAC).
        brand: Brand identifier for the content.

    Returns:
        dict with keys:
            - ``status`` (str): ``"success"`` or ``"error"``.
            - On success, all MarkdownFile fields: ``filename``, ``title``,
              ``content_type``, ``source_url``, ``component_type``,
              ``aem_node_id``, ``md_content``, ``md_body``, ``content_hash``,
              ``modify_date``, ``extracted_at``, ``parent_context``,
              ``region``, ``brand``.
            - On error: ``error`` (str) with the failure message.
    """
    try:
        now = datetime.now(timezone.utc)

        title = metadata.get("title", "Untitled")
        content_type = metadata.get("content_type", "")
        source_url = metadata.get("source_url", "")
        component_type = metadata.get("component_type", "")
        aem_node_id = metadata.get("aem_node_id", "")
        parent_context = metadata.get("parent_context", "")

        # modify_date from dataLayer repo:modifyDate, fallback to current UTC
        raw_modify_date = metadata.get("modify_date")
        if raw_modify_date:
            if isinstance(raw_modify_date, datetime):
                modify_date = raw_modify_date
            else:
                modify_date = datetime.fromisoformat(str(raw_modify_date))
            if modify_date.tzinfo is None:
                modify_date = modify_date.replace(tzinfo=timezone.utc)
        else:
            modify_date = now

        extracted_at = now

        # Compute content hash from body only
        content_hash = compute_content_hash(content)

        # Build frontmatter metadata
        fm_metadata = {
            "title": title,
            "content_type": content_type,
            "source_url": source_url,
            "component_type": component_type,
            "aem_node_id": aem_node_id,
            "modify_date": modify_date.isoformat(),
            "extracted_at": extracted_at.isoformat(),
            "parent_context": parent_context,
            "region": region,
            "brand": brand,
        }

        # Generate full markdown with frontmatter using python-frontmatter
        post = frontmatter.Post(content, **fm_metadata)
        md_content = frontmatter.dumps(post)

        # Generate filename from slugified title
        filename = _slugify(title) + ".md"

        return {
            "status": "success",
            "filename": filename,
            "title": title,
            "content_type": content_type,
            "source_url": source_url,
            "component_type": component_type,
            "aem_node_id": aem_node_id,
            "md_content": md_content,
            "md_body": content,
            "content_hash": content_hash,
            "modify_date": modify_date.isoformat(),
            "extracted_at": extracted_at.isoformat(),
            "parent_context": parent_context,
            "region": region,
            "brand": brand,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
