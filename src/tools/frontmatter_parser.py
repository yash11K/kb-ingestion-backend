"""parse_frontmatter tool – parses YAML frontmatter and validates required fields."""

from __future__ import annotations

import frontmatter
from strands.tools import tool

REQUIRED_FIELDS: list[str] = [
    "title",
    "content_type",
    "source_url",
    "component_type",
    "key",
    "namespace",
    "extracted_at",
    "parent_context",
    "region",
    "brand",
]


@tool
def parse_frontmatter(md_content: str) -> dict:
    """Parse YAML frontmatter from a markdown string and validate required metadata fields.

    Extracts the YAML frontmatter block (delimited by ``---``) from the
    provided markdown content and checks whether all required knowledge-base
    fields are present and non-empty.

    Args:
        md_content: Full markdown string potentially containing a YAML
            frontmatter block. If no valid frontmatter is found, the entire
            string is treated as the body.

    Returns:
        dict with keys:
            - ``metadata`` (dict): Parsed frontmatter key/value pairs.
            - ``body`` (str): Markdown body after the frontmatter block.
            - ``missing_fields`` (list[str]): Required fields that are absent or empty.
            - ``valid`` (bool): True when all required fields are present.
            - ``status`` (str): ``"success"`` or ``"error"``.
    """
    try:
        post = frontmatter.loads(md_content)
        metadata = dict(post.metadata)
        body = post.content
    except Exception as exc:
        return {
            "status": "error",
            "metadata": {},
            "body": md_content,
            "missing_fields": REQUIRED_FIELDS[:],
            "valid": False,
            "error": f"Failed to parse frontmatter: {exc}",
        }

    missing_fields = [
        field for field in REQUIRED_FIELDS
        if not metadata.get(field)
    ]

    return {
        "status": "success",
        "metadata": metadata,
        "body": body,
        "missing_fields": missing_fields,
        "valid": len(missing_fields) == 0,
    }
