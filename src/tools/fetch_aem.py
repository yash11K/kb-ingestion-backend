"""fetch_aem_json tool – fetches and parses JSON from an AEM model.json endpoint."""

from __future__ import annotations

import httpx
from strands.tools import tool


class ToolError(Exception):
    """Raised when a Strands tool encounters a recoverable error."""


@tool
def fetch_aem_json(url: str, timeout: int = 30) -> dict:
    """Fetch and parse JSON from an Adobe Experience Manager model.json endpoint.

    Makes an HTTP GET request to the given AEM URL and returns the parsed
    JSON response. Used as the first step in the content extraction pipeline
    to retrieve the raw AEM page structure.

    Args:
        url: The full AEM model.json endpoint URL to fetch
            (e.g. ``https://example.com/content/page.model.json``).
        timeout: Request timeout in seconds. Defaults to 30.

    Returns:
        dict with keys:
            - ``status`` (str): ``"success"`` or ``"error"``.
            - ``content`` (dict): Parsed JSON object from the AEM endpoint
              (only on success).
            - ``error`` (str): Error description (only on failure).

    Raises:
        ToolError: On non-200 status, timeout, or invalid JSON.
    """
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException:
        raise ToolError(f"Request to {url} timed out after {timeout} seconds")
    except httpx.RequestError as exc:
        raise ToolError(f"Request to {url} failed: {exc}")

    if response.status_code != 200:
        raise ToolError(
            f"AEM endpoint returned HTTP {response.status_code}: {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ToolError(f"Invalid JSON response from {url}: {exc}")
