"""Pre-prune AEM model.json payloads before sending to the discovery agent.

Recursively walks the AEM JSON tree and strips noise — experience fragments
(header/footer), login modals, booking widgets, nav chrome, i18n translation
dictionaries, and analytics dataLayer objects. This dramatically reduces the
token count without removing any page-specific content, so Haiku can focus on
what matters.

This is NOT content filtering. It removes structural site chrome that is
identical across every page on the site and never contains page-specific
knowledge-base content.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Keys to drop entirely from any dict ──────────────────────────────────────
# These are large, repetitive blobs that never contain page content.
_DROP_KEYS: set[str] = {
    "i18n",          # translation string dictionaries (login, MFA, etc.)
    "dataLayer",     # analytics metadata
}

# ── :items child keys to drop (prefix match) ────────────────────────────────
# Experience fragments are shared site-wide header/footer components.
_DROP_ITEMS_PREFIXES: tuple[str, ...] = (
    "experiencefragment",
)

# ── :type values whose entire subtree should be dropped ─────────────────────
# These are interactive/structural components that never hold KB content.
_DROP_TYPES: set[str] = {
    "avis/components/content/loginModal",
    "avis/components/content/headerNavigation",
    "avis/components/content/bookingwidget",
    "avis/components/content/header",
    "avis/components/content/footer",
    "avis/components/content/footerNavigation",
    "avis/components/content/footerLegal",
    "avis/components/content/multiColumnLinks",
    "wcm/msm/components/ghost",
}


def prune_aem_json(aem_json: dict[str, Any]) -> dict[str, Any]:
    """Return a pruned copy of the AEM JSON with noise removed.

    The original dict is not mutated.
    """
    original_size = _rough_size(aem_json)
    pruned = _prune_node(copy.deepcopy(aem_json))
    pruned_size = _rough_size(pruned)

    logger.info(
        "AEM pruner: %d → %d bytes (%.0f%% reduction)",
        original_size,
        pruned_size,
        (1 - pruned_size / original_size) * 100 if original_size else 0,
    )
    return pruned


def _rough_size(obj: Any) -> int:
    """Quick byte-size estimate via repr length."""
    return len(repr(obj))


def _should_drop_type(node: dict[str, Any]) -> bool:
    """Check if a node's :type matches a noise pattern."""
    node_type = node.get(":type", "")
    return node_type in _DROP_TYPES


def _prune_node(node: Any) -> Any:
    """Recursively prune a single node in the AEM JSON tree."""
    if not isinstance(node, dict):
        return node

    # Drop noisy top-level keys
    for key in _DROP_KEYS:
        node.pop(key, None)

    # Prune :items children
    items = node.get(":items")
    if isinstance(items, dict):
        keys_to_drop = []
        for key, child in items.items():
            # Drop by key prefix (experience fragments)
            if any(key.startswith(prefix) for prefix in _DROP_ITEMS_PREFIXES):
                keys_to_drop.append(key)
                continue

            # Drop by :type
            if isinstance(child, dict) and _should_drop_type(child):
                keys_to_drop.append(key)
                continue

            # Recurse into surviving children
            items[key] = _prune_node(child)

        for key in keys_to_drop:
            del items[key]

        # Also clean up :itemsOrder to match
        items_order = node.get(":itemsOrder")
        if isinstance(items_order, list):
            node[":itemsOrder"] = [k for k in items_order if k not in keys_to_drop]

    # Recurse into children array (structural metadata, not content)
    children = node.get("children")
    if isinstance(children, list):
        node["children"] = [
            c for c in children
            if not isinstance(c, dict)
            or not any(
                c.get("name", "").startswith(prefix)
                for prefix in _DROP_ITEMS_PREFIXES
            )
        ]

    # Recurse into any other dict values that might contain nested :items
    for key, value in node.items():
        if key in (":items", "children", ":itemsOrder"):
            continue
        if isinstance(value, dict):
            node[key] = _prune_node(value)
        elif isinstance(value, list):
            node[key] = [_prune_node(item) for item in value]

    return node
