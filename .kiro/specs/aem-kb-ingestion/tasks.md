# Implementation Plan: AEM Knowledge Base Ingestion System

## Overview

Build a Python FastAPI pipeline that fetches AEM model.json content, extracts content nodes via an Extractor Agent (Strands SDK + Bedrock), validates them via a Validator Agent, routes based on score thresholds, and uploads approved files to S3. All state is tracked in NeonDB (PostgreSQL). Implementation proceeds bottom-up: config → models → DB → tools → agents → services → API → wiring.

## Tasks

- [x] 1. Set up project structure, configuration, and data models
  - [x] 1.1 Create project skeleton with pyproject.toml and directory structure
    - Create `pyproject.toml` with dependencies: fastapi, uvicorn, httpx, asyncpg, boto3, strands-agents, strands-agents-bedrock, pydantic-settings, markdownify, python-frontmatter, hypothesis, pytest, pytest-asyncio, respx, moto
    - Create `.env.example` with all required environment variables
    - Create `src/main.py` stub, `src/config.py`, and all `__init__.py` files
    - _Requirements: 14.1_

  - [x] 1.2 Implement configuration module (`src/config.py`)
    - Define `Settings` class extending `BaseSettings` with all fields: database_url, aws_region, s3_bucket_name, bedrock_model_id, aem_request_timeout, auto_approve_threshold, auto_reject_threshold, allowlist, denylist
    - Load from `.env` file
    - _Requirements: 1.3, 2.2, 2.3, 5.1, 5.2, 5.3_

  - [x] 1.3 Implement Pydantic data models (`src/models/schemas.py`)
    - Define enums: `FileStatus`, `JobStatus`
    - Define internal models: `ContentNode`, `MarkdownFile`, `ValidationBreakdown`, `ValidationResult`, `S3UploadResult`, `DuplicateCheckResult`, `FrontmatterResult`
    - Define API request models: `IngestRequest`, `AcceptRequest`, `RejectRequest`, `UpdateRequest`
    - Define API response models: `IngestResponse`, `QueueActionResponse`, `QueueItemSummary`, `QueueItemDetail`, `FileSummary`, `FileDetail`, `IngestionJobResponse`, `PaginatedResponse`
    - _Requirements: 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 7.2, 8.2, 10.1_

- [x] 2. Implement database layer
  - [x] 2.1 Create SQL migration script (`src/db/migrations/001_initial.sql`)
    - Create `kb_files` table with all columns including region and brand as NOT NULL TEXT
    - Create `ingestion_jobs` table with all columns including `duplicates_skipped`
    - Create indexes on kb_files: content_hash, status, region, brand, source_url, content_type, created_at
    - Create index on ingestion_jobs: status
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 2.2 Implement database connection pool (`src/db/connection.py`)
    - Implement `create_pool()` using asyncpg with SSL required
    - Implement pool cleanup on shutdown
    - _Requirements: 7.1_

  - [x] 2.3 Implement database query functions (`src/db/queries.py`)
    - Implement `insert_kb_file`, `update_kb_file_status`, `get_kb_file`, `list_kb_files`, `find_by_content_hash`
    - Implement `insert_ingestion_job`, `update_ingestion_job`, `get_ingestion_job`
    - Implement `list_review_queue` with filtering by region, brand, content_type, component_type and pagination
    - Ensure `updated_at` is set on every status change
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2, 9.1, 12.1_

  - [ ]* 2.4 Write unit tests for database query functions
    - Test insert and retrieval of kb_files and ingestion_jobs
    - Test filtering and pagination logic
    - Test `find_by_content_hash` returns correct results
    - _Requirements: 7.2, 9.1_

- [x] 3. Implement extractor tools
  - [x] 3.1 Implement `fetch_aem_json` tool (`src/tools/fetch_aem.py`)
    - Use httpx async GET with configurable timeout (30s default)
    - Return parsed JSON on success
    - Raise `ToolError` on non-200 status, timeout, or invalid JSON
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 3.2 Write property test for fetch error handling
    - **Property 1: Fetch error sets job to failed**
    - **Validates: Requirements 1.2, 1.4**

  - [x] 3.3 Implement `filter_by_component_type` tool (`src/tools/filter_components.py`)
    - Implement recursive traversal of `:items` objects
    - Implement glob-style suffix matching for allowlist/denylist
    - Denylist takes precedence over allowlist
    - Preserve parent node path as `parent_context`
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [ ]* 3.4 Write property tests for component filtering
    - **Property 2: Recursive traversal discovers all typed nodes**
    - **Property 3: Component type filtering correctness**
    - **Property 4: Node count invariant**
    - **Property 5: Parent context path preservation**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

  - [x] 3.5 Implement `html_to_markdown` tool (`src/tools/html_converter.py`)
    - Use markdownify library to convert HTML to clean Markdown
    - Strip all remaining HTML tags from output
    - _Requirements: 3.2_

  - [ ]* 3.6 Write property test for HTML conversion
    - **Property 7: HTML to Markdown conversion removes HTML tags**
    - **Validates: Requirements 3.2**

  - [x] 3.7 Implement `generate_md_file` tool (`src/tools/md_generator.py`)
    - Generate YAML frontmatter with all required fields
    - Compute SHA-256 content hash from body only (excluding frontmatter)
    - Populate modify_date from dataLayer, extracted_at as current UTC
    - Populate region and brand from ingestion request parameters
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 3.8 Write property tests for markdown generation
    - **Property 6: One-to-one node-to-file mapping**
    - **Property 8: Generated frontmatter contains all required fields**
    - **Property 9: Content hash excludes frontmatter**
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6, 3.7**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement validator tools and agents
  - [x] 5.1 Implement `parse_frontmatter` tool (`src/tools/frontmatter_parser.py`)
    - Parse YAML frontmatter using python-frontmatter
    - Return metadata dict, body, missing required fields list, and validity flag
    - _Requirements: 4.1, 15.1_

  - [ ]* 5.2 Write property test for frontmatter round-trip
    - **Property 28: Markdown frontmatter round-trip**
    - **Validates: Requirements 15.1, 15.2, 15.3**

  - [x] 5.3 Implement `check_duplicate` tool (`src/tools/duplicate_checker.py`)
    - Query KB_Files_Table by content_hash using asyncpg pool
    - Return `DuplicateCheckResult` with is_duplicate flag and existing_file_id
    - _Requirements: 9.1, 9.3_

  - [ ]* 5.4 Write property test for duplicate detection
    - **Property 20: Duplicate content hash skips file creation**
    - **Validates: Requirements 9.1, 9.2**

  - [x] 5.5 Implement Extractor Agent (`src/agents/extractor.py`)
    - Create `ExtractorAgent` class wrapping Strands Agent with BedrockModel
    - Register tools: fetch_aem_json, filter_by_component_type, html_to_markdown, generate_md_file
    - Define system prompt for content extraction
    - Implement `extract()` method returning `list[MarkdownFile]`
    - _Requirements: 1.1, 2.1, 3.1_

  - [x] 5.6 Implement Validator Agent (`src/agents/validator.py`)
    - Create `ValidatorAgent` class wrapping Strands Agent with BedrockModel
    - Register tools: check_duplicate, parse_frontmatter
    - Define system prompt for validation scoring
    - Implement `validate()` method returning `ValidationResult`
    - Ensure sub-scores are within defined ranges and score equals their sum
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 5.7 Write property tests for validation scoring
    - **Property 10: Validation sub-scores within defined ranges**
    - **Property 11: Validation score is sum of sub-scores**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

- [x] 6. Implement services layer
  - [x] 6.1 Implement S3 upload service (`src/services/s3_upload.py`)
    - Create `S3UploadService` class with boto3 S3 client
    - Implement `upload()` with key structure: `knowledge-base/{content_type}/{YYYY-MM}/{filename}`
    - Set ContentType to `text/markdown`, include file_id and content_hash as metadata
    - Return `S3UploadResult` with bucket, key, and uploaded_at
    - Handle upload failures: retain approved status, log error
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 6.2 Write property tests for S3 upload
    - **Property 14: S3 upload key structure and metadata**
    - **Property 15: Approved files transition to in_s3 after upload**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**

  - [x] 6.3 Implement Pipeline Service (`src/services/pipeline.py`)
    - Create `PipelineService` class with extractor, validator, db_pool, s3_service, settings
    - Implement `run()` method orchestrating: fetch → extract → insert to DB → validate → route → upload → complete job
    - Insert each file with status `pending_validation`
    - Route based on score thresholds: ≥0.7 approved, 0.2–0.7 pending_review, <0.2 auto_rejected
    - Store validation_score, breakdown, and issues for every file
    - Check content_hash for duplicates before creating records; increment duplicates_skipped
    - Upload approved files to S3, update status to in_s3
    - Update job with final counters and completed status
    - Handle errors: set job to failed with error_message on unrecoverable errors
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 7.1, 7.3, 7.4, 8.1, 8.3, 8.4, 9.1, 9.2_

  - [ ]* 6.4 Write property tests for pipeline routing and lifecycle
    - **Property 12: Score-based routing correctness**
    - **Property 13: Validation data always persisted**
    - **Property 16: Initial file status is pending_validation**
    - **Property 17: Status transitions follow lifecycle rules**
    - **Property 18: Status change updates timestamp**
    - **Property 19: Job completion records accurate counters**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 7.1, 7.3, 7.4, 8.3**

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement API layer
  - [x] 8.1 Implement ingestion API endpoints (`src/api/ingest.py`)
    - POST `/api/v1/ingest`: validate IngestRequest, create ingestion_job, launch BackgroundTask for pipeline.run(), return 202 with job_id
    - GET `/api/v1/ingest/{job_id}`: return full IngestionJobResponse, 404 if not found
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 11.1, 11.2_

  - [ ]* 8.2 Write property tests for ingestion API
    - **Property 21: Valid ingest request returns 202 with job_id**
    - **Property 22: Invalid ingest request returns 422**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

  - [x] 8.3 Implement review queue API endpoints (`src/api/queue.py`)
    - GET `/api/v1/queue`: paginated list of pending_review files with filters (region, brand, content_type, component_type)
    - GET `/api/v1/queue/{file_id}`: full QueueItemDetail, 404 if not found or not pending_review
    - POST `/api/v1/queue/{file_id}/accept`: set status to approved, record reviewed_by and reviewed_at, trigger S3 upload
    - POST `/api/v1/queue/{file_id}/reject`: set status to rejected, record reviewed_by, reviewed_at, review_notes
    - PUT `/api/v1/queue/{file_id}/update`: update md_content, recompute content_hash, refresh updated_at, preserve status
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [ ]* 8.4 Write property tests for queue API
    - **Property 23: Queue listing returns only pending_review files matching filters**
    - **Property 24: Accept sets status to approved with review metadata**
    - **Property 25: Reject sets status to rejected with review metadata**
    - **Property 26: Content update recomputes hash without changing status**
    - **Validates: Requirements 12.1, 12.4, 12.5, 12.6**

  - [x] 8.5 Implement files listing API endpoints (`src/api/files.py`)
    - GET `/api/v1/files`: paginated list of all files with filters (status, region, brand, content_type, component_type)
    - GET `/api/v1/files/{file_id}`: full FileDetail, 404 if not found
    - _Requirements: 13.1, 13.2, 13.3_

  - [ ]* 8.6 Write property test for files listing
    - **Property 27: Files listing respects filters**
    - **Validates: Requirements 13.1**

  - [x] 8.7 Implement API router aggregation (`src/api/router.py`)
    - Create top-level APIRouter with `/api/v1` prefix
    - Include ingest, queue, and files sub-routers
    - _Requirements: 10.1, 12.1, 13.1_

- [x] 9. Wire application together and finalize
  - [x] 9.1 Implement FastAPI app factory and lifespan (`src/main.py`)
    - Create FastAPI app with lifespan context manager
    - On startup: load Settings, create asyncpg pool, create boto3 S3 client, instantiate agents and services
    - On shutdown: close asyncpg pool
    - Include top-level API router
    - Store shared dependencies (pool, services) in app.state
    - _Requirements: 1.1, 7.1, 8.1_

  - [x] 9.2 Implement test configuration (`tests/conftest.py`)
    - Set up Hypothesis profiles (dev: 100 examples, ci: 200 examples)
    - Create shared fixtures: test database pool, mocked S3 (moto), mocked HTTP (respx), FastAPI test client
    - _Requirements: 15.3_

  - [ ]* 9.3 Write integration tests for end-to-end pipeline
    - Test full pipeline flow with mocked AEM endpoint, mocked Bedrock, test DB, and mocked S3
    - Verify job status transitions from in_progress to completed
    - Verify file status transitions through the full lifecycle
    - Verify counter accuracy on completed job
    - _Requirements: 8.3, 7.4_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use Hypothesis with minimum 100 examples per test
- Checkpoints ensure incremental validation at key milestones
- The design uses Python throughout; all code examples and implementations use Python with FastAPI, asyncpg, boto3, and Strands Agents SDK
