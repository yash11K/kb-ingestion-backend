# Design Document: Revalidation Pipeline

## Overview

This design adds revalidation capabilities to the AEM Knowledge Base Ingestion System. It introduces three main changes:

1. Per-file error resilience in the existing `PipelineService` so a single validation failure no longer aborts the entire ingestion job.
2. A synchronous single-file revalidation endpoint (`POST /api/v1/files/{file_id}/revalidate`) that re-runs the `ValidatorAgent` on an existing KB file and returns the updated result.
3. An asynchronous batch revalidation endpoint (`POST /api/v1/revalidate`) with job tracking (`GET /api/v1/revalidate/{job_id}`) that processes multiple files in the background.

All revalidation follows the same score-routing logic as ingestion: files scoring ≥ `auto_approve_threshold` are approved and uploaded to S3, files scoring < `auto_reject_threshold` are auto-rejected, and files in between remain `pending_review`.

## Architecture

The feature extends the existing architecture with minimal new components:

```mermaid
graph TD
    A[API Layer] -->|POST /files/{id}/revalidate| B[RevalidationService]
    A -->|POST /revalidate| B
    A -->|GET /revalidate/{job_id}| C[DB: revalidation_jobs]
    B -->|validate| D[ValidatorAgent]
    B -->|update status| E[DB: kb_files]
    B -->|upload approved| F[S3UploadService]
    B -->|track progress| C
    G[PipelineService] -->|try/catch per file| D
```

Key architectural decisions:

- **New `RevalidationService` class** rather than extending `PipelineService`. The pipeline handles extraction + insertion + validation; revalidation only re-validates existing records. Keeping them separate avoids coupling.
- **Reuse existing `ValidatorAgent`** and `S3UploadService` directly — no wrappers or adapters needed.
- **New `revalidation_jobs` table** separate from `ingestion_jobs` because the schemas differ (revalidation tracks completed/failed/not_found counters, not files_created/duplicates_skipped).
- **FastAPI `BackgroundTasks`** for batch processing, consistent with the existing ingestion pattern.

## Components and Interfaces

### 1. RevalidationService (`src/services/revalidation.py`)

New service class that encapsulates all revalidation logic.

```python
class RevalidationService:
    def __init__(
        self,
        validator: ValidatorAgent,
        db_pool: asyncpg.Pool,
        s3_service: S3UploadService,
        settings: Settings,
    ) -> None: ...

    async def revalidate_single(self, file_id: UUID) -> dict:
        """Revalidate one file synchronously. Returns updated file record.
        Raises FileNotFoundError if file_id doesn't exist.
        Raises RuntimeError if ValidatorAgent fails."""

    async def revalidate_batch(self, job_id: UUID, file_ids: list[UUID]) -> None:
        """Background task: revalidate multiple files, updating job progress."""
```

Internal helpers (private):
- `_run_validation(record: dict) -> ValidationResult` — calls `ValidatorAgent.validate` after reconstructing a `MarkdownFile` from the DB record.
- `_route_and_update(file_id: UUID, result: ValidationResult) -> None` — applies score thresholds, updates DB status, uploads to S3 if approved.

### 2. Revalidation API routes (`src/api/revalidate.py`)

New router with three endpoints:

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| POST | `/files/{file_id}/revalidate` | 200 | Synchronous single-file revalidation |
| POST | `/revalidate` | 202 | Start batch revalidation job |
| GET | `/revalidate/{job_id}` | 200 | Get batch job status |

The single-file endpoint lives under `/files/` for REST consistency but is defined in the revalidate router and mounted accordingly.

### 3. PipelineService modification (`src/services/pipeline.py`)

Wrap the per-file validation call in a try/except inside `_run_pipeline`. On exception:
- Log at ERROR level with file identifier and exception details.
- Increment `files_pending_review` counter (file stays `pending_review` with null validation fields).
- Continue to next file.

### 4. Database queries (`src/db/queries.py`)

New query functions:
- `insert_revalidation_job(pool, total_files) -> UUID`
- `update_revalidation_job(pool, job_id, **kwargs) -> None`
- `get_revalidation_job(pool, job_id) -> dict | None`

### 5. Database migration (`src/db/migrations/002_revalidation_jobs.sql`)

Creates the `revalidation_jobs` table and index.

### 6. Pydantic schemas (`src/models/schemas.py`)

New models:
- `RevalidateRequest` — request body for batch endpoint
- `RevalidateResponse` — 202 acceptance response
- `RevalidationJobResponse` — full job status response

## Data Models

### RevalidateRequest

```python
class RevalidateRequest(BaseModel):
    file_ids: list[UUID] = Field(..., min_length=1)
```

### RevalidateResponse

```python
class RevalidateResponse(BaseModel):
    job_id: UUID
    status: JobStatus
```

### RevalidationJobResponse

```python
class RevalidationJobResponse(BaseModel):
    id: UUID
    status: JobStatus
    total_files: int
    completed: int
    failed: int
    not_found: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
```

### revalidation_jobs table

```sql
CREATE TABLE revalidation_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status          TEXT NOT NULL DEFAULT 'in_progress',
    total_files     INTEGER NOT NULL,
    completed       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    not_found       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_revalidation_jobs_status ON revalidation_jobs (status);
```

### MarkdownFile reconstruction

The `RevalidationService` needs to reconstruct a `MarkdownFile` from a DB record to pass to `ValidatorAgent.validate()`. The DB record contains all required fields (`filename`, `title`, `content_type`, `source_url`, `component_type`, `aem_node_id`, `md_content`, `content_hash`, `modify_date`, `parent_context`, `region`, `brand`). The `md_body` and `extracted_at` fields are derived: `md_body` is extracted by stripping frontmatter from `md_content`, and `extracted_at` defaults to `datetime.now(UTC)`.


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Score routing correctness

*For any* validation score and any pair of (auto_reject_threshold, auto_approve_threshold) where reject < approve, the routing function shall return `approved` when score ≥ approve, `auto_rejected` when score < reject, and `pending_review` otherwise. This must be a total function over all floats in [0.0, 1.0].

**Validates: Requirements 2.4, 2.5, 2.6**

### Property 2: Pipeline error resilience

*For any* batch of N files where K files (0 ≤ K ≤ N) raise exceptions during validation, the pipeline shall still process all N−K non-failing files to completion, and each of the K failing files shall remain in the database with `pending_review` status and null `validation_score`, `validation_breakdown`, and `validation_issues`.

**Validates: Requirements 1.1, 1.2**

### Property 3: Pipeline counter accuracy under failures

*For any* ingestion batch of N files (after deduplication) where K files fail validation, the completed ingestion job counters shall satisfy: `files_created = N` and `files_auto_approved + files_pending_review + files_auto_rejected = N`, with `files_pending_review ≥ K` (since failed files stay pending_review).

**Validates: Requirements 1.4**

### Property 4: Revalidation updates validation fields

*For any* existing KB file with any prior status, after successful revalidation the file's `validation_score`, `validation_breakdown`, and `validation_issues` in the database shall equal the values returned by the `ValidatorAgent`, and the file's status shall match the score-routing result.

**Validates: Requirements 2.3, 2.1**

### Property 5: Approved revalidation triggers S3 upload

*For any* KB file whose revalidation score meets or exceeds the auto_approve_threshold, after revalidation the file's status shall be `in_s3` and its `s3_bucket`, `s3_key`, and `s3_uploaded_at` fields shall be non-null.

**Validates: Requirements 2.7**

### Property 6: Revalidation exception leaves record unchanged

*For any* existing KB file, if the `ValidatorAgent` raises an exception during single-file revalidation, the file's database record (status, validation_score, validation_breakdown, validation_issues) shall be identical to its state before the revalidation attempt, and the API shall return HTTP 502.

**Validates: Requirements 2.8**

### Property 7: Batch job counter invariant

*For any* batch revalidation job with `total_files = T`, at any point during or after processing, the invariant `completed + failed + not_found ≤ T` shall hold. Upon job completion, `completed + failed + not_found = T` and the job status shall be `completed` with a non-null `completed_at`.

**Validates: Requirements 3.3, 3.5, 4.1, 4.2, 4.3**

### Property 8: Batch request input validation

*For any* request body sent to `POST /api/v1/revalidate`, if the `file_ids` field is missing, not a list, empty, or contains non-UUID values, the API shall return HTTP 422. For any non-empty list of valid UUIDs, the API shall return HTTP 202.

**Validates: Requirements 3.2, 6.1**

## Error Handling

| Scenario | Component | Behavior |
|----------|-----------|----------|
| Single file validation exception during ingestion | `PipelineService._run_pipeline` | Catch exception, log at ERROR with file ID and exception, leave file as `pending_review` with null validation fields, continue to next file |
| Entire pipeline failure (e.g., extraction fails) | `PipelineService.run` | Existing behavior: catch at top level, mark ingestion job as `failed` with error message |
| Single-file revalidation: file not found | `POST /files/{file_id}/revalidate` | Return HTTP 404 with `{"detail": "File not found"}` |
| Single-file revalidation: validator exception | `POST /files/{file_id}/revalidate` | Return HTTP 502 with `{"detail": "Validation service unavailable"}`, leave DB record unchanged |
| Batch revalidation: empty file_ids | `POST /revalidate` | Pydantic validation returns HTTP 422 automatically (via `min_length=1`) |
| Batch revalidation: file_id not found | `RevalidationService.revalidate_batch` | Skip file, increment `not_found` counter on job, continue |
| Batch revalidation: validator exception for one file | `RevalidationService.revalidate_batch` | Log error, increment `failed` counter on job, continue to next file |
| Batch revalidation: unrecoverable error (e.g., DB connection lost) | `RevalidationService.revalidate_batch` | Catch at top level, set job status to `failed` with `error_message` and `completed_at` |
| Batch job status: job not found | `GET /revalidate/{job_id}` | Return HTTP 404 with `{"detail": "Revalidation job not found"}` |
| S3 upload failure after approval | `RevalidationService._route_and_update` | Log error, file retains `approved` status (consistent with existing pipeline behavior) |

## Testing Strategy

### Property-Based Tests

Use `hypothesis` (Python property-based testing library) for all correctness properties. Each test runs a minimum of 100 iterations.

| Test | Property | Description |
|------|----------|-------------|
| `test_score_routing_correctness` | Property 1 | Generate random scores in [0.0, 1.0] and random threshold pairs, verify routing output matches expected status |
| `test_pipeline_error_resilience` | Property 2 | Generate random file batches with random failure indices, mock validator to raise on those indices, verify all others processed and failures retain null fields |
| `test_pipeline_counter_accuracy` | Property 3 | Generate random batches with random failures, verify counter sums equal total files created |
| `test_revalidation_updates_fields` | Property 4 | Generate random validation results, mock validator to return them, verify DB record matches |
| `test_approved_triggers_s3_upload` | Property 5 | Generate random scores ≥ approve threshold, verify file ends up as in_s3 with S3 metadata |
| `test_exception_leaves_record_unchanged` | Property 6 | Generate random file states, mock validator to raise, verify record unchanged and 502 returned |
| `test_batch_counter_invariant` | Property 7 | Generate random batches with mix of existing/non-existing/failing files, verify counter invariant holds |
| `test_batch_input_validation` | Property 8 | Generate random invalid and valid request bodies, verify 422 vs 202 responses |

Each property test must be tagged with a comment:
```python
# Feature: revalidation-pipeline, Property {N}: {property_text}
```

### Unit Tests

Unit tests complement property tests for specific examples, edge cases, and integration points:

- Single-file revalidation with a known file returning a known score (example)
- Single-file revalidation with non-existent UUID returns 404 (edge case)
- Batch revalidation with all file_ids not found (edge case)
- Batch job status endpoint returns correct fields (example)
- Database migration creates table with correct schema (example)
- `RevalidationService` reconstructs `MarkdownFile` from DB record correctly (example)
- Pipeline continues after validation timeout (integration)
- S3 upload failure during revalidation logs error and retains approved status (edge case)
