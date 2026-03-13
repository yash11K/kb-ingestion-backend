"""Tests for the fetch_aem_json tool."""

import httpx
import pytest
import respx

from src.tools.fetch_aem import ToolError, fetch_aem_json


class TestFetchAemJson:
    """Unit tests for fetch_aem_json tool."""

    @respx.mock
    def test_returns_parsed_json_on_success(self):
        payload = {"jcr:title": "Test Page", ":items": {"root": {}}}
        respx.get("https://example.com/content/page.model.json").mock(
            return_value=httpx.Response(200, json=payload)
        )

        result = fetch_aem_json(url="https://example.com/content/page.model.json")

        assert result == payload

    @respx.mock
    def test_uses_custom_timeout(self):
        respx.get("https://example.com/page.model.json").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        result = fetch_aem_json(url="https://example.com/page.model.json", timeout=10)

        assert result == {"ok": True}

    @respx.mock
    def test_raises_tool_error_on_non_200_status(self):
        respx.get("https://example.com/missing.model.json").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        with pytest.raises(ToolError, match="HTTP 404"):
            fetch_aem_json(url="https://example.com/missing.model.json")

    @respx.mock
    def test_raises_tool_error_on_500_status(self):
        respx.get("https://example.com/error.model.json").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with pytest.raises(ToolError, match="HTTP 500"):
            fetch_aem_json(url="https://example.com/error.model.json")

    @respx.mock
    def test_raises_tool_error_on_timeout(self):
        respx.get("https://example.com/slow.model.json").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )

        with pytest.raises(ToolError, match="timed out"):
            fetch_aem_json(url="https://example.com/slow.model.json")

    @respx.mock
    def test_raises_tool_error_on_invalid_json(self):
        respx.get("https://example.com/bad.model.json").mock(
            return_value=httpx.Response(200, text="<html>not json</html>")
        )

        with pytest.raises(ToolError, match="Invalid JSON"):
            fetch_aem_json(url="https://example.com/bad.model.json")

    @respx.mock
    def test_raises_tool_error_on_connection_error(self):
        respx.get("https://unreachable.example.com/page.model.json").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with pytest.raises(ToolError, match="Request to .* failed"):
            fetch_aem_json(url="https://unreachable.example.com/page.model.json")

    @respx.mock
    def test_error_message_includes_status_and_body(self):
        respx.get("https://example.com/forbidden.model.json").mock(
            return_value=httpx.Response(403, text="Access Denied")
        )

        with pytest.raises(ToolError) as exc_info:
            fetch_aem_json(url="https://example.com/forbidden.model.json")

        assert "403" in str(exc_info.value)
        assert "Access Denied" in str(exc_info.value)
