"""Haiku-based intelligent pre-filter for AEM model.json content.

Replaces the hardcoded allowlist/denylist approach with a cheap Haiku call
that classifies JSON paths as content, navigation, noise, or structural.
Falls back to the legacy filter_by_component_type_direct() on failure.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from src.config import Settings
from src.models.schemas import ContentNode
from src.tools.filter_components import (
    _extract_html_content,
    _has_meaningful_content,
    filter_by_component_type_direct,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an AEM (Adobe Experience Manager) content classifier. You receive a \
schema summary of an AEM model.json page and must classify each node path.

AEM model.json structure:
- Nodes are nested under ":items" objects
- Each node has a ":type" field (e.g. "avis/components/content/text")
- Content nodes have fields like: text, richText, description, bodyContent, \
headline, heroHeadline, heroDescription, body, bodyText
- Noise nodes include: login modals, booking widgets, react components, \
data layers, i18n strings, images, dividers, breadcrumbs, search, forms
- Navigation nodes contain: navigationList, hamburgerMenu, vehicleList, \
accountMenuItems, multiColumnLinks
- Structural nodes are containers that wrap other nodes (:type ends with \
/container, /responsivegrid, /experiencefragment)

Classify each path as ONE of:
- "content" — has meaningful text/article/FAQ content worth extracting
- "navigation" — navigation structures (already handled separately)
- "noise" — login modals, booking widgets, images, dividers, etc.
- "structural" — containers that should be recursed into (not extracted directly)

Return ONLY a JSON array of objects: [{"path": "...", "classification": "..."}]
No explanation, no markdown fences — just the JSON array."""


def _build_schema_summary(node: dict, path: str = "") -> list[dict]:
    """Build a compact schema summary for Haiku classification.

    For each :items child, emits {path, type, keys} but NOT full content.
    """
    summaries: list[dict] = []
    items = node.get(":items")
    if not isinstance(items, dict):
        return summaries

    for key, child in items.items():
        if not isinstance(child, dict):
            continue

        child_path = f"{path}/{key}"
        node_type = child.get(":type", "")

        # Collect top-level keys (excluding :items and :itemsOrder)
        top_keys = [k for k in child.keys() if k not in (":items", ":itemsOrder")]

        summaries.append({
            "path": child_path,
            "type": node_type,
            "keys": top_keys,
        })

        # Recurse
        summaries.extend(_build_schema_summary(child, child_path))

    return summaries


def _extract_subtree(model_json: dict, target_path: str) -> dict | None:
    """Extract a subtree from the model JSON given a path like /root/container/text."""
    segments = [s for s in target_path.split("/") if s]
    current = model_json

    for segment in segments:
        items = current.get(":items")
        if not isinstance(items, dict):
            return None
        if segment not in items:
            return None
        current = items[segment]

    return current


class HaikuPrefilter:
    """Uses Haiku to classify AEM JSON paths as content vs noise."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
        )

    def identify_content_paths(
        self,
        model_json: dict,
    ) -> list[ContentNode]:
        """Classify JSON paths using Haiku and return content nodes.

        Falls back to legacy allowlist filtering on any error.
        """
        try:
            return self._classify_with_haiku(model_json)
        except Exception:
            logger.warning(
                "Haiku pre-filter failed, falling back to allowlist",
                exc_info=True,
            )
            return filter_by_component_type_direct(
                model_json,
                self.settings.allowlist,
                self.settings.denylist,
            )

    def _classify_with_haiku(self, model_json: dict) -> list[ContentNode]:
        """Run Haiku classification and extract content nodes."""
        schema_summary = _build_schema_summary(model_json)

        if not schema_summary:
            return []

        user_msg = json.dumps(schema_summary, indent=2)

        response = self._client.invoke_model(
            modelId=self.settings.haiku_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            }),
        )

        response_body = json.loads(response["body"].read())
        text = response_body["content"][0]["text"]

        # Parse the classification result
        classifications = json.loads(text)
        content_paths = [
            c["path"] for c in classifications
            if c.get("classification") == "content"
        ]

        # Extract subtrees and build ContentNode objects
        nodes: list[ContentNode] = []
        for path in content_paths:
            subtree = _extract_subtree(model_json, path)
            if subtree is None or not isinstance(subtree, dict):
                continue

            if not _has_meaningful_content(subtree):
                continue

            node_type = subtree.get(":type", "unknown")
            metadata = {
                k: v for k, v in subtree.items()
                if k not in (":items", ":type", ":itemsOrder")
            }

            nodes.append(ContentNode(
                node_type=node_type,
                aem_node_id=path,
                html_content=_extract_html_content(subtree),
                parent_context="/".join(path.split("/")[:-1]),
                metadata=metadata,
            ))

        return nodes
