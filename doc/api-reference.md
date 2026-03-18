# API Reference

Base URL: `http://localhost:8000/api/v1`

## Endpoints Overview

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| POST | `/ingest` | Start batch ingestion | 202 |
| GET | `/ingest/{job_id}` | Get ingestion job status | 200 |
| GET | `/jobs` | List all ingestion jobs (paginated) | 200 |
| GET | `/ingest/{job_id}/stream` | SSE event stream for a job | 200 (SSE) |
| GET | `/sources` | List all sources (paginated) | 200 |
| GET | `/sources/active-jobs` | Active jobs by source | 200 |
| GET | `/sources/{source_id}` | Get source detail | 200 |
| GET | `/sources/{source_id}/jobs` | List jobs for a source | 200 |
| POST | `/sources/{source_id}/ingest` | Re-ingest an existing source | 202 |
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
| GET | `/nav/tree` | Parse AEM model.json into nav tree | 200 |
| GET | `/deep-links` | List all deep links (paginated) | 200 |
| GET | `/deep-links/{source_id}` | List deep links for a source | 200 |
| POST | `/deep-links/{source_id}/confirm` | Confirm deep links for ingestion | 202 |
| POST | `/deep-links/{source_id}/dismiss` | Dismiss deep links | 200 |
| POST | `/kb/search` | Full-text KB search (SSE) | 200 (SSE) |
| POST | `/kb/chat` | RAG chat with KB context (SSE) | 200 (SSE) |

---

## Ingestion

### POST /ingest

Start a batch ingestion job. One job is created per URL, each linked to its own source.

**Request:**
```json
{
  "urls": [
    "https://aem-instance/content/page-a.model.json",
    "https://aem-instance/content/page-b.model.json"
  ],
  "nav_root_url": "https://aem-instance/content/home.model.json",
  "nav_metadata": {
    "https://aem-instance/content/page-a.model.json": {
      "label": "Page A",
      "section": "Main Nav",
      "page_path": "/content/page-a"
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `urls` | list[string (URL)] | Yes | One or more AEM model.json endpoints |
| `nav_root_url` | string | No | Home page URL this nav tree came from |
| `nav_metadata` | object | No | Per-URL metadata: `{url: {label, section, page_path}}` |

Region and brand are auto-inferred from each URL.

**Response (202):**
```json
{
  "jobs": [
    {
      "source_id": "a1b2c3d4-...",
      "job_id": "e5f6a7b8-...",
      "url": "https://aem-instance/content/page-a.model.json"
    }
  ],
  "status": "in_progress"
}
```

### GET /ingest/{job_id}

**Response (200):**
```json
{
  "id": "a1b2c3d4-...",
  "source_id": "b2c3d4e5-...",
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
  "completed_at": "2025-03-05T10:01:30Z",
  "child_urls": ["https://..."],
  "max_depth": 0,
  "pages_crawled": 1,
  "current_depth": 0
}
```

### GET /jobs

Paginated list of all ingestion jobs.

**Query params:** `page` (default 1), `size` (default 20)

---

## Sources

### GET /sources

Paginated list of all ingestion sources.

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `region` | string | Filter by region |
| `brand` | string | Filter by brand |
| `page` | int | Page number (default 1) |
| `size` | int | Items per page (default 20) |

**Response item:**
```json
{
  "id": "uuid",
  "url": "https://...",
  "region": "US",
  "brand": "BrandName",
  "nav_label": "Page Label",
  "nav_section": "Main Nav",
  "last_ingested_at": "2025-03-05T10:00:00Z",
  "created_at": "2025-03-01T08:00:00Z"
}
```

### GET /sources/active-jobs

Returns a map of `{source_id: job_id}` for all sources with in-progress jobs.

**Response (200):**
```json
{
  "source-uuid-1": "job-uuid-1",
  "source-uuid-2": "job-uuid-2"
}
```

### GET /sources/{source_id}

Source detail with aggregate job and file stats.

**Response (200):**
```json
{
  "id": "uuid",
  "url": "https://...",
  "region": "US",
  "brand": "BrandName",
  "last_ingested_at": "2025-03-05T10:00:00Z",
  "created_at": "2025-03-01T08:00:00Z",
  "updated_at": "2025-03-05T10:01:30Z",
  "total_jobs": 5,
  "completed_jobs": 4,
  "failed_jobs": 0,
  "active_jobs": 1,
  "total_files": 42,
  "pending_review": 3,
  "approved": 37,
  "rejected": 2
}
```

### GET /sources/{source_id}/jobs

Paginated ingestion jobs for a specific source.

**Query params:** `page` (default 1), `size` (default 20)

### POST /sources/{source_id}/ingest

Trigger a new ingestion job for an existing source. Region and brand are inherited from the source record.

**Response (202):**
```json
{
  "source_id": "uuid",
  "job_id": "uuid",
  "status": "in_progress"
}
```

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

**Query params:** `status`, `region`, `brand`, `content_type`, `component_type`, `source_id`, `page`, `size`

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

## Navigation & Deep Links

### GET /nav/tree

Parse an AEM model.json URL into a navigation tree for source selection. Results are cached for 24 hours.

**Query params:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | AEM model.json URL (e.g. home page) |
| `force_refresh` | bool | No | Bypass cache (default false) |

**Response (200):**
```json
{
  "brand": "BrandName",
  "region": "US",
  "base_url": "https://...",
  "sections": [
    {
      "section_name": "Hamburger Menu",
      "nodes": [
        {
          "label": "Products",
          "url": "https://...",
          "model_json_url": "https://...model.json",
          "is_external": false,
          "children": []
        }
      ]
    }
  ]
}
```

### GET /deep-links

List all deep links across all sources with optional status filter and pagination.

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter by status: `pending`, `confirmed`, `dismissed`, `ingested` |
| `page` | int | Page number (default 1) |
| `size` | int | Items per page (default 50, max 100) |

**Response item:**
```json
{
  "id": "uuid",
  "source_id": "uuid",
  "url": "https://...",
  "model_json_url": "https://...model.json",
  "anchor_text": "Link text",
  "found_in_node": "node-id",
  "found_in_page": "https://...",
  "status": "pending",
  "created_at": "2025-03-05T10:00:00Z"
}
```

### GET /deep-links/{source_id}

List deep links for a specific source, filtered by status.

**Query params:** `status` (default `pending`)

### POST /deep-links/{source_id}/confirm

Confirm selected deep links and start one ingestion job per link.

**Request:**
```json
{
  "link_ids": ["uuid-1", "uuid-2"]
}
```

**Response (200):**
```json
{
  "jobs": [
    { "source_id": "uuid", "job_id": "uuid-job-1", "url": "https://example.com/page-1.model.json" },
    { "source_id": "uuid", "job_id": "uuid-job-2", "url": "https://example.com/page-2.model.json" }
  ],
  "status": "in_progress"
}
```

### POST /deep-links/{source_id}/dismiss

Dismiss selected deep links.

**Request:**
```json
{
  "link_ids": ["uuid-1", "uuid-2"]
}
```

**Response (200):**
```json
{
  "dismissed": 2
}
```

---

## Knowledge Base

### POST /kb/search

Full-text search across the knowledge base. Results are streamed as SSE events.

**Request:**
```json
{
  "query": "search terms",
  "limit": 10
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Search query (1–1000 chars) |
| `limit` | int | No | Max results (1–50, default 10) |

**Response:** SSE stream of ranked search results.

### POST /kb/chat

RAG endpoint — retrieves relevant KB context then streams a Bedrock-generated response as SSE.

**Request:**
```json
{
  "query": "your question here",
  "context_limit": 5
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Chat query (1–2000 chars) |
| `context_limit` | int | No | Max context documents (1–20, default 5) |

**Response:** SSE stream of generated tokens.

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
| 502 | Upstream service unavailable (e.g., Bedrock, AEM) |
