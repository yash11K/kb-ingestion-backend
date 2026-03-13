"""Preservation property tests for filter_by_component_type.

**Validates: Requirements 3.1, 3.2**

These tests capture the baseline behavior of filter_by_component_type on UNFIXED code.
They must PASS on the current code and continue to PASS after the fix, ensuring no
regressions in the filtering logic.

Observation-first methodology:
- Observed: _traverse returns ContentNode objects, @tool returns list[dict] via model_dump()
- Observed: Denylist takes precedence — matching both allowlist and denylist excludes the node
- Observed: Denied nodes' children are still recursively traversed
- Observed: Metadata excludes :items, :type, and :itemsOrder keys
- Observed: Glob matching is suffix-based via endswith after stripping */ prefix
- Observed: */text matches "core/richtext" because endswith("text") is True
- Observed: Empty models and models without :items return empty lists
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from src.tools.filter_components import (
    filter_by_component_type,
    _traverse,
    _matches_any,
)
from src.models.schemas import ContentNode


# ---------------------------------------------------------------------------
# Hypothesis strategies: generate random AEM JSON structures
# ---------------------------------------------------------------------------

# Component type segments used to build realistic :type values
_TYPE_PREFIXES = ["core", "components", "mysite", "brand", "custom"]
_TYPE_SUFFIXES = [
    "text", "richtext", "image", "container", "responsivegrid",
    "accordionitem", "tabitem", "page", "button", "title",
    "teaser", "list", "embed", "separator", "download",
]


@st.composite
def component_types(draw) -> str:
    """Generate a random AEM component :type like 'core/components/text'."""
    num_segments = draw(st.integers(min_value=1, max_value=3))
    segments = [draw(st.sampled_from(_TYPE_PREFIXES)) for _ in range(num_segments - 1)]
    segments.append(draw(st.sampled_from(_TYPE_SUFFIXES)))
    return "/".join(segments)


@st.composite
def glob_patterns(draw) -> str:
    """Generate a glob-style pattern like '*/text' or 'text'."""
    suffix = draw(st.sampled_from(_TYPE_SUFFIXES))
    use_glob = draw(st.booleans())
    return f"*/{suffix}" if use_glob else suffix


@st.composite
def aem_leaf_node(draw, node_type_strategy=None) -> dict:
    """Generate a leaf AEM node (no nested :items)."""
    if node_type_strategy is None:
        node_type_strategy = component_types()
    node_type = draw(node_type_strategy)
    node: dict = {":type": node_type}

    # Optionally add HTML content fields
    if draw(st.booleans()):
        html_field = draw(st.sampled_from(["text", "description", "content", "html"]))
        node[html_field] = f"<p>Content for {node_type}</p>"

    # Optionally add metadata
    if draw(st.booleans()):
        node["jcr:title"] = f"Title {draw(st.integers(min_value=0, max_value=100))}"

    return node


@st.composite
def aem_json_tree(draw, max_depth: int = 3, max_children: int = 5) -> dict:
    """Generate a random AEM JSON tree with nested :items.

    Produces trees of varying depth and breadth with random component types.
    """
    if max_depth <= 0 or draw(st.booleans()):
        # Leaf node
        return draw(aem_leaf_node())

    # Internal node with children
    node = draw(aem_leaf_node())
    num_children = draw(st.integers(min_value=0, max_value=max_children))
    if num_children > 0:
        items = {}
        for i in range(num_children):
            child_key = f"item_{i}"
            items[child_key] = draw(aem_json_tree(max_depth=max_depth - 1, max_children=max_children))
        node[":items"] = items

    return node


@st.composite
def aem_root_json(draw) -> dict:
    """Generate a root-level AEM JSON structure with :items."""
    num_children = draw(st.integers(min_value=0, max_value=6))
    items = {}
    for i in range(num_children):
        items[f"node_{i}"] = draw(aem_json_tree(max_depth=2, max_children=3))
    root: dict = {":items": items}
    if draw(st.booleans()):
        root[":type"] = draw(component_types())
    return root


@st.composite
def allowlist_denylist(draw) -> tuple[list[str], list[str]]:
    """Generate random allowlist and denylist pattern combinations."""
    allowlist = draw(st.lists(glob_patterns(), min_size=1, max_size=5))
    denylist = draw(st.lists(glob_patterns(), min_size=0, max_size=3))
    return allowlist, denylist


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


class TestFilterPreservation:
    """Property 2: Preservation — filter_by_component_type produces identical ContentNode results.

    **Validates: Requirements 3.1, 3.2**

    These tests verify that the core filtering logic (_traverse) produces
    results consistent with the @tool-decorated filter_by_component_type,
    and that key behavioral invariants are preserved.
    """

    @given(
        root=aem_root_json(),
        patterns=allowlist_denylist(),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_traverse_matches_tool_output(
        self,
        root: dict,
        patterns: tuple[list[str], list[str]],
    ):
        """The @tool function output equals _traverse output serialized via model_dump.

        **Validates: Requirements 3.1, 3.2**

        For any valid AEM JSON with any allowlist/denylist combination,
        the direct call to _traverse (core logic) produces ContentNode objects
        whose model_dump() matches the @tool-decorated function's output.
        This ensures the tool is a faithful wrapper around the core logic.
        """
        allowlist, denylist = patterns

        # Direct core logic call
        nodes = _traverse(root, "", allowlist, denylist)
        expected = [node.model_dump() for node in nodes]

        # @tool-decorated function call
        actual = filter_by_component_type(
            model_json=root, allowlist=allowlist, denylist=denylist,
        )

        assert actual == expected, (
            f"Tool output differs from _traverse output.\n"
            f"Expected {len(expected)} nodes, got {len(actual)} nodes.\n"
            f"Allowlist: {allowlist}, Denylist: {denylist}"
        )

    @given(
        root=aem_root_json(),
        patterns=allowlist_denylist(),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_denylist_precedence_over_allowlist(
        self,
        root: dict,
        patterns: tuple[list[str], list[str]],
    ):
        """Denylist always takes precedence over allowlist for overlapping patterns.

        **Validates: Requirements 3.2**

        For any AEM JSON and any allowlist/denylist combination, no node whose
        :type matches the denylist should appear in the output, even if it also
        matches the allowlist.
        """
        allowlist, denylist = patterns

        results = filter_by_component_type(
            model_json=root, allowlist=allowlist, denylist=denylist,
        )

        for node_dict in results:
            node_type = node_dict["node_type"]
            assert not _matches_any(node_type, denylist), (
                f"Node with type '{node_type}' matched denylist {denylist} "
                f"but was included in results. Denylist must take precedence."
            )

    @given(
        root=aem_root_json(),
        patterns=allowlist_denylist(),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_all_results_match_allowlist(
        self,
        root: dict,
        patterns: tuple[list[str], list[str]],
    ):
        """Every returned node must match the allowlist.

        **Validates: Requirements 3.1, 3.2**

        For any AEM JSON and any allowlist/denylist combination, every node
        in the output must have a :type that matches at least one allowlist pattern.
        """
        allowlist, denylist = patterns

        results = filter_by_component_type(
            model_json=root, allowlist=allowlist, denylist=denylist,
        )

        for node_dict in results:
            node_type = node_dict["node_type"]
            assert _matches_any(node_type, allowlist), (
                f"Node with type '{node_type}' does not match any allowlist "
                f"pattern {allowlist} but was included in results."
            )

    @given(root=aem_root_json())
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_empty_allowlist_returns_no_results(self, root: dict):
        """An empty allowlist should produce zero results.

        **Validates: Requirements 3.1**

        No nodes can match an empty allowlist, so the output must always be empty.
        """
        results = filter_by_component_type(
            model_json=root, allowlist=[], denylist=[],
        )
        assert results == [], (
            f"Expected empty results with empty allowlist, got {len(results)} nodes."
        )

    @given(
        root=aem_root_json(),
        patterns=allowlist_denylist(),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_content_node_structure_preserved(
        self,
        root: dict,
        patterns: tuple[list[str], list[str]],
    ):
        """Each result has the correct ContentNode fields.

        **Validates: Requirements 3.1**

        Every node dict returned by filter_by_component_type must contain
        exactly the ContentNode fields: node_type, aem_node_id, html_content,
        parent_context, metadata.
        """
        allowlist, denylist = patterns

        results = filter_by_component_type(
            model_json=root, allowlist=allowlist, denylist=denylist,
        )

        expected_keys = {"node_type", "aem_node_id", "html_content", "parent_context", "metadata"}
        for node_dict in results:
            assert set(node_dict.keys()) == expected_keys, (
                f"Node dict keys {set(node_dict.keys())} != expected {expected_keys}"
            )
            # metadata must not contain :items, :type, or :itemsOrder
            meta = node_dict["metadata"]
            assert ":items" not in meta, "metadata must not contain :items"
            assert ":type" not in meta, "metadata must not contain :type"
            assert ":itemsOrder" not in meta, "metadata must not contain :itemsOrder"
