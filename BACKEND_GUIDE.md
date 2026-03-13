# AEM Knowledge Base Ingestion System — Backend API Guide

Base URL: `http://localhost:8000/api/v1`

---

## Ingestion

### POST /api/v1/ingest

Start a new ingestion job. Fetches content from an AEM model.json endpoint, extracts content nodes, validates them, and routes based on quality scores.

**Request Body:**
```json
{
  "url": "https://your-aem-instance/content/page.model.json",
  "region": "US",
  "brand": "YourBrand",
  "component_types": ["*/accordionitem", "*/text"]
}
```

| Field              | Type       | Required | Description                                                                                      |
|--------------------|------------|----------|--------------------------------------------------------------------------------------------------|
| `url`              | string     | Yes      | AEM model.json endpoint URL                                                                      |
| `region`           | string     | Yes      | Geographic region (e.g. US, EU, APAC)                                                            |
| `brand`            | string     | Yes      | Brand identifier                                                                                 |
| `component_types`  | list[str]  | No       | Optional. Overrides the server default allowlist of AEM component types to extract. When omitted, falls back to `DEFAULT_COMPONENT_TYPES` from server config. |

**Response (202 Accepted):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "in_progress"
}
```

**Errors:**
- `422` — Missing or invalid `url`, `region`, or `brand`

---

### GET /api/v1/ingest/{job_id}

Get the status and counters for an ingestion job.

**Path Parameters:**
| Param    | Type | Description          |
|----------|------|----------------------|
| `job_id` | UUID | The ingestion job ID |

**Response (200):**
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "source_url": "https://your-aem-instance/content/page.model.json",
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

**Job statuses:** `in_progress`, `completed`, `failed`

**Errors:**
- `404` — Job not found

---

## Review Queue

### GET /api/v1/queue

List files pending human review. Only returns files with status `pending_review`.

**Query Parameters:**
| Param            | Type   | Default    | Description                                                              |
|------------------|--------|------------|--------------------------------------------------------------------------|
| `region`         | string | —          | Filter by region                                                         |
| `brand`          | string | —          | Filter by brand                                                          |
| `content_type`   | string | —          | Filter by content type                                                   |
| `component_type` | string | —          | Filter by AEM component type                                             |
| `sort_by`        | string | created_at | Field to sort by. Options: `created_at`, `validation_score`, `title`     |
| `sort_order`     | string | desc       | Sort direction: `asc` or `desc`                                          |
| `page`           | int    | 1          | Page number                                                              |
| `size`           | int    | 20         | Items per page                                                           |

**Response (200):**
```json
{
  "items": [
    {
      "id": "...",
      "filename": "faq-how-to-reset.md",
      "title": "How to Reset Your Password",
      "content_type": "faq",
      "component_type": "core/components/text",
      "region": "US",
      "brand": "Acme",
      "validation_score": 0.55,
      "created_at": "2025-03-05T10:00:00Z"
    }
  ],
  "total": 25,
  "page": 1,
  "size": 20,
  "pages": 2
}
```

---

### GET /api/v1/queue/{file_id}

Get full details for a file in the review queue. Returns the file regardless of its current status, so reviewers can inspect files that have been auto-approved or auto-rejected.

**Response (200):**
```json
{
  "id": "...",
  "filename": "faq-how-to-reset.md",
  "title": "How to Reset Your Password",
  "content_type": "faq",
  "component_type": "core/components/text",
  "source_url": "https://aem.example.com/content/page.model.json",
  "aem_node_id": "/root/items/text_1",
  "md_content": "---\ntitle: How to Reset...\n---\n# How to Reset...",
  "region": "US",
  "brand": "Acme",
  "status": "pending_review",
  "validation_score": 0.55,
  "validation_breakdown": {
    "metadata_completeness": 0.25,
    "semantic_quality": 0.2,
    "uniqueness": 0.1
  },
  "validation_issues": ["Missing modify_date field"],
  "created_at": "2025-03-05T10:00:00Z",
  "updated_at": "2025-03-05T10:00:00Z"
}
```

**Errors:**
- `404` — File not found

> **Note:** The queue list endpoint (`GET /queue`) still only returns `pending_review` files. The detail endpoint returns any file so reviewers can inspect files in any state. The accept and reject action endpoints still enforce `pending_review` status.

---

### POST /api/v1/queue/{file_id}/accept

Approve a file from the review queue. Triggers S3 upload in the background.

**Request Body:**
```json
{
  "reviewed_by": "reviewer@example.com",
  "review_notes": "Looks good, minor formatting acceptable."
}
```

| Field          | Type   | Required | Description                                      |
|----------------|--------|----------|--------------------------------------------------|
| `reviewed_by`  | string | Yes      | Email or identifier of the reviewer              |
| `review_notes` | string | No       | Optional notes for audit trail consistency        |

**Response (200):**
```json
{
  "file_id": "...",
  "status": "approved",
  "message": "File accepted and S3 upload triggered"
}
```

**Errors:**
- `404` — File not found or not in `pending_review` status

---

### POST /api/v1/queue/{file_id}/reject

Reject a file from the review queue.

**Request Body:**
```json
{
  "reviewed_by": "reviewer@example.com",
  "review_notes": "Content is too short and lacks context"
}
```

**Response (200):**
```json
{
  "file_id": "...",
  "status": "rejected",
  "message": "File rejected"
}
```

**Errors:**
- `404` — File not found or not in `pending_review` status
- `422` — Missing `reviewed_by` or `review_notes`

---

### PUT /api/v1/queue/{file_id}/update

Update the markdown content of a file. Recomputes the content hash automatically. Preserves the current file status.

**Request Body:**
```json
{
  "md_content": "---\ntitle: Updated Title\n---\n# Updated content here",
  "reviewed_by": "editor@example.com"
}
```

| Field         | Type   | Required | Description                                      |
|---------------|--------|----------|--------------------------------------------------|
| `md_content`  | string | Yes      | Updated markdown content (with frontmatter)      |
| `reviewed_by` | string | Yes      | Email or identifier of the person making the edit |

**Response (200):**
```json
{
  "file_id": "...",
  "status": "pending_review",
  "message": "File content updated"
}
```

**Errors:**
- `404` — File not found
- `422` — Missing required `md_content` or `reviewed_by`

---

## Files

### GET /api/v1/stats

Returns aggregate statistics across all tracked files.

**Response (200):**
```json
{
  "total_files": 1247,
  "pending_review": 83,
  "approved": 1021,
  "rejected": 143,
  "avg_score": 0.72
}
```

| Field            | Type   | Description                                              |
|------------------|--------|----------------------------------------------------------|
| `total_files`    | int    | Total count of all files in the system                   |
| `pending_review` | int    | Files awaiting human review                              |
| `approved`       | int    | Files that have been accepted (includes `in_s3`)         |
| `rejected`       | int    | Files rejected (both manual `rejected` and `auto_rejected`) |
| `avg_score`      | float  | Average validation score across all files (0.0–1.0)      |

---

### GET /api/v1/files

List all tracked files with optional filters.

**Query Parameters:**
| Param            | Type   | Default    | Description                                                                                      |
|------------------|--------|------------|--------------------------------------------------------------------------------------------------|
| `status`         | string | —          | Filter by status: `pending_validation`, `approved`, `pending_review`, `auto_rejected`, `in_s3`, `rejected` |
| `region`         | string | —          | Filter by region                                                                                 |
| `brand`          | string | —          | Filter by brand                                                                                  |
| `content_type`   | string | —          | Filter by content type                                                                           |
| `component_type` | string | —          | Filter by AEM component type                                                                     |
| `sort_by`        | string | created_at | Field to sort by. Options: `created_at`, `validation_score`, `title`                             |
| `sort_order`     | string | desc       | Sort direction: `asc` or `desc`                                                                  |
| `page`           | int    | 1          | Page number                                                                                      |
| `size`           | int    | 20         | Items per page                                                                                   |

**Response (200):**
```json
{
  "items": [
    {
      "id": "...",
      "filename": "faq-how-to-reset.md",
      "title": "How to Reset Your Password",
      "content_type": "faq",
      "status": "in_s3",
      "region": "US",
      "brand": "Acme",
      "validation_score": 0.85,
      "created_at": "2025-03-05T10:00:00Z"
    }
  ],
  "total": 100,
  "page": 1,
  "size": 20,
  "pages": 5
}
```

---

### GET /api/v1/files/{file_id}

Get full details for any tracked file.

**Response (200):**
```json
{
  "id": "...",
  "filename": "faq-how-to-reset.md",
  "title": "How to Reset Your Password",
  "content_type": "faq",
  "content_hash": "a1b2c3...",
  "source_url": "https://aem.example.com/content/page.model.json",
  "component_type": "core/components/text",
  "aem_node_id": "/root/items/text_1",
  "md_content": "---\ntitle: ...\n---\n# Content...",
  "modify_date": "2025-01-15T10:00:00Z",
  "parent_context": "/root/items",
  "region": "US",
  "brand": "Acme",
  "validation_score": 0.85,
  "validation_breakdown": {
    "metadata_completeness": 0.3,
    "semantic_quality": 0.35,
    "uniqueness": 0.2
  },
  "validation_issues": [],
  "status": "in_s3",
  "s3_bucket": "my-kb-bucket",
  "s3_key": "knowledge-base/faq/2025-03/faq-how-to-reset.md",
  "s3_uploaded_at": "2025-03-05T10:01:00Z",
  "reviewed_by": null,
  "reviewed_at": null,
  "review_notes": null,
  "created_at": "2025-03-05T10:00:00Z",
  "updated_at": "2025-03-05T10:01:00Z"
}
```

**Errors:**
- `404` — File not found

---

## Revalidation

### POST /api/v1/files/{file_id}/revalidate

Re-run validation on a single KB file. This is synchronous — the response contains the updated file detail with new scores and status.

**Path Parameters:**
| Param     | Type | Description     |
|-----------|------|-----------------|
| `file_id` | UUID | The KB file ID  |

**Response (200):**

Returns the full `FileDetail` object (same shape as `GET /files/{file_id}`) with updated validation fields and status.

```json
{
  "id": "...",
  "filename": "faq-how-to-reset.md",
  "title": "How to Reset Your Password",
  "content_type": "faq",
  "content_hash": "a1b2c3...",
  "source_url": "https://aem.example.com/content/page.model.json",
  "component_type": "core/components/text",
  "aem_node_id": "/root/items/text_1",
  "md_content": "---\ntitle: ...\n---\n# Content...",
  "modify_date": "2025-01-15T10:00:00Z",
  "parent_context": "/root/items",
  "region": "US",
  "brand": "Acme",
  "doc_type": "FAQ",
  "validation_score": 0.85,
  "validation_breakdown": {
    "metadata_completeness": 0.3,
    "semantic_quality": 0.35,
    "uniqueness": 0.2
  },
  "validation_issues": [],
  "status": "in_s3",
  "s3_bucket": "my-kb-bucket",
  "s3_key": "knowledge-base/faq/2025-03/faq-how-to-reset.md",
  "s3_uploaded_at": "2025-03-05T10:01:00Z",
  "reviewed_by": null,
  "reviewed_at": null,
  "review_notes": null,
  "created_at": "2025-03-05T10:00:00Z",
  "updated_at": "2025-03-05T10:02:00Z"
}
```

**Status routing after revalidation:**
- Score ≥ `AUTO_APPROVE_THRESHOLD` (0.7) → `approved` → auto-uploaded to S3 → `in_s3`
- Score ≥ `AUTO_REJECT_THRESHOLD` (0.2) and < `AUTO_APPROVE_THRESHOLD` → `pending_review`
- Score < `AUTO_REJECT_THRESHOLD` (0.2) → `auto_rejected`

**Errors:**
- `404` — File not found
- `502` — Validation service unavailable (file record left unchanged)

> **Note:** This endpoint works on files in any status. The file's previous validation scores are overwritten with the new results.

---

### POST /api/v1/revalidate

Start a batch revalidation job. Returns immediately with a job ID while files are processed in the background.

**Request Body:**
```json
{
  "file_ids": [
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "b2c3d4e5-f6a7-8901-bcde-f12345678901"
  ]
}
```

| Field      | Type        | Required | Description                              |
|------------|-------------|----------|------------------------------------------|
| `file_ids` | list[UUID]  | Yes      | Non-empty list of KB file IDs to revalidate |

**Response (202 Accepted):**
```json
{
  "job_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
  "status": "in_progress"
}
```

**Errors:**
- `422` — Empty `file_ids` list, missing field, or invalid UUIDs

**Batch behavior:**
- Files that don't exist are skipped (increments `not_found` counter)
- Files that fail validation are skipped (increments `failed` counter)
- Each successfully revalidated file follows the same score-routing logic as single-file revalidation

---

### GET /api/v1/revalidate/{job_id}

Get the status and progress of a batch revalidation job.

**Path Parameters:**
| Param    | Type | Description              |
|----------|------|--------------------------|
| `job_id` | UUID | The revalidation job ID  |

**Response (200):**
```json
{
  "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
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

| Field          | Type           | Description                                                    |
|----------------|----------------|----------------------------------------------------------------|
| `id`           | UUID           | Job identifier                                                 |
| `status`       | string         | `in_progress`, `completed`, or `failed`                        |
| `total_files`  | int            | Number of file IDs submitted in the request                    |
| `completed`    | int            | Files successfully revalidated                                 |
| `failed`       | int            | Files where validation raised an error                         |
| `not_found`    | int            | File IDs that didn't exist in the database                     |
| `error_message`| string or null | Set when the entire job fails (e.g. DB connection lost)        |
| `started_at`   | datetime       | When the job was created                                       |
| `completed_at` | datetime or null | When the job finished (null while `in_progress`)             |

**Counter invariant:** `completed + failed + not_found = total_files` when status is `completed`.

**Errors:**
- `404` — Revalidation job not found

---

## File Status Lifecycle

```
pending_validation → approved (score >= 0.7)
pending_validation → pending_review (0.2 <= score < 0.7)
pending_validation → auto_rejected (score < 0.2)
approved → in_s3 (after S3 upload)
pending_review → approved (human accepts)
pending_review → rejected (human rejects)

Revalidation (any status):
  * → approved → in_s3 (score >= 0.7, auto-uploads to S3)
  * → pending_review (0.2 <= score < 0.7)
  * → auto_rejected (score < 0.2)
```

## Validation Scoring

| Category                | Range     | Description                                    |
|-------------------------|-----------|------------------------------------------------|
| `metadata_completeness` | 0.0 – 0.3 | Presence/validity of all 10 frontmatter fields |
| `semantic_quality`      | 0.0 – 0.5 | Content coherence, readability, completeness   |
| `uniqueness`            | 0.0 – 0.2 | 0.2 if unique, 0.0 if duplicate content hash   |
| **Total score**         | 0.0 – 1.0 | Sum of the three sub-scores                    |

### Server Configuration

The auto-approve threshold (default 0.7) and auto-reject threshold (default 0.2) are configurable via environment variables:

- `AUTO_APPROVE_THRESHOLD` — Score at or above this value triggers auto-approval (default: `0.7`)
- `AUTO_REJECT_THRESHOLD` — Score below this value triggers auto-rejection (default: `0.2`)

Files scoring at or above `AUTO_APPROVE_THRESHOLD` are auto-approved, files scoring below `AUTO_REJECT_THRESHOLD` are auto-rejected, and files in between are routed to `pending_review`.
