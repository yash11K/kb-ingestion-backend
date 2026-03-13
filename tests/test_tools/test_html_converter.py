"""Tests for the html_to_markdown tool."""

from src.tools.html_converter import html_to_markdown


class TestHtmlToMarkdown:
    """Unit tests for html_to_markdown tool."""

    def test_empty_string_returns_empty(self):
        result = html_to_markdown(html_content="")
        assert result["status"] == "success"
        assert result["markdown"] == ""

    def test_converts_bold_tag(self):
        result = html_to_markdown(html_content="<strong>bold</strong>")
        assert result["status"] == "success"
        assert "**bold**" in result["markdown"]
        assert "<" not in result["markdown"]

    def test_converts_paragraph(self):
        result = html_to_markdown(html_content="<p>Hello world</p>")
        assert result["status"] == "success"
        assert "Hello world" in result["markdown"]
        assert "<p>" not in result["markdown"]

    def test_converts_heading(self):
        result = html_to_markdown(html_content="<h1>Title</h1>")
        assert result["status"] == "success"
        assert "Title" in result["markdown"]
        assert "<h1>" not in result["markdown"]

    def test_converts_link(self):
        result = html_to_markdown(html_content='<a href="https://example.com">click</a>')
        assert result["status"] == "success"
        assert "click" in result["markdown"]
        assert "<a" not in result["markdown"]

    def test_strips_remaining_html_tags(self):
        result = html_to_markdown(html_content="<div><span>text</span></div>")
        assert result["status"] == "success"
        assert "<" not in result["markdown"]
        assert "text" in result["markdown"]

    def test_strips_whitespace(self):
        result = html_to_markdown(html_content="  <p>content</p>  ")
        assert result["status"] == "success"
        assert result["markdown"] == result["markdown"].strip()

    def test_complex_nested_html(self):
        html = "<div><h2>Section</h2><p>Some <em>italic</em> and <strong>bold</strong> text.</p></div>"
        result = html_to_markdown(html_content=html)
        assert result["status"] == "success"
        assert "<" not in result["markdown"]
        assert "Section" in result["markdown"]
        assert "italic" in result["markdown"]
        assert "bold" in result["markdown"]

    def test_none_like_empty_input(self):
        result = html_to_markdown(html_content="")
        assert result["status"] == "success"
        assert result["markdown"] == ""

    def test_plain_text_passthrough(self):
        result = html_to_markdown(html_content="just plain text")
        assert result["status"] == "success"
        assert result["markdown"] == "just plain text"

    def test_list_conversion(self):
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        result = html_to_markdown(html_content=html)
        assert result["status"] == "success"
        assert "<" not in result["markdown"]
        assert "Item 1" in result["markdown"]
        assert "Item 2" in result["markdown"]
