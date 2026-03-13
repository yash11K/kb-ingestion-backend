# Implementation Plan: Extractor Agent Refactor

## Overview

Refactor the Extractor Agent from a tool-delegating Strands agent into an LLM-first content processor. The implementation proceeds bottom-up: new data models and config first, then tool deprecation, then the core agent rewrite (prompt, response parsing, batching, SSE), then the PostProcessor, and finally integration wiring. Property-based tests use `hypothesis`.

## Tasks

- [x] 1. Define ExtractionResult model and extend Settings
  - [x] 1.1 Add `ExtractionResult` Pydantic model to `src/models/schemas.py`
    - Add class with required fields: `title`, `content_type`, `markdown_body`, `source_nodes` (list[str]), `component_type`, `source_url`, `parent_context`, `grouping_rationale`
    - Add `@field_validator("markdown_body")` that raises `ValueError` when the value is empty or whitespace-only
    - _Requirements: 6.1, 6.3_

  - [ ]* 1.2 Write property test for ExtractionResult validation (Property 10)
    - **Property 10: ExtractionResult model validation**
    - Test that any dict with all required fields and non-empty `markdown_body` constructs successfully, and any dict missing a required field or with empty/whitespace `markdown_body` raises `ValidationError`
    - Create test file `tests/test_agents/test_extraction_result.py`
    - **Validates: Requirements 6.1, 6.3**

  - [x] 1.3 Add `batch_threshold` field to `Settings` in `src/config.py`
    - Add `batch_threshold: int = 8` field
    - Add `@field_validator("batch_threshold")` that clamps values < 1 to 1
    - Env var: `BATCH_THRESHOLD`
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 1.4 Write property test for batch threshold clamping (Property 11)
    - **Property 11: Batch threshold clamping**
    - Test that any integer value < 1 is clamped to 1 by the Settings validator
    - Add to `tests/test_config.py`
    - **Validates: Requirements 7.3**

- [x] 2. Deprecate tool modules
  - [x] 2.1 Remove `@tool` decorator from `html_to_markdown` in `src/tools/html_converter.py`
    - Remove the `from strands.tools import tool` import and `@tool` decorator
    - Retain the function as a plain Python utility
    - _Requirements: 9.1_

  - [x] 2.2 Remove `@tool` decorator from `generate_md_file` in `src/tools/md_generator.py`
    - Remove the `from strands.tools import tool` import and `@tool` decorator
    - Retain `_slugify` and `compute_content_hash` as importable plain Python utilities
    - _Requirements: 9.2_

- [x] 3. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Rewrite ExtractorAgent core
  - [x] 4.1 Replace system prompt and remove tool registrations in `src/agents/extractor.py`
    - Replace `EXTRACTOR_SYSTEM_PROMPT` with new prompt instructing the LLM to: convert HTML to markdown, infer title/content_type, decide grouping/splitting, return JSON array of ExtractionResult objects
    - Change `self._tools = [html_to_markdown, generate_md_file]` to `self._tools = []`
    - Remove imports of `html_to_markdown` and `generate_md_file`
    - Add import of `ExtractionResult` from `src/models/schemas.py`
    - _Requirements: 1.2, 1.4, 2.1, 9.3_

  - [x] 4.2 Implement `_build_prompt` method on `ExtractorAgent`
    - Serialize ContentNodes to JSON and build the user prompt with url, region, brand context
    - The prompt must contain raw ContentNode JSON, no tool call references
    - _Requirements: 1.1_

  - [ ]* 4.3 Write property test for prompt construction (Property 1)
    - **Property 1: Prompt contains raw ContentNode JSON**
    - Generate random ContentNode lists, verify prompt contains their JSON serialization and does not reference `html_to_markdown` or `generate_md_file`
    - Create test in `tests/test_agents/test_extractor_prompt.py`
    - **Validates: Requirements 1.1**

  - [x] 4.4 Implement `_parse_response` static method on `ExtractorAgent`
    - Extract JSON array from response text by locating outermost `[` and `]`
    - Handle preamble/postamble text around the JSON array
    - Validate each element against `ExtractionResult` Pydantic model
    - Skip invalid elements with a logged warning
    - Return empty list on invalid JSON, logging the error
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 4.5 Write property tests for response parsing (Properties 2, 3, 4)
    - **Property 2: Response parsing round-trip with preamble/postamble**
    - Generate valid ExtractionResult dicts, serialize to JSON, wrap with random preamble/postamble (no `[` or `]`), verify round-trip parsing recovers originals
    - **Property 3: Invalid JSON returns empty list**
    - Generate random strings without valid JSON arrays, verify empty list returned without exception
    - **Property 4: Partial validity — skip invalid elements**
    - Generate mixed arrays of valid/invalid dicts, verify only valid ones returned
    - Create test in `tests/test_agents/test_extractor_parsing.py`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

- [x] 5. Implement batching logic
  - [x] 5.1 Implement batch splitting in `ExtractorAgent`
    - Split nodes into batches of `batch_threshold` size when `len(nodes) > batch_threshold`
    - Process all nodes in a single invocation when `len(nodes) <= batch_threshold`
    - Process batches sequentially, concatenate results
    - On batch failure: log error with batch index and node count, skip failed batch, continue remaining
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.6_

  - [ ]* 5.2 Write property test for batch splitting (Property 7)
    - **Property 7: Batch splitting correctness**
    - Generate random node lists and thresholds ≥ 1, verify correct number of batches, correct sizes, and concatenation equals original list
    - Create test in `tests/test_agents/test_extractor_batching.py`
    - **Validates: Requirements 4.1, 4.3, 4.6**

  - [ ]* 5.3 Write property test for failed batch resilience (Property 8)
    - **Property 8: Failed batch does not prevent other batches**
    - Mock batch invocations with random failures, verify results from successful batches are present
    - Add to `tests/test_agents/test_extractor_batching.py`
    - **Validates: Requirements 4.4**

- [x] 6. Implement SSE streaming for batch progress
  - [x] 6.1 Add SSE event publishing for batch lifecycle in `ExtractorAgent`
    - Publish `extraction_batching` event at start with total_batches and total_nodes
    - Publish `extraction_batch_start` per batch with batch_index (1-based), total_batches, node_count
    - Publish `extraction_batch_complete` per batch with batch_index, total_batches, result_count
    - Publish `extraction_batch_error` on batch failure with batch_index and error message
    - Publish `extraction_complete` at end with total result count
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 6.2 Write property test for SSE event counts (Property 9)
    - **Property 9: SSE events match batch count**
    - Mock StreamManager, run batched extraction with N batches, verify exactly N start events, N complete/error events, 1 batching event, 1 complete event
    - Add to `tests/test_agents/test_extractor_batching.py`
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.5**

- [x] 7. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement PostProcessor
  - [x] 8.1 Create `PostProcessor` class in `src/agents/extractor.py`
    - Implement `process(results, url, region, brand) -> list[MarkdownFile]`
    - For each ExtractionResult: compute SHA-256 hash of `markdown_body`, slugify title to filename, assemble YAML frontmatter with all required metadata fields, join `source_nodes` into comma-separated `aem_node_id`, build `MarkdownFile`
    - Import `_slugify` and `compute_content_hash` from `src/tools/md_generator.py` as plain Python functions
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 9.4_

  - [ ]* 8.2 Write property test for PostProcessor field correctness (Property 5)
    - **Property 5: PostProcessor produces correct MarkdownFile fields**
    - Generate random ExtractionResults, run through PostProcessor, verify `content_hash == SHA-256(markdown_body)`, `filename == _slugify(title) + ".md"`, `md_body == markdown_body`, `aem_node_id == ",".join(source_nodes)`, and `md_content` contains YAML frontmatter with all required fields
    - Create test in `tests/test_agents/test_post_processor.py`
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.6, 8.2**

  - [ ]* 8.3 Write property test for PostProcessor count invariant (Property 6)
    - **Property 6: PostProcessor count invariant**
    - Generate random-length ExtractionResult lists, verify output count matches input count
    - Add to `tests/test_agents/test_post_processor.py`
    - **Validates: Requirements 2.5**

- [x] 9. Wire extract() method end-to-end
  - [x] 9.1 Rewrite `extract()` method in `ExtractorAgent`
    - Wire together: fetch AEM JSON → filter → batch → invoke agent per batch → parse responses → concatenate ExtractionResults → PostProcessor.process → return list[MarkdownFile]
    - Maintain existing method signature: `async def extract(self, url, region, brand, job_id, stream_manager) -> list[MarkdownFile]`
    - Remove old `_parse_result`, `_extract_files_from_messages`, `_extract_files_from_tool_results`, `_parse_json_from_text` helper functions
    - _Requirements: 1.1, 1.3, 1.5, 8.1, 8.3_

  - [ ]* 9.2 Write unit tests for end-to-end extract flow
    - Mock Bedrock agent invocation, verify full extract() → MarkdownFile flow
    - Test edge cases: single node, empty node list, batch boundary (exactly threshold, threshold + 1)
    - Verify zero tools registration, system prompt content
    - Add to `tests/test_agents/test_extractor_integration.py`
    - _Requirements: 1.4, 2.1, 4.5, 8.1, 8.3, 8.4_

- [x] 10. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using `hypothesis`
- Unit tests validate specific examples and edge cases
- The `PostProcessor` imports `_slugify` and `compute_content_hash` from the deprecated tool modules as plain Python utilities
