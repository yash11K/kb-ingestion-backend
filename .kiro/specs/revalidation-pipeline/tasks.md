# Implementation Plan: Revalidation Pipeline

## Overview

Add per-file error resilience to the existing ingestion pipeline, a synchronous single-file revalidation endpoint, and an asynchronous batch revalidation endpoint with job tracking. Implementation proceeds: DB migration → schemas → DB queries → pipeline fix → revalidation service → API routes → wiring.

## Tasks

- [x] 1. Database migration and schema models
  - [x] 1.1 Create database migration for revalidation_jobs table (`src/db/migrations/002_revalidation_jobs.sql`)
    - Create `revalidation_jobs` table with columns: id (UUID PK, default uuid_generate_v4()), status (TEXT, default 'in_progress'), total_files (INTEGER), completed (INTEGER, default 0), failed (INTEGER, default 0), not_found (INTEGER, default 0), error_message (TEXT, nullable), started_at (TIMESTAMPTZ, default NOW()), completed_at (TIMESTAMPTZ, nullable)
    - Create index `idx_revalidation_jobs_status` on `revalidation_jobs.status`
    - _Requirements: 5.1, 5.2_

  - [x] 1.2 Add revalidation Pydantic models to `src/models/schemas.py`
    - Add `RevalidateRequest` with `file_ids: list[UUID]` field using `Field(..., min_length=1)`
    - Add `RevalidateResponse` with `job_id: UUID` and `status: JobStatus`
    - Add `RevalidationJobResponse` with fields: id, status, total_files, completed, failed, not_found, error_message, started_at, completed_at
    - _Requirements: 6.1, 6.2, 6.3, 6.4_


- [x] 2. Database query functions for revalidation jobs
  - [x] 2.1 Add revalidation job query functions to `src/db/queries.py`
    - Implement `insert_revalidation_job(pool, total_files) -> UUID` — insert with status 'in_progress', started_at NOW(), counters at 0
    - Implement `update_revalidation_job(pool, job_id, **kwargs) -> None` — dynamic SET clause for any combination of status, completed, failed, not_found, error_message, completed_at
    - Implement `get_revalidation_job(pool, job_id) -> dict | None` — SELECT * with _row_to_dict
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1_

  - [ ]* 2.2 Write unit tests for revalidation job query functions
    - Test insert creates record with correct defaults
    - Test update increments counters correctly
    - Test get returns None for non-existent job_id
    - _Requirements: 4.1, 5.1_

- [x] 3. Add per-file error handling to PipelineService
  - [x] 3.1 Modify `PipelineService._run_pipeline` in `src/services/pipeline.py`
    - Wrap the per-file validation call (`self.validator.validate(md_file)`) and subsequent routing/update in a try/except block
    - On exception: log at ERROR level with file_id and exception details, increment `files_pending_review` counter, continue to next file
    - File remains in DB with `pending_review` status and null validation_score, validation_breakdown, validation_issues (already set at insert)
    - Ensure job still completes with accurate counters after partial failures
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 3.2 Write property test for pipeline error resilience
    - **Property 2: Pipeline error resilience**
    - Mock ValidatorAgent to raise exceptions for a subset of files, verify remaining files are processed and failing files retain null validation fields
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 3.3 Write property test for pipeline counter accuracy under failures
    - **Property 3: Pipeline counter accuracy under failures**
    - Verify `files_created = N` and `files_auto_approved + files_pending_review + files_auto_rejected = N` with `files_pending_review >= K` failed files
    - **Validates: Requirements 1.4**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement RevalidationService
  - [x] 5.1 Create `src/services/revalidation.py` with `RevalidationService` class
    - Constructor takes `validator: ValidatorAgent`, `db_pool: asyncpg.Pool`, `s3_service: S3UploadService`, `settings: Settings`
    - Implement private `_reconstruct_markdown_file(record: dict) -> MarkdownFile` — rebuild MarkdownFile from DB record fields, derive md_body by stripping frontmatter from md_content, set extracted_at to now(UTC)
    - Implement private `_route_by_score(score: float) -> FileStatus` — same threshold logic as PipelineService: score >= auto_approve_threshold → APPROVED, score < auto_reject_threshold → AUTO_REJECTED, else PENDING_REVIEW
    - Implement private `_route_and_update(file_id: UUID, result: ValidationResult) -> None` — update DB with validation results and routed status, upload to S3 if approved (with same error handling as pipeline: log and retain approved on S3 failure)
    - _Requirements: 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 5.2 Implement `revalidate_single(self, file_id: UUID) -> dict` method
    - Fetch file record via `get_kb_file`; raise `FileNotFoundError` if None
    - Reconstruct MarkdownFile, call `validator.validate(md_file)`
    - Call `_route_and_update` with results
    - Return updated file record via `get_kb_file`
    - On ValidatorAgent exception: raise `RuntimeError` (leave DB unchanged)
    - _Requirements: 2.1, 2.2, 2.3, 2.7, 2.8_

  - [x] 5.3 Implement `revalidate_batch(self, job_id: UUID, file_ids: list[UUID]) -> None` method
    - Iterate over file_ids; for each: fetch record, skip if not found (increment not_found), validate, route_and_update, increment completed; on validator exception: log error, increment failed
    - After each file: update revalidation job counters via `update_revalidation_job`
    - On completion: set job status to 'completed' with completed_at
    - Wrap entire method in try/except for unrecoverable errors: set job status to 'failed' with error_message and completed_at
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4_

  - [ ]* 5.4 Write property test for score routing correctness
    - **Property 1: Score routing correctness**
    - Generate random scores in [0.0, 1.0] and random threshold pairs where reject < approve, verify routing returns correct status
    - **Validates: Requirements 2.4, 2.5, 2.6**

  - [ ]* 5.5 Write property test for revalidation field updates
    - **Property 4: Revalidation updates validation fields**
    - Generate random ValidationResult values, mock validator, verify DB record matches after revalidation
    - **Validates: Requirements 2.3, 2.1**

  - [ ]* 5.6 Write property test for approved revalidation S3 upload
    - **Property 5: Approved revalidation triggers S3 upload**
    - Generate scores >= auto_approve_threshold, verify file ends up as in_s3 with non-null S3 metadata
    - **Validates: Requirements 2.7**

  - [ ]* 5.7 Write property test for revalidation exception safety
    - **Property 6: Revalidation exception leaves record unchanged**
    - Generate random file states, mock validator to raise, verify record unchanged
    - **Validates: Requirements 2.8**

  - [ ]* 5.8 Write property test for batch job counter invariant
    - **Property 7: Batch job counter invariant**
    - Generate random batches with mix of existing/non-existing/failing files, verify `completed + failed + not_found = total_files` on completion
    - **Validates: Requirements 3.3, 3.5, 4.1, 4.2, 4.3**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement revalidation API routes
  - [x] 7.1 Create `src/api/revalidate.py` with revalidation router
    - `POST /files/{file_id}/revalidate` — call `revalidation_service.revalidate_single(file_id)`, return FileDetail on success, 404 if FileNotFoundError, 502 if RuntimeError
    - `POST /revalidate` — validate RevalidateRequest body, create revalidation job via `insert_revalidation_job`, add `revalidation_service.revalidate_batch` as BackgroundTask, return 202 with RevalidateResponse
    - `GET /revalidate/{job_id}` — fetch job via `get_revalidation_job`, return RevalidationJobResponse, 404 if not found
    - _Requirements: 2.1, 2.2, 2.8, 3.1, 3.2, 3.6, 4.5, 4.6, 6.1, 6.2, 6.3, 6.4_

  - [ ]* 7.2 Write property test for batch request input validation
    - **Property 8: Batch request input validation**
    - Generate random invalid and valid request bodies, verify 422 vs 202 responses
    - **Validates: Requirements 3.2, 6.1**

  - [ ]* 7.3 Write unit tests for revalidation API endpoints
    - Test single-file revalidation returns updated FileDetail
    - Test single-file revalidation with non-existent UUID returns 404
    - Test single-file revalidation with validator failure returns 502
    - Test batch revalidation returns 202 with job_id
    - Test batch job status endpoint returns correct RevalidationJobResponse fields
    - Test batch job status with non-existent job_id returns 404
    - _Requirements: 2.1, 2.2, 2.8, 3.1, 4.5, 4.6_

- [x] 8. Wire revalidation into the application
  - [x] 8.1 Register revalidation router in `src/api/router.py`
    - Import and include the revalidate router in the api_router
    - _Requirements: 2.1, 3.1, 4.5_

  - [x] 8.2 Create and store RevalidationService in `src/main.py` lifespan
    - Instantiate `RevalidationService(validator, pool, s3_service, settings)` during startup
    - Store as `app.state.revalidation_service`
    - _Requirements: 2.1, 3.1_

- [x] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use Hypothesis with minimum 100 examples per test
- Checkpoints ensure incremental validation at key milestones
- All code is Python with FastAPI, asyncpg, and existing project patterns
