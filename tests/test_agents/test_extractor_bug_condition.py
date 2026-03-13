"""Bug condition exploration test for MaxTokensReachedException.

**Validates: Requirements 1.1, 1.2, 1.4**

This test encodes the EXPECTED (correct) behavior after the fix:
- No MaxTokensReachedException raised for large payloads
- filter_by_component_type called as a direct Python function (not LLM tool)
- Agent context contains only filtered ContentNode data

On UNFIXED code, this test is EXPECTED TO FAIL because:
- The current ExtractorAgent passes full unfiltered JSON through the LLM context
- filter_by_component_type is registered as an LLM tool, not called directly
- Large payloads exceed the agent's context window token limit
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, AsyncMock

import httpx
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from src.agents.extractor import ExtractorAgent
from src.config import Settings
from src.models.schemas import ContentNode


# ---------------------------------------------------------------------------
# Helpers: build large AEM JSON payloads that trigger the bug condition
# ---------------------------------------------------------------------------

def _build_aem_node(index: int, node_type: str = "core/components/text") -> dict:
    """Build a single AEM content node with realistic HTML content."""
    # Each node has ~5 KB of HTML content to bulk up the payload
    html_content = f"<div><h2>Section {index}</h2>" + "<p>Lorem ipsum dolor sit amet. </p>" * 100 + "</div>"
    return {
        ":type": node_type,
        "text": html_content,
        "jcr:title": f"Node {index}",
        "jcr:lastModified": "2024-01-15T10:30:00.000Z",
    }


def _build_large_aem_json(num_nodes: int) -> dict:
    """Build an AEM JSON payload with the specified number of content nodes.

    Creates a flat :items structure with many nodes to produce a payload
    large enough to exceed the agent's context window token limit.
    """
    items = {}
    for i in range(num_nodes):
        items[f"node_{i}"] = _build_aem_node(i)
    return {
        "jcr:title": "Large Test Page",
        ":type": "core/components/page",
        ":items": items,
    }


def _estimate_tokens(payload: dict) -> int:
    """Rough byte-to-token estimate (1 token ≈ 4 bytes)."""
    return len(json.dumps(payload)) // 4


# ---------------------------------------------------------------------------
# Hypothesis strategy: generate large AEM payloads that satisfy isBugCondition
# ---------------------------------------------------------------------------

@st.composite
def large_aem_payloads(draw):
    """Generate AEM JSON payloads large enough to trigger the bug condition.

    The bug condition requires:
    - estimated_tokens > agent context token limit
    - filter_by_component_type invoked as LLM tool (not direct call)
    - agent receives full unfiltered JSON in context window

    We generate payloads with 400-600 nodes (~2-3 MB) to reliably exceed limits.
    """
    num_nodes = draw(st.integers(min_value=400, max_value=600))
    payload = _build_large_aem_json(num_nodes)

    # Verify the payload is large enough to trigger the bug condition
    payload_bytes = len(json.dumps(payload))
    assert payload_bytes > 500_000, f"Payload too small: {payload_bytes} bytes"

    return payload


# ---------------------------------------------------------------------------
# Mock settings for testing
# ---------------------------------------------------------------------------

def _make_test_settings() -> Settings:
    """Create a Settings instance for testing without requiring .env file."""
    return Settings(
        database_url="postgresql://test:test@localhost:5432/testdb",
        aws_region="us-east-1",
        s3_bucket_name="test-bucket",
        bedrock_model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        allowlist=["*/text", "*/richtext", "*/accordionitem"],
        denylist=["*/responsivegrid", "*/container", "*/page"],
    )


# ---------------------------------------------------------------------------
# Bug condition exploration test
# ---------------------------------------------------------------------------

class MaxTokensReachedException(Exception):
    """Simulates the Strands SDK MaxTokensReachedException."""
    pass


class TestBugConditionExploration:
    """Exploration tests that demonstrate the bug exists on unfixed code.

    These tests encode the EXPECTED behavior (post-fix). They FAIL on unfixed
    code, which confirms the bug exists. After the fix is implemented, these
    tests should PASS.
    """

    @given(payload=large_aem_payloads())
    @settings(
        max_examples=5,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_large_payload_does_not_raise_max_tokens(self, payload: dict):
        """Property 1: Large AEM JSON payloads should NOT raise MaxTokensReachedException.

        **Validates: Requirements 1.1, 1.2, 1.4**

        Bug condition (isBugCondition):
        - Payload exceeds token limit (estimated_tokens > context limit)
        - filter_by_component_type is invoked as LLM tool
        - Agent receives full unfiltered JSON in context window

        Expected behavior (post-fix):
        - No MaxTokensReachedException raised
        - filter_by_component_type called as direct Python function
        - Agent context contains only filtered ContentNode data

        On UNFIXED code: This test FAILS because the ExtractorAgent passes
        the full unfiltered JSON through the LLM context as a tool argument,
        causing MaxTokensReachedException for large payloads.
        """
        # Verify this payload satisfies the bug condition (large enough)
        payload_bytes = len(json.dumps(payload))
        estimated_tokens = payload_bytes // 4
        assert estimated_tokens > 100_000, "Payload must be large enough to trigger bug"

        settings = _make_test_settings()

        # Track whether filter_by_component_type_direct is called directly
        filter_called_directly = False
        filter_call_args = {}

        def mock_filter_direct(model_json, allowlist, denylist):
            nonlocal filter_called_directly, filter_call_args
            filter_called_directly = True
            filter_call_args = {
                "payload_bytes": len(json.dumps(model_json)),
                "allowlist": allowlist,
                "denylist": denylist,
            }
            # Return realistic filtered nodes (much smaller than input)
            return [
                ContentNode(
                    node_type="core/components/text",
                    aem_node_id="/node_0",
                    html_content="<p>Filtered content</p>",
                    parent_context="",
                    metadata={"jcr:title": "Node 0"},
                )
            ]

        # Mock the Strands Agent to simulate token limit behavior.
        # On UNFIXED code: the agent receives the full payload and raises
        # MaxTokensReachedException when the tool argument is too large.
        # On FIXED code: the agent only receives filtered data and succeeds.
        async def mock_invoke_async(prompt):
            """Simulate Strands Agent invoke_async behavior.

            If the prompt contains a very large payload (indicating the full
            unfiltered JSON was passed through), raise MaxTokensReachedException.
            If the prompt is small (filtered data only), return success.
            """
            prompt_str = str(prompt)
            prompt_bytes = len(prompt_str.encode("utf-8"))
            estimated_prompt_tokens = prompt_bytes // 4

            # If the agent receives more than 100K tokens, it exceeds the limit
            if estimated_prompt_tokens > 100_000:
                raise MaxTokensReachedException(
                    "Agent has reached an unrecoverable state due to max_tokens limit"
                )

            # Small prompt = filtered data only, return mock result
            mock_result = MagicMock()
            mock_result.tool_results = None
            mock_result.__str__ = lambda self: "[]"
            return mock_result

        # Patch BedrockModel and Agent to avoid real AWS calls
        with patch("src.agents.extractor.BedrockModel") as mock_bedrock, \
             patch("src.agents.extractor.Agent") as mock_agent_cls, \
             patch("src.agents.extractor.filter_by_component_type_direct", side_effect=mock_filter_direct) as mock_filter, \
             patch("src.agents.extractor.httpx") as mock_httpx:

            mock_model = MagicMock()
            mock_bedrock.return_value = mock_model

            mock_agent_instance = MagicMock()
            mock_agent_instance.invoke_async = mock_invoke_async
            mock_agent_cls.return_value = mock_agent_instance

            # Mock httpx.get to return our large payload
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = payload
            mock_httpx.get.return_value = mock_response
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.RequestError = httpx.RequestError

            extractor = ExtractorAgent(settings)

            # Run the extraction - on UNFIXED code this will raise
            # MaxTokensReachedException because the full payload flows
            # through the agent context as a tool argument
            try:
                import asyncio
                result = asyncio.get_event_loop().run_until_complete(
                    extractor.extract(
                        url="https://example.com/large-page.model.json",
                        region="US",
                        brand="test-brand",
                    )
                )
            except MaxTokensReachedException:
                # On UNFIXED code, this exception IS raised - test fails
                # because the expected behavior is NO exception
                pytest.fail(
                    f"MaxTokensReachedException raised for payload of "
                    f"{payload_bytes} bytes (~{estimated_tokens} tokens). "
                    f"Bug condition confirmed: filter_by_component_type is "
                    f"invoked as LLM tool and full unfiltered JSON "
                    f"({payload_bytes} bytes) flows through agent context."
                )

        # Post-fix assertions (expected behavior):
        # 1. filter_by_component_type_direct should be called as direct Python function
        assert filter_called_directly, (
            "filter_by_component_type was NOT called as a direct Python function. "
            "On unfixed code, it is registered as an LLM tool instead."
        )

        # 2. Agent context should contain only filtered data (not full payload)
        if filter_call_args:
            assert filter_call_args["payload_bytes"] == payload_bytes, (
                "filter_by_component_type_direct should receive the full payload "
                "for filtering, but the agent should only see filtered results."
            )
