"""html_to_markdown tool – converts HTML content to clean Markdown."""

from __future__ import annotations

import re

from markdownify import markdownify


def html_to_markdown(html_content: str) -> dict:
    """Convert an HTML string to clean Markdown using markdownify.

    Strips any residual HTML tags from the converted output so the result
    is pure Markdown suitable for knowledge-base storage.

    Args:
        html_content: Raw HTML string to convert. May contain any valid
            HTML elements (headings, paragraphs, lists, tables, etc.).

    Returns:
        dict with keys:
            - ``status`` (str): ``"success"`` or ``"error"``.
            - ``markdown`` (str): The converted Markdown text (empty string
              on error or empty input).
            - ``error`` (str | None): Error message if conversion failed.
    """
    if not html_content:
        return {"status": "success", "markdown": ""}

    try:
        result = markdownify(html_content)
        # Strip any remaining HTML tags
        result = re.sub(r"<[^>]+>", "", result)
        return {"status": "success", "markdown": result.strip()}
    except Exception as exc:
        return {"status": "error", "markdown": "", "error": str(exc)}
