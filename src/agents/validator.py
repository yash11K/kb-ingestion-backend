"""Validator Agent definition.

Uses Haiku to score markdown files on metadata completeness, semantic quality,
and uniqueness. Uniqueness is assessed by querying the Bedrock KB vector store
for semantically similar documents and having the agent reason about conceptual
overlap.

Scoring: each dimension is 0–10, total is 0–30.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import Settings
from src.models.schemas import MarkdownFile, ValidationBreakdown, ValidationResult
from src.tools.uniqueness_checker import check_uniqueness, set_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from src.services.stream_manager import StreamManager

logger = logging.getLogger(__name__)

VALIDATOR_SYSTEM_PROMPT = """\
You are a content validation agent. Your job is to score a Markdown file \
on three dimensions: metadata completeness, semantic quality, and uniqueness. \
You must also classify the document type and ALWAYS provide an assessment comment.

ALL SCORES USE A 0–10 SCALE. The total score is the sum of all three (0–30).

Follow these steps exactly:

1. Read the YAML frontmatter directly from the markdown content. Check which \
required fields are present and non-empty. Required fields: title, content_type, \
source_url, component_type, key, namespace, region, brand.

2. Score metadata_completeness from 0 to 10:
   - 10 if ALL required frontmatter fields are present and non-empty.
   - Deduct proportionally for each missing or empty field. With 8 required \
fields, each field is worth 1.25 points.

3. Score semantic_quality from 0 to 10:
   - 9-10 for well-structured, coherent, readable content with clear meaning \
and sufficient depth.
   - 6-8 for adequate content with minor issues or moderate depth.
   - 3-5 for poor quality, incoherent, very short, or thin content \
(e.g. just a title and one line, banner text, placeholder content).
   - 0-2 for empty or meaningless content.
   Be strict: a file with only a heading and a single short sentence is NOT \
high quality content. It should score 4 or below for semantic quality.

4. Use the check_uniqueness tool with the document's markdown body content \
and source_url to query the knowledge base for semantically similar documents.
   Then reason about the results:
   - This is NOT a word-matching exercise. Evaluate SEMANTIC and CONCEPTUAL \
overlap. Two documents about the same topic using different words are still \
duplicates in meaning.
   - Score uniqueness from 0 to 10:
     - 9-10: Completely novel content — no meaningful conceptual overlap with \
existing KB documents.
     - 6-8: Mostly unique — minor topical overlap but adds substantial new \
information or perspective.
     - 3-5: Moderate overlap — covers similar concepts as existing documents \
but has some unique aspects.
     - 1-2: High overlap — most concepts are already covered in existing KB \
documents with little new information.
     - 0: Semantic duplicate — the same information/concepts exist in the KB \
already, even if worded differently.
   - If the tool reports KB is not available, score uniqueness as 10 (benefit \
of the doubt) and note this in the uniqueness_insight.

5. Provide a "uniqueness_insight" string: 1-3 sentences explaining your \
uniqueness reasoning. What overlap did you find? What's novel? Examples:
   - "No similar documents found in the KB. This content about seasonal \
discount policies appears to be entirely new."
   - "Found 3 documents covering rental add-on pricing. This document \
overlaps significantly on base pricing but adds unique seasonal discount \
information not found elsewhere."
   - "KB not available for comparison. Uniqueness cannot be assessed."

6. Compute the total score as: metadata_completeness + semantic_quality + uniqueness.

7. ALWAYS provide an assessment in the "issues" list. This is MANDATORY even \
for high-scoring files. The first entry must be a brief overall assessment \
of the content (1-2 sentences describing what the content is and your quality \
judgment). Additional entries should describe specific problems found.

8. Classify the document type based on the actual content semantics:
   - "TnC" for terms and conditions, legal agreements, policies
   - "FAQ" for frequently asked questions, Q&A content
   - "ProductGuide" for product descriptions, feature guides, how-to guides
   - "Support" for troubleshooting, help articles, support documentation
   - "Marketing" for promotional content, campaigns, offers
   - "General" for content that doesn't fit the above categories

9. Return your result as a single JSON object:
{
  "score": <total_score>,
  "breakdown": {
    "metadata_completeness": <float 0-10>,
    "semantic_quality": <float 0-10>,
    "uniqueness": <float 0-10>
  },
  "issues": ["<overall assessment — ALWAYS required>", "<specific issue 1>", ...],
  "uniqueness_insight": "<your reasoning about uniqueness>",
  "doc_type": "<one of: TnC, FAQ, ProductGuide, Support, Marketing, General>"
}

Return ONLY the JSON object, with no additional text or explanation.
"""

# Uniqueness-only prompt for independent uniqueness revalidation
UNIQUENESS_ONLY_PROMPT = """\
You are a content uniqueness assessment agent. Your ONLY job is to evaluate \
how unique a document is compared to existing knowledge base content.

You will be given a markdown document. Use the check_uniqueness tool with the \
document's markdown body and source_url to query the knowledge base.

Then reason about SEMANTIC and CONCEPTUAL overlap — not word matching. Two \
documents about the same topic using different words are still duplicates in \
meaning.

Score uniqueness from 0 to 10:
- 9-10: Completely novel content — no meaningful conceptual overlap.
- 6-8: Mostly unique — minor topical overlap but substantial new information.
- 3-5: Moderate overlap — similar concepts but some unique aspects.
- 1-2: High overlap — most concepts already covered with little new info.
- 0: Semantic duplicate — same information exists in KB already.

If the tool reports KB is not available, score uniqueness as 10 and note this.

Return a JSON object:
{
  "uniqueness": <float 0-10>,
  "uniqueness_insight": "<1-3 sentences explaining your reasoning>"
}

Return ONLY the JSON object, with no additional text.
"""


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value to the given range."""
    return max(min_val, min(max_val, value))


class ValidatorAgent:
    """Wraps the Strands Agent with validation tools (Haiku-based)."""

    def __init__(self, settings: Settings, session_factory: "async_sessionmaker[AsyncSession]") -> None:
        self._model_kwargs = dict(
            model_id=settings.haiku_model_id,
            region_name=settings.aws_region,
            max_tokens=4096,
        )
        self._tools = [check_uniqueness]
        self.settings = settings
        set_settings(settings)

    def _make_callback(
        self,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
        agent_label: str = "validator",
    ):
        """Create a callback handler for agent invocations."""
        last_log: dict[str, str | None] = {"msg": None}

        def _callback(**kwargs: Any) -> None:
            if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
                tool_name = kwargs["current_tool_use"]["name"]
                log_msg = f"{agent_label} tool call: {tool_name}"
                if log_msg != last_log["msg"]:
                    logger.info("%s", log_msg)
                    last_log["msg"] = log_msg
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "tool_call", {
                        "agent": agent_label,
                        "tool": tool_name,
                        "message": f"{agent_label} agent calling tool: {tool_name}",
                    })
            elif "data" in kwargs:
                if stream_manager and job_id:
                    stream_manager.publish(job_id, "agent_log", {
                        "agent": agent_label,
                        "chunk": kwargs["data"],
                    })
            elif "result" in kwargs:
                logger.info("%s agent completed response", agent_label)
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
                    logger.info("%s agent thinking: %.500s", agent_label, text)

        return _callback

    async def validate(
        self,
        md_file: MarkdownFile,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> ValidationResult:
        """Score a markdown file on metadata, semantics, and uniqueness (0–30)."""
        prompt = (
            f"Validate the following markdown file.\n\n"
            f"Source URL: {md_file.source_url}\n\n"
            f"Full markdown content:\n"
            f"---START---\n{md_file.md_content}\n---END---\n\n"
            f"Read the frontmatter directly, evaluate semantic quality of the body, "
            f"check uniqueness against the knowledge base using the check_uniqueness "
            f"tool with the markdown body content and source_url, and return the "
            f"validation scores as a JSON object."
        )

        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            callback_handler=self._make_callback(job_id, stream_manager, "validator"),
        )
        result = await agent.invoke_async(prompt)
        return self._parse_result(result)

    async def assess_uniqueness(
        self,
        md_file: MarkdownFile,
        job_id: UUID | None = None,
        stream_manager: "StreamManager | None" = None,
    ) -> dict:
        """Run uniqueness assessment only. Returns uniqueness score and insight.

        Used for independent uniqueness revalidation without re-running the
        full validation pipeline.
        """
        prompt = (
            f"Assess the uniqueness of the following markdown document.\n\n"
            f"Source URL: {md_file.source_url}\n\n"
            f"Markdown body:\n"
            f"---START---\n{md_file.md_body}\n---END---\n\n"
            f"Use the check_uniqueness tool with the markdown body and source_url, "
            f"then return your uniqueness assessment as JSON."
        )

        agent = Agent(
            model=BedrockModel(**self._model_kwargs),
            tools=self._tools,
            system_prompt=UNIQUENESS_ONLY_PROMPT,
            callback_handler=self._make_callback(job_id, stream_manager, "uniqueness"),
        )
        result = await agent.invoke_async(prompt)
        return self._parse_uniqueness_result(result)

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
            float(breakdown_data.get("metadata_completeness", 0.0)), 0.0, 10.0
        )
        semantic_quality = _clamp(
            float(breakdown_data.get("semantic_quality", 0.0)), 0.0, 10.0
        )
        uniqueness = _clamp(
            float(breakdown_data.get("uniqueness", 0.0)), 0.0, 10.0
        )

        score = round(metadata_completeness + semantic_quality + uniqueness, 2)
        score = _clamp(score, 0.0, 30.0)

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = []
        issues = [str(i) for i in issues]
        if not issues:
            issues = ["No assessment provided by validator agent."]

        uniqueness_insight = str(data.get("uniqueness_insight", ""))

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
            uniqueness_insight=uniqueness_insight,
            doc_type=doc_type,
        )

    @staticmethod
    def _parse_uniqueness_result(result: Any) -> dict:
        """Parse uniqueness-only agent response."""
        text = str(result)
        data = _extract_json_object(text)

        if data is None:
            logger.warning("Could not parse uniqueness result from agent response")
            return {"uniqueness": 0.0, "uniqueness_insight": "Failed to parse agent response."}

        uniqueness = _clamp(float(data.get("uniqueness", 0.0)), 0.0, 10.0)
        insight = str(data.get("uniqueness_insight", ""))

        return {"uniqueness": uniqueness, "uniqueness_insight": insight}


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
