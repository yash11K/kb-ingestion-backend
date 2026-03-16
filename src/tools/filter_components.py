"""filter_by_component_type tool – recursively traverses AEM model.json and filters by component type."""

from __future__ import annotations

from strands.tools import tool

from src.models.schemas import ContentNode


def _matches_any(node_type: str, patterns: list[str]) -> bool:
    """Check if a node type matches any of the glob-style patterns.

    Glob-style suffix matching: "*/text" matches "core/components/text".
    The "*/" prefix is stripped and the remainder is checked via endswith.
    """
    for pattern in patterns:
        suffix = pattern.removeprefix("*/")
        if node_type.endswith(suffix):
            return True
    return False

def _is_react_or_widget(node_type: str) -> bool:
    """Return True if the component type suffix contains 'react' or 'widget'.

    The suffix is the last segment after the final ``/`` in the :type string.
    Matching is case-insensitive.
    """
    suffix = node_type.rsplit("/", 1)[-1].lower()
    return "react" in suffix or "widget" in suffix


# Fields that are structural / non-content AEM metadata.
_STRUCTURAL_FIELDS = frozenset({
    ":type", ":items", ":itemsOrder", "id",
    "i18n", "dataLayer", "appliedCssClassNames",
})

# Fields known to carry actual text content.
_TEXT_CONTENT_FIELDS = frozenset({
    "text", "description", "content", "html", "richText", "body", "bodyText",
    "heroHeadline", "heroDescription", "bodyContent",
    "headline", "title",
})


def _has_meaningful_content(node: dict) -> bool:
    """Return True if the node has fields beyond structural/config-only data.

    A node is considered to lack meaningful content when:
    - Its only non-structural fields are i18n keys, dataLayer, or appliedCssClassNames
    - It contains only ``id`` and ``:type`` with no text content fields
    """
    for key, value in node.items():
        if key in _STRUCTURAL_FIELDS:
            continue
        # If we find any known text content field with a non-empty string, it's meaningful
        if key in _TEXT_CONTENT_FIELDS:
            if isinstance(value, str) and value.strip():
                return True
            continue
        # Any other field with a non-trivial value counts as meaningful
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (int, float, bool)):
            return True
        if isinstance(value, (list, dict)) and value:
            return True
    return False


def _extract_html_content(node: dict) -> str:
    """Extract HTML content from an AEM node by checking common HTML fields.

    Concatenates all matching fields so components with multiple HTML
    properties (e.g. herobanner with heroHeadline + heroDescription,
    or contentcardelement with headline + bodyContent) are fully captured.

    Plain-text heading fields (headline, title) are wrapped in an <h3> tag
    so markdownify converts them to a proper Markdown heading.
    """
    # Fields that contain raw HTML
    html_fields = [
        "text", "description", "content", "html", "richText", "body", "bodyText",
        "heroHeadline", "heroDescription",
        "bodyContent",   # contentcardelement, contentcard
    ]
    # Fields that are plain-text display headings (not navigation/SEO titles)
    heading_fields = ["headline"]

    parts: list[str] = []

    # Prepend heading as an <h3> so markdownify produces ### Heading
    for field in heading_fields:
        value = node.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(f"<h3>{value.strip()}</h3>")
            break  # only use the first heading field found

    for field in html_fields:
        if field in node and isinstance(node[field], str) and node[field].strip():
            parts.append(node[field])

    return "\n".join(parts) if parts else ""


def _traverse(
    node: dict,
    parent_path: str,
    allowlist: list[str],
    denylist: list[str],
) -> list[ContentNode]:
    """Recursively traverse :items objects and collect matching ContentNodes."""
    results: list[ContentNode] = []
    items = node.get(":items")
    if not isinstance(items, dict):
        return results

    for key, child in items.items():
        if not isinstance(child, dict):
            continue

        child_path = f"{parent_path}/{key}"
        node_type = child.get(":type")

        if node_type is not None:
            if _matches_any(node_type, denylist):
                # Denylist takes precedence – skip this node but still recurse
                pass
            elif _is_react_or_widget(node_type):
                # Skip react/widget components regardless of allowlist
                pass
            elif _matches_any(node_type, allowlist):
                # Content quality gate: skip nodes with no meaningful content
                if _has_meaningful_content(child):
                    # Collect metadata (everything except :items, :type, and known HTML fields)
                    metadata = {
                        k: v
                        for k, v in child.items()
                        if k not in (":items", ":type", ":itemsOrder")
                    }

                    results.append(
                        ContentNode(
                            node_type=node_type,
                            aem_node_id=child_path,
                            html_content=_extract_html_content(child),
                            parent_context=parent_path,
                            metadata=metadata,
                        )
                    )

        # Always recurse into nested :items regardless of match/deny
        results.extend(_traverse(child, child_path, allowlist, denylist))

    return results


def filter_by_component_type_direct(
    model_json: dict,
    allowlist: list[str],
    denylist: list[str],
) -> list[ContentNode]:
    """Recursively traverse :items and filter by component type (direct call).

    This is the core filtering logic extracted as a plain Python function
    so it can be called directly without going through the Strands agent's
    LLM context window. This avoids unnecessary token consumption for what
    is a deterministic operation.

    Args:
        model_json: The parsed AEM model.json object.
        allowlist: List of glob-style component type patterns to include
                   (e.g. ``["*/text", "*/accordion"]``).
        denylist: List of glob-style component type patterns to exclude.
                  Denylist takes precedence over allowlist.

    Returns:
        List of ContentNode objects for nodes whose :type matches the allowlist
        and does not match the denylist.
    """
    return _traverse(model_json, "", allowlist, denylist)


@tool
def filter_by_component_type(
    model_json: dict,
    allowlist: list[str],
    denylist: list[str],
) -> list[dict]:
    """Recursively traverse :items and filter by component type.

    Args:
        model_json: The parsed AEM model.json object.
        allowlist: List of glob-style component type patterns to include
                   (e.g. ``["*/text", "*/accordion"]``).
        denylist: List of glob-style component type patterns to exclude.
                  Denylist takes precedence over allowlist.

    Returns:
        List of ContentNode dicts for nodes whose :type matches the allowlist
        and does not match the denylist.
    """
    nodes = filter_by_component_type_direct(model_json, allowlist, denylist)
    return [node.model_dump() for node in nodes]
