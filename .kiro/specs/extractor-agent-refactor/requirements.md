# Requirements Document

## Introduction

Refactor the Extractor Agent in the AEM Knowledge Base Ingestion System to shift intelligent content processing from deterministic tool calls into the Bedrock LLM itself. The current agent uses two Strands tools (`html_to_markdown` and `generate_md_file`) in a rigid 1:1 node-to-file mapping. The refactored agent receives pre-filtered AEM content nodes as raw JSON, performs all content understanding (HTML-to-markdown conversion, intelligent file splitting, metadata inference) within the LLM, and returns structured JSON output. Python code handles the remaining deterministic post-processing (content hashing, filename slugification, frontmatter assembly). Large node sets are batched across sequential agent calls to stay within Bedrock token limits.

## Glossary

- **Extractor_Agent**: The Strands Agent backed by Amazon Bedrock that receives filtered AEM content nodes and produces structured extraction results as JSON.
- **Pipeline_Service**: The orchestration service (`PipelineService`) that coordinates the extract → validate → route → upload flow.
- **Content_Node**: A Pydantic model (`ContentNode`) representing a single filtered AEM component with its node type, HTML content, path, and metadata.
- **Extraction_Result**: A new Pydantic model representing a single file output from the Extractor_Agent, containing title, content_type, markdown body, source metadata, and grouping rationale.
- **Markdown_File**: The existing Pydantic model (`MarkdownFile`) representing a fully assembled markdown file with frontmatter, content hash, and filename.
- **Batch**: A subset of Content_Nodes sent to the Extractor_Agent in a single invocation to stay within Bedrock token limits.
- **Post_Processor**: Python-side logic that converts Extraction_Results into Markdown_Files by performing deterministic operations (hashing, slugification, frontmatter assembly).
- **Stream_Manager**: The SSE event broadcasting service that delivers real-time pipeline progress to connected clients.
- **Batch_Threshold**: A configurable integer (default 8) defining the minimum number of Content_Nodes that triggers batched agent invocations.
- **Bedrock_Max_Tokens**: The existing configurable maximum output token limit per Bedrock agent invocation.

## Requirements

### Requirement 1: Agent Prompt Redesign

**User Story:** As a developer, I want the Extractor_Agent to receive raw filtered Content_Nodes and perform all content understanding within the LLM, so that the agent can make intelligent decisions about file splitting and metadata enrichment without being constrained by rigid tool-based 1:1 mapping.

#### Acceptance Criteria

1. WHEN the Pipeline_Service invokes the Extractor_Agent, THE Extractor_Agent SHALL receive the serialized Content_Node data as raw JSON in the prompt without any intermediate tool-based transformation.
2. THE Extractor_Agent system prompt SHALL instruct the LLM to convert HTML content to clean markdown, infer metadata fields (title, content_type), decide how to group or split nodes into logical files, and return structured JSON output.
3. WHEN the Extractor_Agent processes Content_Nodes, THE Extractor_Agent SHALL return a JSON array of Extraction_Result objects, where each object contains: `title`, `content_type`, `markdown_body`, `source_nodes` (list of aem_node_ids that contributed), `component_type`, `source_url`, `parent_context`, and `grouping_rationale`.
4. THE Extractor_Agent SHALL operate with zero Strands tool registrations, relying solely on LLM reasoning for content processing.
5. WHEN the Extractor_Agent groups multiple Content_Nodes into a single file, THE Extractor_Agent SHALL include all contributing `aem_node_id` values in the `source_nodes` field of the Extraction_Result.

### Requirement 2: Remove Deterministic Tools from Agent

**User Story:** As a developer, I want to remove `html_to_markdown` and `generate_md_file` as Strands agent tools, so that the agent's tool surface is eliminated and all deterministic post-processing happens in Python.

#### Acceptance Criteria

1. THE Extractor_Agent SHALL be instantiated with an empty tools list (no Strands `@tool` registrations).
2. THE Post_Processor SHALL compute the SHA-256 content hash from the `markdown_body` field of each Extraction_Result.
3. THE Post_Processor SHALL generate filenames by slugifying the `title` field of each Extraction_Result and appending `.md`.
4. THE Post_Processor SHALL assemble YAML frontmatter containing all required metadata fields (title, content_type, source_url, component_type, aem_node_id, modify_date, extracted_at, parent_context, region, brand) and combine it with the markdown body to produce the full `md_content`.
5. THE Post_Processor SHALL produce a Markdown_File object for each Extraction_Result, preserving compatibility with the existing validator pipeline.
6. WHEN multiple source nodes contribute to a single Extraction_Result, THE Post_Processor SHALL join the `source_nodes` list into a comma-separated string for the `aem_node_id` field of the Markdown_File.

### Requirement 3: Structured JSON Response Parsing

**User Story:** As a developer, I want robust parsing of the agent's JSON response, so that extraction results are reliably converted into typed Pydantic models even when the LLM output contains minor formatting variations.

#### Acceptance Criteria

1. WHEN the Extractor_Agent returns a response, THE Extractor_Agent SHALL parse the response text to extract a JSON array of Extraction_Result objects.
2. IF the Extractor_Agent response contains text outside the JSON array (preamble or postamble), THEN THE Extractor_Agent SHALL extract the JSON array by locating the outermost `[` and `]` delimiters.
3. IF the Extractor_Agent response contains invalid JSON, THEN THE Extractor_Agent SHALL log the parse error with the raw response text and return an empty list.
4. WHEN parsing succeeds, THE Extractor_Agent SHALL validate each element against the Extraction_Result Pydantic model and skip elements that fail validation, logging a warning for each skipped element.

### Requirement 4: Batched Agent Invocations

**User Story:** As a developer, I want large sets of Content_Nodes to be split into batches for sequential agent calls, so that each invocation stays within Bedrock's context and token limits.

#### Acceptance Criteria

1. WHEN the number of filtered Content_Nodes exceeds the Batch_Threshold, THE Extractor_Agent SHALL split the nodes into batches of size equal to the Batch_Threshold.
2. THE Extractor_Agent SHALL process each Batch sequentially (one agent invocation at a time) to respect Bedrock API throttling limits.
3. WHEN all batches complete, THE Extractor_Agent SHALL concatenate the Extraction_Result lists from all batch invocations into a single result list.
4. IF a single Batch invocation fails, THEN THE Extractor_Agent SHALL log the error with the batch index and node count, skip the failed batch, and continue processing remaining batches.
5. THE Batch_Threshold SHALL be configurable via the Settings class with a default value of 8.
6. WHEN the number of filtered Content_Nodes is equal to or below the Batch_Threshold, THE Extractor_Agent SHALL process all nodes in a single agent invocation without batching.

### Requirement 5: SSE Streaming for Batch Progress

**User Story:** As a frontend developer, I want SSE events to reflect batch-level progress during extraction, so that the UI can display meaningful progress indicators for large ingestion jobs.

#### Acceptance Criteria

1. WHEN batched extraction begins, THE Extractor_Agent SHALL publish an SSE event with stage `extraction_batching` containing the total number of batches and total node count.
2. WHEN each Batch invocation starts, THE Extractor_Agent SHALL publish an SSE event with stage `extraction_batch_start` containing the current batch index (1-based), total batches, and the number of nodes in the current batch.
3. WHEN each Batch invocation completes, THE Extractor_Agent SHALL publish an SSE event with stage `extraction_batch_complete` containing the current batch index, total batches, and the number of Extraction_Results produced by that batch.
4. WHEN a Batch invocation fails, THE Extractor_Agent SHALL publish an SSE event with stage `extraction_batch_error` containing the batch index and error message.
5. WHEN all batches complete (or a single non-batched invocation completes), THE Extractor_Agent SHALL publish an SSE event with stage `extraction_complete` containing the total number of Extraction_Results produced.

### Requirement 6: Extraction_Result Pydantic Model

**User Story:** As a developer, I want a typed Pydantic model for the agent's structured output, so that extraction results are validated and documented at the schema level.

#### Acceptance Criteria

1. THE Extraction_Result model SHALL contain the following required fields: `title` (str), `content_type` (str), `markdown_body` (str), `source_nodes` (list of str), `component_type` (str), `source_url` (str), `parent_context` (str), and `grouping_rationale` (str).
2. THE Extraction_Result model SHALL be defined in `src/models/schemas.py` alongside the existing models.
3. WHEN the `markdown_body` field is empty, THE Extraction_Result model SHALL raise a validation error.

### Requirement 7: Configuration for Batch Threshold

**User Story:** As an operator, I want the batch threshold to be configurable via environment variables, so that I can tune batching behavior based on the Bedrock model's context window size without code changes.

#### Acceptance Criteria

1. THE Settings class SHALL include a `batch_threshold` field of type `int` with a default value of 8.
2. THE `batch_threshold` field SHALL be loadable from the `BATCH_THRESHOLD` environment variable.
3. WHEN `batch_threshold` is set to a value less than 1, THE Settings class SHALL treat the value as 1 (minimum one node per batch).

### Requirement 8: Pipeline Integration Compatibility

**User Story:** As a developer, I want the refactored Extractor_Agent to produce output compatible with the existing Pipeline_Service and Validator_Agent, so that downstream processing remains unchanged.

#### Acceptance Criteria

1. THE Extractor_Agent `extract()` method SHALL continue to return `list[MarkdownFile]`, maintaining the existing method signature.
2. WHEN the Post_Processor produces Markdown_Files, each Markdown_File SHALL contain all fields required by the Validator_Agent: `filename`, `title`, `content_type`, `source_url`, `component_type`, `aem_node_id`, `md_content`, `md_body`, `content_hash`, `modify_date`, `extracted_at`, `parent_context`, `region`, `brand`.
3. THE Pipeline_Service SHALL invoke the refactored Extractor_Agent using the same `await self.extractor.extract(url, region, brand, job_id, sm)` call pattern without modification.
4. THE Validator_Agent, score routing, and S3 upload stages of the Pipeline_Service SHALL remain unchanged.

### Requirement 9: Deprecate Tool Modules

**User Story:** As a developer, I want the `html_to_markdown` and `generate_md_file` Strands tool functions to be clearly deprecated or removed, so that the codebase reflects the new architecture and avoids confusion.

#### Acceptance Criteria

1. THE `html_to_markdown` Strands `@tool` decorator SHALL be removed from `src/tools/html_converter.py`, retaining the underlying conversion function as a plain Python utility if needed by other modules.
2. THE `generate_md_file` Strands `@tool` decorator SHALL be removed from `src/tools/md_generator.py`, with its deterministic logic (hashing, slugification, frontmatter assembly) relocated to the Post_Processor.
3. THE Extractor_Agent module SHALL remove all imports of `html_to_markdown` and `generate_md_file`.
4. WHEN the Post_Processor reuses utility functions from the deprecated tool modules (e.g., `_slugify`, `compute_content_hash`), THE Post_Processor SHALL import them as plain Python functions.