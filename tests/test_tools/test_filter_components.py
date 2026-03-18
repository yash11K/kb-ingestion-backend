"""Unit tests for filter_by_component_type tool."""

from src.tools.filter_components import (
    _matches_any,
    _traverse,
    _extract_html_content,
    _is_react_or_widget,
    _has_meaningful_content,
    filter_by_component_type,
    filter_by_denylist_only,
)


class TestMatchesAny:
    def test_glob_suffix_match(self):
        assert _matches_any("core/components/text", ["*/text"]) is True

    def test_glob_suffix_no_match(self):
        assert _matches_any("core/components/text", ["*/image"]) is False

    def test_exact_suffix_match(self):
        assert _matches_any("text", ["*/text"]) is True

    def test_multiple_patterns(self):
        assert _matches_any("core/components/image", ["*/text", "*/image"]) is True

    def test_empty_patterns(self):
        assert _matches_any("core/components/text", []) is False

    def test_pattern_without_glob_prefix(self):
        """Patterns without */ prefix still work – removeprefix is a no-op."""
        assert _matches_any("core/components/text", ["text"]) is True


class TestExtractHtmlContent:
    def test_extracts_text_field(self):
        assert _extract_html_content({"text": "<p>Hello</p>"}) == "<p>Hello</p>"

    def test_extracts_description_field(self):
        assert _extract_html_content({"description": "<b>Desc</b>"}) == "<b>Desc</b>"

    def test_returns_empty_when_no_html_fields(self):
        assert _extract_html_content({":type": "something"}) == ""

    def test_prefers_first_matching_field(self):
        node = {"text": "<p>Text</p>", "description": "<p>Desc</p>"}
        assert _extract_html_content(node) == "<p>Text</p>\n<p>Desc</p>"


class TestTraverse:
    def test_simple_flat_items(self):
        model = {
            ":items": {
                "item1": {":type": "core/text", "text": "<p>Hello</p>"},
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 1
        assert results[0].node_type == "core/text"
        assert results[0].aem_node_id == "/item1"
        assert results[0].parent_context == ""

    def test_nested_items(self):
        model = {
            ":items": {
                "container": {
                    ":type": "core/container",
                    ":items": {
                        "text1": {":type": "core/text", "text": "<p>Nested</p>"},
                    },
                }
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 1
        assert results[0].aem_node_id == "/container/text1"
        assert results[0].parent_context == "/container"

    def test_denylist_takes_precedence(self):
        model = {
            ":items": {
                "item1": {":type": "core/text", "text": "<p>Hello</p>"},
            }
        }
        results = _traverse(model, "", ["*/text"], ["*/text"])
        assert len(results) == 0

    def test_denied_node_children_still_traversed(self):
        model = {
            ":items": {
                "container": {
                    ":type": "core/container",
                    ":items": {
                        "text1": {":type": "core/text", "text": "<p>Inside</p>"},
                    },
                }
            }
        }
        results = _traverse(model, "", ["*/text", "*/container"], ["*/container"])
        assert len(results) == 1
        assert results[0].node_type == "core/text"

    def test_no_items_returns_empty(self):
        results = _traverse({"key": "value"}, "", ["*/text"], [])
        assert results == []

    def test_deeply_nested(self):
        model = {
            ":items": {
                "l1": {
                    ":type": "core/grid",
                    ":items": {
                        "l2": {
                            ":type": "core/grid",
                            ":items": {
                                "l3": {":type": "core/text", "text": "<p>Deep</p>"},
                            },
                        }
                    },
                }
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 1
        assert results[0].aem_node_id == "/l1/l2/l3"
        assert results[0].parent_context == "/l1/l2"

    def test_multiple_matches(self):
        model = {
            ":items": {
                "a": {":type": "core/text", "text": "<p>A</p>"},
                "b": {":type": "core/richtext", "text": "<p>B</p>"},
                "c": {":type": "core/image"},
            }
        }
        results = _traverse(model, "", ["*/text", "*/richtext"], [])
        assert len(results) == 2
        types = {r.node_type for r in results}
        assert types == {"core/text", "core/richtext"}


class TestFilterByComponentTypeTool:
    def test_returns_list_of_dicts(self):
        model = {
            ":items": {
                "item1": {":type": "core/text", "text": "<p>Hello</p>"},
            }
        }
        result = filter_by_component_type(
            model_json=model, allowlist=["*/text"], denylist=[]
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["node_type"] == "core/text"
        assert result[0]["aem_node_id"] == "/item1"
        assert result[0]["html_content"] == "<p>Hello</p>"
        assert result[0]["parent_context"] == ""

    def test_empty_model(self):
        result = filter_by_component_type(model_json={}, allowlist=["*/text"], denylist=[])
        assert result == []


class TestIsReactOrWidget:
    def test_react_suffix(self):
        assert _is_react_or_widget("core/components/reactHeader") is True

    def test_widget_suffix(self):
        assert _is_react_or_widget("core/components/bookingwidget") is True

    def test_react_case_insensitive(self):
        assert _is_react_or_widget("core/components/ReactNav") is True

    def test_widget_case_insensitive(self):
        assert _is_react_or_widget("core/components/BookingWidget") is True

    def test_normal_component(self):
        assert _is_react_or_widget("core/components/text") is False

    def test_react_in_middle_of_path_not_suffix(self):
        """Only the suffix (last segment) matters, not middle path segments."""
        assert _is_react_or_widget("react/components/text") is False

    def test_widget_embedded_in_suffix(self):
        assert _is_react_or_widget("core/mywidgetpanel") is True


class TestHasMeaningfulContent:
    def test_node_with_text_content(self):
        assert _has_meaningful_content({":type": "core/text", "text": "<p>Hello</p>"}) is True

    def test_node_with_only_type_and_id(self):
        assert _has_meaningful_content({":type": "core/text", "id": "abc123"}) is False

    def test_node_with_only_structural_fields(self):
        assert _has_meaningful_content({
            ":type": "core/text",
            ":items": {"child": {}},
            ":itemsOrder": ["child"],
            "id": "abc",
        }) is False

    def test_node_with_only_i18n_and_datalayer(self):
        assert _has_meaningful_content({
            ":type": "core/text",
            "i18n": {"key": "value"},
            "dataLayer": {"page": "info"},
            "appliedCssClassNames": "some-class",
        }) is False

    def test_node_with_description(self):
        assert _has_meaningful_content({":type": "core/teaser", "description": "<p>Info</p>"}) is True

    def test_node_with_headline(self):
        assert _has_meaningful_content({":type": "core/card", "headline": "My Title"}) is True

    def test_node_with_empty_text_field(self):
        assert _has_meaningful_content({":type": "core/text", "text": "   "}) is False

    def test_node_with_custom_non_empty_string_field(self):
        assert _has_meaningful_content({":type": "core/text", "ctaLink": "/en/products"}) is True

    def test_node_with_numeric_field(self):
        assert _has_meaningful_content({":type": "core/text", "order": 5}) is True

    def test_node_with_non_empty_list(self):
        assert _has_meaningful_content({":type": "core/text", "tags": ["faq"]}) is True

    def test_node_with_empty_list(self):
        assert _has_meaningful_content({":type": "core/text", "tags": []}) is False


class TestTraverseReactWidgetFiltering:
    def test_react_component_skipped_even_if_allowlisted(self):
        model = {
            ":items": {
                "item1": {":type": "core/reactHeader", "text": "<p>Hello</p>"},
            }
        }
        results = _traverse(model, "", ["*/reactHeader"], [])
        assert len(results) == 0

    def test_widget_component_skipped_even_if_allowlisted(self):
        model = {
            ":items": {
                "item1": {":type": "core/bookingwidget", "text": "<p>Book now</p>"},
            }
        }
        results = _traverse(model, "", ["*/bookingwidget"], [])
        assert len(results) == 0

    def test_react_widget_children_still_traversed(self):
        model = {
            ":items": {
                "widget": {
                    ":type": "core/reactWidget",
                    ":items": {
                        "text1": {":type": "core/text", "text": "<p>Inside widget</p>"},
                    },
                }
            }
        }
        results = _traverse(model, "", ["*/text", "*/reactWidget"], [])
        assert len(results) == 1
        assert results[0].node_type == "core/text"


class TestTraverseContentQualityFiltering:
    def test_node_with_only_i18n_skipped(self):
        model = {
            ":items": {
                "item1": {
                    ":type": "core/text",
                    "i18n": {"key": "translation"},
                    "dataLayer": {"page": "info"},
                },
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 0

    def test_node_with_only_id_and_type_skipped(self):
        model = {
            ":items": {
                "item1": {":type": "core/text", "id": "abc123"},
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 0

    def test_node_with_real_content_passes(self):
        model = {
            ":items": {
                "item1": {
                    ":type": "core/text",
                    "text": "<p>Real content</p>",
                    "i18n": {"key": "translation"},
                },
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 1

    def test_node_with_only_appliedcssclassnames_skipped(self):
        model = {
            ":items": {
                "item1": {
                    ":type": "core/text",
                    "appliedCssClassNames": "some-class",
                },
            }
        }
        results = _traverse(model, "", ["*/text"], [])
        assert len(results) == 0


class TestFilterByDenylistOnly:
    """Tests for the denylist-only filter (no allowlist required)."""

    def test_accepts_any_content_node(self):
        """Nodes with meaningful content are accepted regardless of type."""
        model = {
            ":items": {
                "card": {":type": "avis/content/contentcardelement", "headline": "LDW", "bodyContent": "<p>Covers damage</p>"},
            }
        }
        results = filter_by_denylist_only(model, [])
        assert len(results) == 1
        assert results[0].node_type == "avis/content/contentcardelement"

    def test_denylisted_nodes_excluded(self):
        model = {
            ":items": {
                "login": {":type": "core/loginModal", "text": "<p>Login</p>"},
                "text": {":type": "core/text", "text": "<p>Content</p>"},
            }
        }
        results = filter_by_denylist_only(model, ["*/loginModal"])
        assert len(results) == 1
        assert results[0].node_type == "core/text"

    def test_react_widget_excluded(self):
        model = {
            ":items": {
                "widget": {":type": "core/bookingwidget", "text": "<p>Book</p>"},
                "text": {":type": "core/text", "text": "<p>Content</p>"},
            }
        }
        results = filter_by_denylist_only(model, [])
        assert len(results) == 1
        assert results[0].node_type == "core/text"

    def test_no_meaningful_content_excluded(self):
        model = {
            ":items": {
                "empty": {":type": "core/text", "id": "abc", "i18n": {"key": "val"}},
            }
        }
        results = filter_by_denylist_only(model, [])
        assert len(results) == 0

    def test_denied_node_children_still_traversed(self):
        model = {
            ":items": {
                "container": {
                    ":type": "core/container",
                    ":items": {
                        "text1": {":type": "core/text", "text": "<p>Nested</p>"},
                    },
                }
            }
        }
        results = filter_by_denylist_only(model, ["*/container"])
        assert len(results) == 1
        assert results[0].node_type == "core/text"

    def test_multiple_content_types_accepted(self):
        """Accepts various component types — no allowlist restriction."""
        model = {
            ":items": {
                "text": {":type": "core/text", "text": "<p>Text</p>"},
                "faq": {":type": "core/faq", "description": "<p>FAQ</p>"},
                "card": {":type": "avis/contentcardelement", "headline": "Coverage", "bodyContent": "<p>Info</p>"},
                "footer": {":type": "avis/footerLegal", "text": "<p>Legal</p>"},
            }
        }
        results = filter_by_denylist_only(model, [])
        assert len(results) == 4
