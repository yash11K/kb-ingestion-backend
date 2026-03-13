# API Reference

Base URL: `http://localhost:8000/api/v1`

## Endpoints Overview

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| POST | `/ingest` | Start ingestion job | 202 |
| GET | `/ingest/{job_id}` | Get ingestion job status | 200 |
| GET | `/jobs` | List all ingestion jobs (paginated) | 200 |
| GET | `/ingest/{job_id}/stream` | SSE event stream for a job | 200 (SSE) |
| GET | `/queue` | List pending review files | 200 |
| GET | `/queue/{file_id}` | Get review queue item detail | 200 |
| POST | `/queue/{file_id}/accept` | Approve a file | 200 |
| POST | `/queue/{file_id}/reject` | Reject a file | 200 |
| PUT | `/queue/{file_id}/update` | Update file content | 200 |
| GET | `/files` | List all files (paginated) | 200 |
| GET | `/files/{file_id}` | Get file detail | 200 |
| POST | `/files/{file_id}/revalidate` | Revalidate single file (sync) | 200 |
| POST | `/revalidate` | Start batch revalidation | 202 |
| GET | `/revalidate/{job_id}` | Get revalidation job status | 200 |
| GET | `/stats` | Aggregate statistics | 200 |

---

## Ingestion

### POST /ingest

Start a new ingestion job.

**Request:**
```json
{
  "url": "https://aem-instance/content/page.model.json",
  "region": "US",
  "brand": "BrandName"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string (URL) | Yes | AEM model.json endpoint |
| `region` | string | Yes | Geographic region (e.g. US, EU, APAC) |
| `brand` | string | Yes | Brand identifier |

**Response (202):**
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "in_progress"
}
```

### GET /ingest/{job_id}

**Response (200):**
```json
{
  "id": "a1b2c3d4-...",
  "source_url": "https://...",
  "status": "completed",
  "total_nodes_found": 12,
  "files_created": 10,
  "files_auto_approved": 6,
  "files_pending_review": 3,
  "files_auto_rejected": 1,
  "duplicates_skipped": 2,
  "error_message": null,
  "started_at": "2025-03-05T10:00:00Z",
  "completed_at": "2025-03-05T10:01:30Z"
}
```

### GET /jobs

Paginated list of all ingestion jobs.

**Query params:** `page` (default 1), `size` (default 20)

---

## Review Queue

### GET /queue

List files pending human review.

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `region` | string | Filter by region |
| `brand` | string | Filter by brand |
| `content_type` | string | Filter by content type |
| `component_type` | string | Filter by AEM component type |
| `page` | int | Page number (default 1) |
| `size` | int | Items per page (default 20) |

**Response:** Paginated list of `QueueItemSummary` objects.

### GET /queue/{file_id}

Full detail for a file in the review queue. Returns the file only if its status is `pending_review`.

### POST /queue/{file_id}/accept

**Request:**
```json
{
  "reviewed_by": "[email]"
}
```

Approves the file and triggers S3 upload in the background.

### POST /queue/{file_id}/reject

**Request:**
```json
{
  "reviewed_by": "[email]",
  "review_notes": "Reason for rejection"
}
```

### PUT /queue/{file_id}/update

**Request:**
```json
{
  "md_content": "---\ntitle: Updated\n---\n# New content"
}
```

Updates the Markdown content, recomputes the content hash, preserves current status.

---

## Files

### GET /files

List all tracked files with optional filters.

**Query params:** `status`, `region`, `brand`, `content_type`, `component_type`, `page`, `size`

### GET /files/{file_id}

Full detail for any tracked file, including validation scores, S3 metadata, and review history.

---

## Revalidation

### POST /files/{file_id}/revalidate

Synchronous single-file revalidation. Re-runs the Validator Agent and returns the updated `FileDetail`.

Score routing after revalidation:
- ≥ 0.7 → `approved` → auto-uploaded to S3 → `in_s3`
- 0.2 – 0.7 → `pending_review`
- < 0.2 → `auto_rejected`

**Errors:** 404 (not found), 502 (validation service unavailable — file unchanged)

### POST /revalidate

Start batch revalidation.

**Request:**
```json
{
  "file_ids": ["uuid-1", "uuid-2", "uuid-3"]
}
```

**Response (202):**
```json
{
  "job_id": "c3d4e5f6-...",
  "status": "in_progress"
}
```

### GET /revalidate/{job_id}

**Response (200):**
```json
{
  "id": "c3d4e5f6-...",
  "status": "completed",
  "total_files": 10,
  "completed": 7,
  "failed": 2,
  "not_found": 1,
  "error_message": null,
  "started_at": "2025-03-05T10:00:00Z",
  "completed_at": "2025-03-05T10:02:30Z"
}
```

Counter invariant: `completed + failed + not_found = total_files` when status is `completed`.

---

## Statistics

### GET /stats

```json
{
  "total_files": 1247,
  "pending_review": 83,
  "approved": 1021,
  "rejected": 143,
  "avg_score": 0.72
}
```

---

## SSE Streaming

### GET /ingest/{job_id}/stream

Opens a persistent SSE connection for real-time pipeline events. See [SSE Streaming](./sse-streaming.md) for the full event specification.

---

## Common Error Responses

| Status | Meaning |
|--------|---------|
| 404 | Resource not found |
| 422 | Validation error (missing/invalid fields) |
| 502 | Upstream service unavailable (e.g., Bedrock) |
