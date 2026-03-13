"""Tests for parse_frontmatter tool."""

import frontmatter
import pytest

from src.tools.frontmatter_parser import REQUIRED_FIELDS, parse_frontmatter


def _build_md(metadata: dict, body: str = "Hello world") -> str:
    """Helper to build a markdown string with YAML frontmatter."""
    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post)


def _full_metadata(**overrides) -> dict:
    """Return a complete metadata dict with all required fields populated."""
    base = {
        "title": "Test Title",
        "content_type": "faq",
        "source_url": "https://example.com/page",
        "component_type": "core/text",
        "key": "contentcardelement_1",
        "namespace": "products-and-services",
        "extracted_at": "2024-06-01T12:00:00+00:00",
        "parent_context": "/root",
        "region": "US",
        "brand": "Acme",
    }
    base.update(overrides)
    return base


class TestParseFrontmatterValid:
    """Tests for valid frontmatter parsing."""

    def test_all_required_fields_present(self):
        md = _build_md(_full_metadata())
        result = parse_frontmatter(md_content=md)

        assert result["status"] == "success"
        assert result["valid"] is True
        assert result["missing_fields"] == []
        assert result["metadata"]["title"] == "Test Title"
        assert result["body"] == "Hello world"

    def test_body_extracted_correctly(self):
        body = "# Heading\n\nSome paragraph content."
        md = _build_md(_full_metadata(), body=body)
        result = parse_frontmatter(md_content=md)

        assert result["body"] == body

    def test_extra_metadata_preserved(self):
        meta = _full_metadata(custom_field="extra_value")
        md = _build_md(meta)
        result = parse_frontmatter(md_content=md)

        assert result["metadata"]["custom_field"] == "extra_value"
        assert result["valid"] is True


class TestParseFrontmatterMissingFields:
    """Tests for missing or empty required fields."""

    def test_single_missing_field(self):
        meta = _full_metadata()
        del meta["title"]
        md = _build_md(meta)
        result = parse_frontmatter(md_content=md)

        assert result["valid"] is False
        assert "title" in result["missing_fields"]

    def test_multiple_missing_fields(self):
        meta = _full_metadata()
        del meta["title"]
        del meta["region"]
        del meta["brand"]
        md = _build_md(meta)
        result = parse_frontmatter(md_content=md)

        assert result["valid"] is False
        assert set(result["missing_fields"]) == {"title", "region", "brand"}

    def test_empty_string_field_treated_as_missing(self):
        meta = _full_metadata(title="")
        md = _build_md(meta)
        result = parse_frontmatter(md_content=md)

        assert result["valid"] is False
        assert "title" in result["missing_fields"]


class TestParseFrontmatterEdgeCases:
    """Tests for edge cases and invalid input."""

    def test_no_frontmatter(self):
        result = parse_frontmatter(md_content="Just plain text, no frontmatter.")

        assert result["metadata"] == {}
        assert result["body"] == "Just plain text, no frontmatter."
        assert result["valid"] is False
        assert set(result["missing_fields"]) == set(REQUIRED_FIELDS)

    def test_empty_string(self):
        result = parse_frontmatter(md_content="")

        assert result["valid"] is False
        assert set(result["missing_fields"]) == set(REQUIRED_FIELDS)

    def test_frontmatter_with_empty_body(self):
        md = _build_md(_full_metadata(), body="")
        result = parse_frontmatter(md_content=md)

        assert result["valid"] is True
        assert result["body"] == ""

    def test_all_fields_missing_returns_full_list(self):
        md = _build_md({})
        result = parse_frontmatter(md_content=md)

        assert result["valid"] is False
        assert set(result["missing_fields"]) == set(REQUIRED_FIELDS)
