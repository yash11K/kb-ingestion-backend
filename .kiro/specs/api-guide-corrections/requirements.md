# Requirements Document

## Introduction

This specification covers a set of corrections and gap-fills for the AEM Knowledge Base Ingestion System's API surface and its corresponding BACKEND_GUIDE.md documentation. The changes span seven areas: adding a component-type override to ingestion, adding audit fields to queue actions, introducing sort controls on list endpoints, documenting configurable validation thresholds, relaxing the queue detail status restriction, and standardizing validation breakdown field names. Each change requires both code modifications and documentation updates to keep the guide aligned with the implementation.

## Glossary

- **Ingestion_API**: The `POST /ingest` endpoint that starts a new AEM content ingestion job.
- **Queue_List_API**: The `GET /queue` endpoint that returns paginated pending-review files.
- **Queue_Detail_API**: The `GET /queue/{file_id}` endpoint that returns full detail for a single queued file.
- **Accept_API**: The `POST /queue/{file_id}/accept` endpoint that approves a pending-review file.
- **Reject_API**: The `POST /queue/{file_id}/reject` endpoint that rejects a pending-review file.
- **Update_API**: The `PUT /queue/{file_id}/update` endpoint that edits a file's markdown content.
- **Files_List_API**: The `GET /files` endpoint that returns paginated files across all statuses.
- **Files_Detail_API**: The `GET /files/{file_id}` endpoint that returns full detail for any tracked file.
- **Pipeline_Service**: The background service that orchestrates fetch, extract, validate, and route steps for an ingestion job.
- **Validator_Agent**: The AI agent that scores markdown files on metadata completeness, semantic quality, and uniqueness.
- **IngestRequest**: The Pydantic request model for `POST /ingest`.
- **AcceptRequest**: The Pydantic request model for `POST /queue/{file_id}/accept`.
- **UpdateRequest**: The Pydantic request model for `PUT /queue/{file_id}/update`.
- **ValidationBreakdown**: The Pydantic model representing the three validation sub-scores.
- **Settings**: The application configuration class loaded from environment variables.
- **BACKEND_GUIDE**: The `BACKEND_GUIDE.md` documentation file describing all API endpoints.
- **component_types**: An optional list of AEM component type strings used to override the server-configured allowlist for a single ingestion job.
- **review_notes**: A free-text string capturing reviewer commentary on a queue action.
- **reviewed_by**: A string identifying the person performing a queue action.
- **sort_by**: A query parameter controlling which column results are ordered by.
- **sort_order**: A query parameter controlling ascending or descending sort direction.
- **VALIDATION_SCORE_THRESHOLD**: The environment variable controlling the auto-approve score boundary (default 0.7).
- **AUTO_REJECT_THRESHOLD**: The environment variable controlling the auto-reject score boundary (default 0.2).

## Requirements

### Requirement 1: Component Types Override on Ingestion

**User Story:** As an API consumer, I want to pass an optional component_types list when starting an ingestion job, so that I can override the server's default AEM component allowlist for that specific job.

#### Acceptance Criteria

1. WHEN a `POST /ingest` request includes a `component_types` field, THE Ingestion_API SHALL pass the provided list to the Pipeline_Service as the component allowlist for that job.
2. WHEN a `POST /ingest` request omits the `component_types` field, THE Ingestion_API SHALL use the server-configured allowlist from Settings.
3. THE IngestRequest model SHALL include an optional `component_types` field of type `list[str]` with a default of `None`.
4. THE BACKEND_GUIDE SHALL document the `component_types` field in the `POST /ingest` request body table as optional, with a description stating it overrides the default component allowlist.

### Requirement 2: Optional Review Notes on Accept

**User Story:** As a reviewer, I want to optionally include review notes when accepting a file, so that the audit trail is consistent with the reject endpoint.

#### Acceptance Criteria

1. WHEN a `POST /queue/{file_id}/accept` request includes a `review_notes` field, THE Accept_API SHALL store the provided notes alongside the reviewed_by and reviewed_at fields.
2. WHEN a `POST /queue/{file_id}/accept` request omits the `review_notes` field, THE Accept_API SHALL store a null value for review_notes.
3. THE AcceptRequest model SHALL include an optional `review_notes` field of type `str` with a default of `None`.
4. THE BACKEND_GUIDE SHALL document the `review_notes` field in the `POST /queue/{file_id}/accept` request body as optional.

### Requirement 3: Reviewed-By Attribution on Content Update

**User Story:** As a reviewer, I want every content edit to be attributed to a person, so that the audit trail tracks who made each change.

#### Acceptance Criteria

1. WHEN a `PUT /queue/{file_id}/update` request is received, THE Update_API SHALL store the `reviewed_by` value from the request body on the file record.
2. THE UpdateRequest model SHALL include a required `reviewed_by` field of type `str`.
3. THE BACKEND_GUIDE SHALL document the `reviewed_by` field in the `PUT /queue/{file_id}/update` request body as required.

### Requirement 4: Sort Controls on List Endpoints

**User Story:** As an API consumer, I want to control the sort column and direction on list endpoints, so that I can order results by validation score, title, or creation date.

#### Acceptance Criteria

1. THE Queue_List_API SHALL accept a `sort_by` query parameter with allowed values `created_at`, `validation_score`, and `title`, defaulting to `created_at`.
2. THE Queue_List_API SHALL accept a `sort_order` query parameter with allowed values `asc` and `desc`, defaulting to `desc`.
3. WHEN `sort_by` and `sort_order` are provided, THE Queue_List_API SHALL order results by the specified column and direction.
4. THE Files_List_API SHALL accept a `sort_by` query parameter with allowed values `created_at`, `validation_score`, and `title`, defaulting to `created_at`.
5. THE Files_List_API SHALL accept a `sort_order` query parameter with allowed values `asc` and `desc`, defaulting to `desc`.
6. WHEN `sort_by` and `sort_order` are provided, THE Files_List_API SHALL order results by the specified column and direction.
7. IF a `sort_by` value is not one of the allowed values, THEN THE Queue_List_API SHALL return a 422 validation error.
8. IF a `sort_by` value is not one of the allowed values, THEN THE Files_List_API SHALL return a 422 validation error.
9. THE BACKEND_GUIDE SHALL document `sort_by` and `sort_order` query parameters in both the `GET /queue` and `GET /files` endpoint tables.
10. THE database query functions SHALL use a safelist to map `sort_by` values to column names, preventing SQL injection.

### Requirement 5: Configurable Validation Threshold Documentation

**User Story:** As a system operator, I want the BACKEND_GUIDE to document the configurable validation thresholds, so that I know which environment variables control auto-approve and auto-reject boundaries.

#### Acceptance Criteria

1. THE BACKEND_GUIDE SHALL include a note in the Validation Scoring section stating that `AUTO_APPROVE_THRESHOLD` (default 0.7) and `AUTO_REJECT_THRESHOLD` (default 0.2) are configurable via environment variables.
2. THE BACKEND_GUIDE SHALL state that files scoring at or above `AUTO_APPROVE_THRESHOLD` are auto-approved, files scoring below `AUTO_REJECT_THRESHOLD` are auto-rejected, and files in between are routed to pending_review.

### Requirement 6: Relax Queue Detail Status Restriction

**User Story:** As an API consumer, I want to retrieve full file details from the queue detail endpoint regardless of file status, so that I can inspect files that have already been accepted or rejected.

#### Acceptance Criteria

1. WHEN a `GET /queue/{file_id}` request is received for an existing file, THE Queue_Detail_API SHALL return the full file detail regardless of the file's current status.
2. THE Queue_Detail_API response SHALL include a `status` field indicating the file's current status.
3. THE Queue_List_API SHALL continue to return only files with `pending_review` status.
4. THE Accept_API SHALL continue to enforce that the file has `pending_review` status before accepting.
5. THE Reject_API SHALL continue to enforce that the file has `pending_review` status before rejecting.
6. IF a `GET /queue/{file_id}` request references a non-existent file_id, THEN THE Queue_Detail_API SHALL return a 404 error.
7. THE BACKEND_GUIDE SHALL update the `GET /queue/{file_id}` error documentation to remove the status restriction note and document the new `status` field in the response.

### Requirement 7: Standardize Validation Breakdown Field Names

**User Story:** As a developer, I want validation breakdown field names to be consistent across the validator agent output, database JSONB column, and all API responses, so that there is no ambiguity or mapping confusion.

#### Acceptance Criteria

1. THE Validator_Agent SHALL output breakdown fields named `metadata_completeness`, `semantic_quality`, and `uniqueness`.
2. THE ValidationBreakdown model SHALL use fields named `metadata_completeness`, `semantic_quality`, and `uniqueness`.
3. THE database JSONB `validation_breakdown` column SHALL store keys named `metadata_completeness`, `semantic_quality`, and `uniqueness`.
4. THE BACKEND_GUIDE SHALL use the field names `metadata_completeness`, `semantic_quality`, and `uniqueness` in all validation breakdown JSON examples.
5. THE BACKEND_GUIDE SHALL not use any alternative field names (such as `metadata_score`, `semantic_score`, or `uniqueness_score`) for validation breakdown fields.
