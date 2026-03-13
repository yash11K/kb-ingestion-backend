"""Unit tests for the generate_md_file tool."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import frontmatter

from src.tools.md_generator import compute_content_hash, generate_md_file, _slugify


class TestComputeContentHash:
    def test_deterministic(self):
        body = "Hello, world!"
        assert compute_content_hash(body) == compute_content_hash(body)

    def test_sha256_hex(self):
        body = "Some markdown content"
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert compute_content_hash(body) == expected

    def test_empty_body(self):
        assert compute_content_hash("") == hashlib.sha256(b"").hexdigest()


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("FAQ: How to use?") == "faq-how-to-use"

    def test_empty_string(self):
        assert _slugify("") == "untitled"

    def test_whitespace_only(self):
        assert _slugify("   ") == "untitled"

    def test_multiple_spaces(self):
        assert _slugify("a   b   c") == "a-b-c"


class TestGenerateMdFile:
    def _base_metadata(self) -> dict:
        return {
            "title": "Test Article",
            "content_type": "faq",
            "source_url": "https://example.com/page.model.json",
            "component_type": "core/components/text",
            "aem_node_id": "/root/items/text1",
            "parent_context": "/root/items",
        }

    def test_returns_dict_with_all_fields(self):
        result = generate_md_file(
            content="# Hello\nSome body text.",
            metadata=self._base_metadata(),
            region="US",
            brand="TestBrand",
        )
        assert result["status"] == "success"
        expected_keys = {
            "status", "filename", "title", "content_type", "source_url",
            "component_type", "aem_node_id", "md_content", "md_body",
            "content_hash", "modify_date", "extracted_at",
            "parent_context", "region", "brand",
        }
        assert set(result.keys()) == expected_keys

    def test_content_hash_matches_body_only(self):
        body = "# Hello\nSome body text."
        result = generate_md_file(
            content=body,
            metadata=self._base_metadata(),
            region="US",
            brand="TestBrand",
        )
        expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert result["content_hash"] == expected_hash

    def test_md_body_equals_input_content(self):
        body = "# Hello\nSome body text."
        result = generate_md_file(
            content=body,
            metadata=self._base_metadata(),
            region="US",
            brand="TestBrand",
        )
        assert result["md_body"] == body

    def test_frontmatter_contains_all_required_fields(self):
        result = generate_md_file(
            content="Body text",
            metadata=self._base_metadata(),
            region="EU",
            brand="BrandX",
        )
        parsed = frontmatter.loads(result["md_content"])
        required_fields = [
            "title", "content_type", "source_url", "component_type",
            "aem_node_id", "modify_date", "extracted_at",
            "parent_context", "region", "brand",
        ]
        for field in required_fields:
            assert field in parsed.metadata, f"Missing frontmatter field: {field}"

    def test_region_and_brand_from_params(self):
        result = generate_md_file(
            content="Body",
            metadata=self._base_metadata(),
            region="APAC",
            brand="MyBrand",
        )
        assert result["region"] == "APAC"
        assert result["brand"] == "MyBrand"
        parsed = frontmatter.loads(result["md_content"])
        assert parsed.metadata["region"] == "APAC"
        assert parsed.metadata["brand"] == "MyBrand"

    def test_modify_date_from_metadata(self):
        meta = self._base_metadata()
        meta["modify_date"] = "2024-01-15T10:30:00+00:00"
        result = generate_md_file(
            content="Body",
            metadata=meta,
            region="US",
            brand="B",
        )
        parsed_date = datetime.fromisoformat(result["modify_date"])
        assert parsed_date.year == 2024
        assert parsed_date.month == 1
        assert parsed_date.day == 15

    def test_modify_date_defaults_to_now_when_missing(self):
        before = datetime.now(timezone.utc)
        result = generate_md_file(
            content="Body",
            metadata=self._base_metadata(),
            region="US",
            brand="B",
        )
        after = datetime.now(timezone.utc)
        parsed_date = datetime.fromisoformat(result["modify_date"])
        assert before <= parsed_date <= after

    def test_extracted_at_is_current_utc(self):
        before = datetime.now(timezone.utc)
        result = generate_md_file(
            content="Body",
            metadata=self._base_metadata(),
            region="US",
            brand="B",
        )
        after = datetime.now(timezone.utc)
        extracted = datetime.fromisoformat(result["extracted_at"])
        assert before <= extracted <= after

    def test_filename_is_slugified_title(self):
        result = generate_md_file(
            content="Body",
            metadata=self._base_metadata(),
            region="US",
            brand="B",
        )
        assert result["filename"] == "test-article.md"

    def test_md_content_is_parseable_frontmatter(self):
        result = generate_md_file(
            content="# Heading\n\nParagraph text.",
            metadata=self._base_metadata(),
            region="US",
            brand="B",
        )
        parsed = frontmatter.loads(result["md_content"])
        assert parsed.content == "# Heading\n\nParagraph text."
        assert parsed.metadata["title"] == "Test Article"

    def test_different_bodies_same_frontmatter_different_hashes(self):
        meta = self._base_metadata()
        r1 = generate_md_file(content="Body A", metadata=meta, region="US", brand="B")
        r2 = generate_md_file(content="Body B", metadata=meta, region="US", brand="B")
        assert r1["content_hash"] != r2["content_hash"]

    def test_same_body_different_frontmatter_same_hash(self):
        body = "Same body content"
        meta1 = self._base_metadata()
        meta1["title"] = "Title One"
        meta2 = self._base_metadata()
        meta2["title"] = "Title Two"
        r1 = generate_md_file(content=body, metadata=meta1, region="US", brand="B")
        r2 = generate_md_file(content=body, metadata=meta2, region="EU", brand="C")
        assert r1["content_hash"] == r2["content_hash"]
