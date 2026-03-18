"""Validator Agent definition.

Uses Haiku to score markdown files on metadata completeness, semantic quality,
and uniqueness. Haiku reads YAML frontmatter natively — no parse_frontmatter
tool needed. Only check_duplicate remains as a tool.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import Settings
from src.models.schemas import MarkdownFile, ValidationBreakdown, ValidationResult
from src.tools.duplicate_checker import check_duplicate, set_db_pool

if TYPE_CHECKING:
    from src.services.stream_manager import StreamManager

logger = logging.getLogger(__name__)

VALIDATOR_SYSTEM_PROMPT = """\
You are a content validation agent. Your job is to score a Markdown file \
on three dimensions: metadata completeness, semantic quality, and uniqueness. \
You must also classify the document type based on its content.

Follow these steps exactly:

1. Read the YAML frontmatter directly from the markdown content. Check which \
required fields are present and non-empty. Required fields: title, content_type, \
source_url, component_type, key, namespace, region, brand.

2. Score metadata_completeness from 0.0 to 0.3:
   - 0.3 if ALL required frontmatter fields are present and non-empty.
   - Deduct proportionally for each missing or empty field. With 8 required \
fields, each field is worth 0.0375.

3. Score semantic_quality from 0.0 to 0.5:
   - 0.5 for well-structured, coherent, readable content with clear meaning.
   - 0.3-0.4 for adequate content with minor issues.
   - 0.1-0.2 for poor quality, incoherent, or very short content.
   - 0.0 for empty or meaningless content.

4. Use the check_duplicate tool with the content_hash to check for duplicates.
   - Score uniqueness as 0.2 if the content is NOT a duplicate.
   - Score uniqueness as 0.0 if the content IS a duplicate.

5. Compute the total score as: metadata_completeness + semantic_quality + uniqueness.

6. Collect any issues found into a list of short descriptive strings.

7. Classify the document type based on the actual content semantics:
   - "TnC" for terms and conditions, legal agreements, policies
   - "FAQ" for frequently asked questions, Q&A content
   - "ProductGuide" for product descriptions, feature guides, how-to guides
   - "Support" for troubleshooting, help articles, support documentation
   - "Marketing" for promotional content, campaigns, offers
   - "General" for content that doesn't fit the above categories

8. Return your result as a single JSON object:
{
  "score": <total_score>,
  "breakdown": {
    "metadata_completeness": <float 0.0-0.3>,
    "semantic_quality": <float 0.0-0.5>,
    "uniqueness": <float 0.0-0.2>
  },
  "issues": ["issue1", "issue2", ...],
  "doc_type": "<one of: TnC, FAQ, ProductGuide, Support, Marketing, General>"
}

Return ONLY the JSON object, with no additional text or explanation.
"""


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value to the given range."""
    return max(min_val, min(max_val, value))


class ValidatorAgent:
    """Wraps the Strands Agent with validation tools (Haiku-based)."""

    def __init__(self, settings: Settings, db_pool: asyncpg.Pool) -> None:
        self._model_kwargs = dict(
            model_id=settings.haiku_model_id,
            region_name=settings.aws_region,
            max_tokens=4096,
        )
        self._tools = [check_duplicate]
        self.settings = settings
        set_db_pool(db_pool)

    async def validate(
        self,
        md_file: MarkdownFile,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> ValidationResult:
        """Score a markdown file on metadata, semantics, and uniqueness."""
        prompt = (
            f"Validate the following markdown file.\n\n"
            f"Content hash: {md_file.content_hash}\n\n"
            f"Full markdown content:\n"
            f"---START---\n{md_file.md_content}\n---END---\n\n"
            f"Read the frontmatter directly, evaluate semantic quality of the body, "
            f"check for duplicates using the content hash, and return the "
            f"validation scores as a JSON object."
        )

        last_log: dict[str, str | None] = {"msg": None}

        def _validator_callback(**kwargs: Any) -> None:
            if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
                tool_name = kwargs["current_tool_use"]["name"]
                log_msg = f"Validator tool call: {tool_name}"
                if log_msg != last_log["msg"]:
                    logger.info("%s", log_msg)
                    last_log["msg"] = log_msg
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "tool_call", {
                        "agent": "validator",
                        "tool": tool_name,
                        "message": f"Validator agent calling tool: {tool_name}",
                    })
            elif "data" in kwargs:
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "agent_log", {
                        "agent": "validator",
                        "chunk": kwargs["data"],
                    })
            elif "result" in kwargs:
                logger.info("Validator agent completed response")
            elif "message" in kwargs and kwargs["message"].get("role") == "assistant":
                content = kwargs["message"].get("content", "")
                text = ""
                if isinstance(content, list):
                    text = " ".join(
                        block.get("text", "") for block in content if isinstance(block, dict) and "text" in block
                    )
                elif isinstance(content, str):
                    text = content
                if text:
                    logger.info("Validator agent thinking: %.500s", text)

        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            callback_handler=_validator_callback,
        )
        result = await agent.invoke_async(prompt)
        return self._parse_result(result)

    @staticmethod
    def _parse_result(result: Any) -> ValidationResult:
        """Parse the agent response into a ValidationResult."""
        text = str(result)
        data = _extract_json_object(text)

        if data is None:
            logger.warning(
                "Could not parse ValidationResult from agent response, "
                "returning default low score"
            )
            return ValidationResult(
                score=0.0,
                breakdown=ValidationBreakdown(
                    metadata_completeness=0.0,
                    semantic_quality=0.0,
                    uniqueness=0.0,
                ),
                issues=["Failed to parse validation agent response"],
            )

        breakdown_data = data.get("breakdown", {})
        metadata_completeness = _clamp(
            float(breakdown_data.get("metadata_completeness", 0.0)), 0.0, 0.3
        )
        semantic_quality = _clamp(
            float(breakdown_data.get("semantic_quality", 0.0)), 0.0, 0.5
        )
        uniqueness = _clamp(
            float(breakdown_data.get("uniqueness", 0.0)), 0.0, 0.2
        )

        score = round(metadata_completeness + semantic_quality + uniqueness, 10)
        score = _clamp(score, 0.0, 1.0)

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = []
        issues = [str(i) for i in issues]

        valid_doc_types = {"TnC", "FAQ", "ProductGuide", "Support", "Marketing", "General"}
        doc_type = data.get("doc_type", "General")
        if doc_type not in valid_doc_types:
            doc_type = "General"

        breakdown = ValidationBreakdown(
            metadata_completeness=metadata_completeness,
            semantic_quality=semantic_quality,
            uniqueness=uniqueness,
        )

        return ValidationResult(
            score=score,
            breakdown=breakdown,
            issues=issues,
            doc_type=doc_type,
        )


def _extract_json_object(text: str) -> dict | None:
    """Extract a JSON object from text, trying multiple strategies."""
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = text[start : end + 1]
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None
