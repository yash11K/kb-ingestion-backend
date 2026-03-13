# Requirements Document

## Introduction

The AEM Knowledge Base Ingestion System currently has no mechanism to retry validation for files that failed the validation step during ingestion (e.g., due to Bedrock timeouts or throttling). These files end up in the database with `pending_review` status but null validation scores, breakdowns, and issues. This feature introduces a revalidation pipeline that provides per-file error resilience during ingestion, a synchronous single-file revalidation endpoint, an asynchronous batch revalidation endpoint with job tracking, and automatic S3 upload for files that score above the auto-approve threshold after revalidation.

## Glossary

- **Pipeline_Service**: The existing `PipelineService` class that orchestrates the AEM content ingestion pipeline (fetch, extract, validate, route, upload).
- **Validator_Agent**: The existing `ValidatorAgent` class that scores markdown files on metadata completeness, semantic quality, and uniqueness via a Bedrock-backed AI agent.
- **Revalidation_Service**: A new service responsible for running one or more existing KB files through the Validator_Agent and updating their status based on the new validation results.
- **Revalidation_API**: The new set of API endpoints for triggering and tracking revalidation of KB files.
- **KB_File**: A knowledge base file record stored in the `kb_files` database table.
- **Revalidation_Job**: A database record that tracks the progress and outcome of a batch revalidation request.
- **Auto_Approve_Threshold**: The configured validation score (default 0.7) at or above which a file is automatically approved.
- **Auto_Reject_Threshold**: The configured validation score (default 0.2) below which a file is automatically rejected.
- **S3_Upload_Service**: The existing service that uploads approved markdown files to S3.

## Requirements

### Requirement 1: Per-File Error Handling in Ingestion Pipeline

**User Story:** As a system operator, I want the ingestion pipeline to continue processing remaining files when a single file's validation fails, so that one transient error does not abort the entire ingestion job.

#### Acceptance Criteria

1. WHEN the Validator_Agent raises an exception during validation of a single KB_File, THE Pipeline_Service SHALL catch the exception and continue processing the remaining files in the batch.
2. WHEN validation fails for a single KB_File due to an exception, THE Pipeline_Service SHALL retain that KB_File in the database with `pending_review` status and null validation_score, validation_breakdown, and validation_issues.
3. WHEN validation fails for a single KB_File due to an exception, THE Pipeline_Service SHALL log the error with the file identifier and exception details at ERROR level.
4. WHEN the ingestion job completes with one or more per-file validation failures, THE Pipeline_Service SHALL still update the ingestion job record to `completed` status with accurate counters for files_created, files_auto_approved, files_pending_review, files_auto_rejected, and duplicates_skipped.

### Requirement 2: Single File Revalidation Endpoint

**User Story:** As a system operator, I want to revalidate a single KB file on demand, so that I can retry validation for files that previously failed or re-score files after content edits.

#### Acceptance Criteria

1. WHEN a POST request is sent to `/api/v1/files/{file_id}/revalidate`, THE Revalidation_API SHALL run the specified KB_File through the Validator_Agent synchronously and return the updated file detail in the response.
2. WHEN the specified file_id does not exist in the database, THE Revalidation_API SHALL return HTTP 404 with a descriptive error message.
3. WHEN the Validator_Agent completes revalidation successfully, THE Revalidation_Service SHALL update the KB_File record with the new validation_score, validation_breakdown, and validation_issues.
4. WHEN the revalidation score meets or exceeds the Auto_Approve_Threshold, THE Revalidation_Service SHALL update the KB_File status to `approved`.
5. WHEN the revalidation score is below the Auto_Reject_Threshold, THE Revalidation_Service SHALL update the KB_File status to `auto_rejected`.
6. WHEN the revalidation score is between the Auto_Reject_Threshold (inclusive) and the Auto_Approve_Threshold (exclusive), THE Revalidation_Service SHALL update the KB_File status to `pending_review`.
7. WHEN the revalidation results in a status of `approved`, THE Revalidation_Service SHALL upload the KB_File to S3 via the S3_Upload_Service and update the KB_File status to `in_s3` with the S3 bucket, key, and upload timestamp.
8. IF the Validator_Agent raises an exception during single-file revalidation, THEN THE Revalidation_API SHALL return HTTP 502 with a descriptive error message and leave the KB_File record unchanged.

### Requirement 3: Batch Revalidation Endpoint

**User Story:** As a system operator, I want to submit a batch of file IDs for revalidation, so that I can efficiently retry validation for multiple files at once without blocking the API.

#### Acceptance Criteria

1. WHEN a POST request is sent to `/api/v1/revalidate` with a JSON body containing a list of file_ids, THE Revalidation_API SHALL return HTTP 202 with a Revalidation_Job identifier.
2. WHEN the request body contains an empty file_ids list, THE Revalidation_API SHALL return HTTP 422 with a descriptive error message.
3. WHEN any file_id in the request does not exist in the database, THE Revalidation_Service SHALL skip that file_id, increment a not_found counter on the Revalidation_Job, and continue processing the remaining files.
4. THE Revalidation_Service SHALL process each file in the batch through the Validator_Agent, update validation results, route by score, and upload to S3 when auto-approved, using the same logic as single-file revalidation.
5. WHEN the Validator_Agent raises an exception for a single file during batch revalidation, THE Revalidation_Service SHALL log the error, increment a failed counter on the Revalidation_Job, and continue processing the remaining files.
6. THE Revalidation_Service SHALL execute batch revalidation as a background task so that the API response returns immediately.

### Requirement 4: Revalidation Job Tracking

**User Story:** As a system operator, I want to track the progress of a batch revalidation job, so that I can monitor completion and identify failures.

#### Acceptance Criteria

1. THE Revalidation_Service SHALL create a Revalidation_Job record in the database when a batch revalidation request is accepted, with status `in_progress`, total_files set to the count of requested file_ids, and all counters initialized to zero.
2. WHILE a batch revalidation job is in progress, THE Revalidation_Service SHALL update the Revalidation_Job record after each file is processed with incremented counters for completed, failed, or not_found.
3. WHEN all files in a batch revalidation job have been processed, THE Revalidation_Service SHALL update the Revalidation_Job status to `completed` with a completed_at timestamp.
4. IF an unrecoverable error occurs that prevents the batch revalidation job from continuing, THEN THE Revalidation_Service SHALL update the Revalidation_Job status to `failed` with an error_message and completed_at timestamp.
5. WHEN a GET request is sent to `/api/v1/revalidate/{job_id}`, THE Revalidation_API SHALL return the full Revalidation_Job record including status, total_files, completed, failed, not_found, error_message, started_at, and completed_at.
6. WHEN the specified job_id does not exist, THE Revalidation_API SHALL return HTTP 404 with a descriptive error message.

### Requirement 5: Revalidation Job Database Schema

**User Story:** As a developer, I want a dedicated database table for revalidation jobs, so that revalidation tracking is cleanly separated from ingestion job tracking.

#### Acceptance Criteria

1. THE database migration SHALL create a `revalidation_jobs` table with columns: id (UUID, primary key), status (TEXT, default `in_progress`), total_files (INTEGER), completed (INTEGER, default 0), failed (INTEGER, default 0), not_found (INTEGER, default 0), error_message (TEXT, nullable), started_at (TIMESTAMPTZ), and completed_at (TIMESTAMPTZ, nullable).
2. THE database migration SHALL create an index on the `revalidation_jobs.status` column.

### Requirement 6: Revalidation Request and Response Schemas

**User Story:** As a developer, I want well-defined request and response models for the revalidation endpoints, so that the API contract is clear and validated.

#### Acceptance Criteria

1. THE Revalidation_API SHALL validate the batch revalidation request body against a schema requiring a `file_ids` field that is a non-empty list of UUID strings.
2. THE Revalidation_API SHALL return the batch revalidation acceptance response with fields: job_id (UUID) and status (JobStatus).
3. THE Revalidation_API SHALL return the revalidation job status response with fields: id (UUID), status (JobStatus), total_files (integer), completed (integer), failed (integer), not_found (integer), error_message (string or null), started_at (datetime), and completed_at (datetime or null).
4. THE Revalidation_API SHALL return the single-file revalidation response using the existing FileDetail schema.
