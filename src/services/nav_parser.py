"""Navigation tree parser for AEM model.json.

Extracts navigation structures (hamburger menu, footer links, top nav, vehicle
categories) from a raw AEM model.json using known key paths — NOT :items
traversal.  The result is a NavTree that the frontend can render as an
interactive tree for source selection.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from src.models.schemas import NavTree, NavTreeNode, NavTreeSection

logger = logging.getLogger(__name__)


def _resolve_url(path: str, base_host: str) -> tuple[str | None, bool]:
    """Resolve a relative AEM path to a full model.json URL.

    Returns (model_json_url | None, is_external).
    """
    if not path:
        return None, False

    # External link — different host
    if path.startswith("http://") or path.startswith("https://"):
        parsed = urlparse(path)
        base_parsed = urlparse(base_host)
        is_external = parsed.netloc != base_parsed.netloc
        if is_external:
            return None, True
        # Same host, full URL — build model.json URL from path
        clean = parsed.path.rstrip("/")
        if clean.endswith(".model.json"):
            return f"{parsed.scheme}://{parsed.netloc}{clean}", False
        return f"{parsed.scheme}://{parsed.netloc}{clean}.model.json", False

    # Anchor-only or empty
    if path.startswith("#") or not path.strip():
        return None, False

    # Relative internal path
    clean = path.rstrip("/")
    return f"{base_host}{clean}.model.json", False


def _parse_nav_list(items: list[dict], base_host: str) -> list[NavTreeNode]:
    """Parse a navigationList array into NavTreeNode list."""
    nodes: list[NavTreeNode] = []
    for item in items:
        label = item.get("title", "")
        url = item.get("url")
        children: list[NavTreeNode] = []

        # Parse subLinks
        sub_links = item.get("subLinks")
        if isinstance(sub_links, list):
            for sub in sub_links:
                sub_label = sub.get("title", "")
                sub_url = sub.get("url")
                model_url, is_ext = _resolve_url(sub_url or "", base_host)
                children.append(NavTreeNode(
                    label=sub_label,
                    url=sub_url,
                    model_json_url=model_url,
                    is_external=is_ext,
                    children=[],
                ))

        model_url, is_ext = _resolve_url(url or "", base_host)
        nodes.append(NavTreeNode(
            label=label,
            url=url,
            model_json_url=model_url,
            is_external=is_ext,
            children=children,
        ))

    return nodes


def _parse_vehicle_list(items: list[dict], base_host: str) -> list[NavTreeNode]:
    """Parse a vehicleList array into NavTreeNode list."""
    nodes: list[NavTreeNode] = []
    for item in items:
        label = item.get("title", "")
        url = item.get("url")
        model_url, is_ext = _resolve_url(url or "", base_host)
        nodes.append(NavTreeNode(
            label=label,
            url=url,
            model_json_url=model_url,
            is_external=is_ext,
            children=[],
        ))
    return nodes


def _parse_multi_column_links(items: list[dict], base_host: str) -> list[NavTreeNode]:
    """Parse multiColumnLinks linkList into NavTreeNode list."""
    nodes: list[NavTreeNode] = []
    for group in items:
        label = group.get("title", "")
        children: list[NavTreeNode] = []
        sub_links = group.get("subLinks")
        if isinstance(sub_links, list):
            for sub in sub_links:
                sub_label = sub.get("title", "")
                sub_url = sub.get("url")
                model_url, is_ext = _resolve_url(sub_url or "", base_host)
                children.append(NavTreeNode(
                    label=sub_label,
                    url=sub_url,
                    model_json_url=model_url,
                    is_external=is_ext,
                    children=[],
                ))
        nodes.append(NavTreeNode(
            label=label,
            url=None,
            model_json_url=None,
            is_external=False,
            children=children,
        ))
    return nodes


def _find_by_type_suffix(items: dict, suffix: str) -> dict | None:
    """Find a child in :items whose :type ends with the given suffix."""
    for _key, child in items.items():
        if not isinstance(child, dict):
            continue
        node_type = child.get(":type", "")
        if node_type.endswith(suffix):
            return child
        # Recurse into :items
        nested = child.get(":items")
        if isinstance(nested, dict):
            result = _find_by_type_suffix(nested, suffix)
            if result is not None:
                return result
    return None


def parse(model_json: dict, base_url: str) -> NavTree:
    """Parse an AEM model.json into a NavTree structure.

    Args:
        model_json: The raw parsed AEM model.json.
        base_url: The full model.json URL (used to derive scheme + host).

    Returns:
        NavTree with all navigation sections extracted.
    """
    parsed_url = urlparse(base_url)
    base_host = f"{parsed_url.scheme}://{parsed_url.netloc}"

    # Infer brand/region from URL
    from src.utils.url_inference import infer_brand, infer_region
    from src.config import get_settings

    settings = get_settings()
    brand = infer_brand(base_url)
    region = infer_region(base_url, settings.locale_region_map)

    sections: list[NavTreeSection] = []

    # Get root :items
    root_items = model_json.get(":items", {})

    # --- Header Navigation ---
    header_nav = _find_by_type_suffix(root_items, "/headerNavigation")
    if header_nav is None:
        # Try alternate casing
        header_nav = _find_by_type_suffix(root_items, "/headernavigation")

    if header_nav is not None:
        # Hamburger Menu — main site navigation
        hamburger = header_nav.get("hamburgerMenu", {})
        ham_nav_list = hamburger.get("navigationList")
        if isinstance(ham_nav_list, list) and ham_nav_list:
            sections.append(NavTreeSection(
                section_name="Hamburger Menu",
                nodes=_parse_nav_list(ham_nav_list, base_host),
            ))

        # Vehicle Categories
        vehicle_list = hamburger.get("vehicleList")
        if isinstance(vehicle_list, list) and vehicle_list:
            sections.append(NavTreeSection(
                section_name="Vehicle Categories",
                nodes=_parse_vehicle_list(vehicle_list, base_host),
            ))

        # Top Nav Bar
        top_nav_list = header_nav.get("navigationList")
        if isinstance(top_nav_list, list) and top_nav_list:
            sections.append(NavTreeSection(
                section_name="Top Navigation",
                nodes=_parse_nav_list(top_nav_list, base_host),
            ))

    # --- Footer Navigation ---
    footer_links = _find_by_type_suffix(root_items, "/multiColumnLinks")
    if footer_links is not None:
        link_list = footer_links.get("linkList")
        if isinstance(link_list, list) and link_list:
            sections.append(NavTreeSection(
                section_name="Footer Links",
                nodes=_parse_multi_column_links(link_list, base_host),
            ))

    # Footer Legal
    footer_legal = _find_by_type_suffix(root_items, "/footerLegal")
    if footer_legal is not None:
        terms_list = footer_legal.get("termsList")
        if isinstance(terms_list, list) and terms_list:
            legal_nodes: list[NavTreeNode] = []
            for term in terms_list:
                label = term.get("title", "")
                url = term.get("url")
                model_url, is_ext = _resolve_url(url or "", base_host)
                legal_nodes.append(NavTreeNode(
                    label=label,
                    url=url,
                    model_json_url=model_url,
                    is_external=is_ext,
                    children=[],
                ))
            if legal_nodes:
                sections.append(NavTreeSection(
                    section_name="Legal",
                    nodes=legal_nodes,
                ))

    return NavTree(
        brand=brand,
        region=region,
        base_url=base_url,
        sections=sections,
    )
