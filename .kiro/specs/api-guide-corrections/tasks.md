# Implementation Plan: API Guide Corrections

## Overview

Incremental implementation of seven corrections and gap-fills to the AEM Knowledge Base Ingestion System. Each task builds on the previous, starting with schema/model changes, then route handlers and query functions, then documentation, and finally wiring verification. Property-based tests use Hypothesis.

## Tasks

- [ ] 1. Add new schema models and enums
  - [ ] 1.1 Add `component_types` field to `IngestRequest` in `src/models/schemas.py`
    - Add `component_types: list[str] | None = None` to the `IngestRequest` model
    - _Requirements: 1.3_

  - [ ] 1.2 Add `review_notes` field to `AcceptRequest` in `src/models/schemas.py`
    - Add `review_notes: str | None = None` to the `AcceptRequest` model
    - _Requirements: 2.3_

  - [ ] 1.3 Add `reviewed_by` field to `UpdateRequest` in `src/models/schemas.py`
    - Add `reviewed_by: str` as a required field to the `UpdateRequest` model
    - _Requirements: 3.2_

  - [ ] 1.4 Add `SortByField` and `SortOrder` enums in `src/models/schemas.py`
    - Create `SortByField(str, Enum)` with values `created_at`, `validation_score`, `title`
    - Create `SortOrder(str, Enum)` with values `asc`, `desc`
    - _Requirements: 4.1, 4.2, 4.4, 4.5_

  - [ ] 1.5 Add `status` field to `QueueItemDetail` in `src/models/schemas.py`
    - Add `status: FileStatus` to the `QueueItemDetail` response model
    - _Requirements: 6.2_

  - [ ] 1.6 Verify `ValidationBreakdown` field names in `src/models/schemas.py`
    - Confirm fields are `metadata_completeness`, `semantic_quality`, `uniqueness`
    - Search codebase for alternative names (`metadata_score`, `semantic_score`, `uniqueness_score`) and fix if found
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 1.7 Write property test for ValidationBreakdown field name consistency
    - **Property 10: Validation breakdown field name consistency**
    - **Validates: Requirements 7.1, 7.2, 7.3**
    - In `tests/test_schemas.py`, generate validation results with random float values and verify field names are exactly `metadata_completeness`, `semantic_quality`, `uniqueness` through model serialization round-trip
    - Use Hypothesis: `@settings(max_examples=100)`

- [ ] 2. Implement component types override on ingestion
  - [ ] 2.1 Update `PipelineService.run()` in `src/services/pipeline.py`
    - Accept optional `component_types: list[str] | None = None` parameter
    - When not None, pass to `self.extractor.extract()` instead of `self.settings.allowlist`
    - When None, use `self.settings.allowlist` as default
    - _Requirements: 1.1, 1.2_

  - [ ] 2.2 Update `POST /ingest` handler in `src/api/ingest.py`
    - Pass `body.component_types` to `pipeline_service.run()` as keyword argument
    - _Requirements: 1.1, 1.2_

  - [ ]* 2.3 Write property test for component types forwarding
    - **Property 1: Component types forwarding**
    - **Validates: Requirements 1.1, 1.2**
    - In `tests/test_ingest.py`, generate random `component_types` lists (including None) and verify pipeline receives the correct allowlist
    - Use Hypothesis: `st.none() | st.lists(st.text())`, `@settings(max_examples=100)`

- [ ] 3. Implement review notes on accept and reviewed_by on update
  - [ ] 3.1 Update `POST /queue/{file_id}/accept` handler in `src/api/queue.py`
    - Pass `review_notes=body.review_notes` to `update_kb_file_status()`
    - _Requirements: 2.1, 2.2_

  - [ ] 3.2 Update `PUT /queue/{file_id}/update` handler in `src/api/queue.py`
    - Pass `reviewed_by=body.reviewed_by` to the file update call
    - _Requirements: 3.1_

  - [ ]* 3.3 Write property test for review notes persistence
    - **Property 2: Review notes persistence on accept**
    - **Validates: Requirements 2.1, 2.2**
    - In `tests/test_queue.py`, generate random `review_notes` strings (including None) and verify stored value matches request
    - Use Hypothesis: `st.none() | st.text()`, `@settings(max_examples=100)`

  - [ ]* 3.4 Write property test for reviewed_by attribution
    - **Property 3: Reviewed-by attribution on update**
    - **Validates: Requirements 3.1**
    - In `tests/test_queue.py`, generate random `reviewed_by` strings and verify stored value matches request
    - Use Hypothesis: `st.text(min_size=1)`, `@settings(max_examples=100)`

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement sort controls on list endpoints
  - [ ] 5.1 Add sort parameters to `list_review_queue()` and `list_kb_files()` in `src/db/queries.py`
    - Accept `sort_by` and `sort_order` parameters with defaults `"created_at"` and `"desc"`
    - Implement `_SORT_COLUMN_MAP` safelist: `{"created_at": "created_at", "validation_score": "validation_score", "title": "title"}`
    - Validate `sort_by` against safelist before interpolating into SQL ORDER BY clause
    - _Requirements: 4.3, 4.6, 4.10_

  - [ ] 5.2 Update `GET /queue` handler in `src/api/queue.py` with sort query parameters
    - Add `sort_by: SortByField = SortByField.CREATED_AT` and `sort_order: SortOrder = SortOrder.DESC` query params
    - Pass values through to `list_review_queue()`
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ] 5.3 Update `GET /files` handler in `src/api/files.py` with sort query parameters
    - Add `sort_by: SortByField = SortByField.CREATED_AT` and `sort_order: SortOrder = SortOrder.DESC` query params
    - Pass values through to `list_kb_files()`
    - _Requirements: 4.4, 4.5, 4.6_

  - [ ]* 5.4 Write property test for sort ordering correctness
    - **Property 4: Sort ordering correctness**
    - **Validates: Requirements 4.3, 4.6**
    - In `tests/test_queries.py`, generate random file sets with varying scores/titles/dates and verify returned order matches expected sort
    - Use Hypothesis: `st.lists(st.fixed_dictionaries({...}))` with sort params, `@settings(max_examples=100)`

  - [ ]* 5.5 Write property test for invalid sort_by rejection
    - **Property 5: Invalid sort_by rejection**
    - **Validates: Requirements 4.7, 4.8**
    - In `tests/test_files.py`, generate random strings not in the allowed set and verify 422 response
    - Use Hypothesis: `st.text().filter(lambda s: s not in {"created_at", "validation_score", "title"})`, `@settings(max_examples=100)`

  - [ ]* 5.6 Write property test for sort column safelist
    - **Property 6: Sort column safelist prevents injection**
    - **Validates: Requirements 4.10**
    - In `tests/test_queries.py`, generate arbitrary `sort_by` strings and verify only safelisted column names appear in the constructed SQL query
    - Use Hypothesis: `st.text()`, `@settings(max_examples=100)`

- [ ] 6. Relax queue detail status restriction
  - [ ] 6.1 Update `GET /queue/{file_id}` handler in `src/api/queue.py`
    - Remove the status guard that rejects non-`pending_review` files
    - Return full file detail for any existing file, 404 only when file does not exist
    - Include `status` field in the response using the updated `QueueItemDetail` model
    - _Requirements: 6.1, 6.2, 6.6_

  - [ ]* 6.2 Write property test for queue detail returning any existing file
    - **Property 7: Queue detail returns any existing file**
    - **Validates: Requirements 6.1, 6.2**
    - In `tests/test_queue.py`, generate files with random statuses and verify queue detail returns them all with correct status field
    - Use Hypothesis: `st.sampled_from(FileStatus)`, `@settings(max_examples=100)`

  - [ ]* 6.3 Write property test for queue list filtering
    - **Property 8: Queue list returns only pending_review**
    - **Validates: Requirements 6.3**
    - In `tests/test_queue.py`, generate file sets with mixed statuses and verify queue list returns only `pending_review` files
    - Use Hypothesis: `st.lists(st.sampled_from(FileStatus))`, `@settings(max_examples=100)`

  - [ ]* 6.4 Write property test for accept/reject pending_review guard
    - **Property 9: Accept and reject enforce pending_review guard**
    - **Validates: Requirements 6.4, 6.5**
    - In `tests/test_queue.py`, generate files with non-pending statuses and verify accept/reject return 404 without modifying status
    - Use Hypothesis: `st.sampled_from([s for s in FileStatus if s != PENDING_REVIEW])`, `@settings(max_examples=100)`

- [ ] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Update BACKEND_GUIDE.md documentation
  - [x] 8.1 Document `component_types` field in POST /ingest request body table
    - Add row for `component_types` as optional `list[str]`, description: overrides default component allowlist
    - _Requirements: 1.4_

  - [x] 8.2 Document `review_notes` field in POST /queue/{file_id}/accept request body
    - Add row for `review_notes` as optional `str`
    - _Requirements: 2.4_

  - [x] 8.3 Document `reviewed_by` field in PUT /queue/{file_id}/update request body
    - Add row for `reviewed_by` as required `str`
    - _Requirements: 3.3_

  - [x] 8.4 Document `sort_by` and `sort_order` query parameters for GET /queue and GET /files
    - Add parameter rows to both endpoint tables with allowed values and defaults
    - _Requirements: 4.9_

  - [x] 8.5 Add configurable validation threshold note to Validation Scoring section
    - Document `AUTO_APPROVE_THRESHOLD` (default 0.7) and `AUTO_REJECT_THRESHOLD` (default 0.2) environment variables
    - Describe auto-approve, auto-reject, and pending_review routing logic
    - _Requirements: 5.1, 5.2_

  - [x] 8.6 Update GET /queue/{file_id} documentation
    - Remove status restriction note from error documentation
    - Document new `status` field in the response
    - _Requirements: 6.7_

  - [x] 8.7 Verify validation breakdown field names in all BACKEND_GUIDE.md examples
    - Ensure all JSON examples use `metadata_completeness`, `semantic_quality`, `uniqueness`
    - Remove any alternative names (`metadata_score`, `semantic_score`, `uniqueness_score`) if found
    - _Requirements: 7.4, 7.5_

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests use Hypothesis with `@settings(max_examples=100)` and tag format: `# Feature: api-guide-corrections, Property {N}: {title}`
- No database migrations required — all changes use existing columns
