# SSE Streaming

## Overview

The system has two SSE stream families:

1. **Ingestion pipeline stream** — `GET /api/v1/ingest/{job_id}/stream` — real-time progress for ingestion jobs
2. **Knowledge Base streams** — `POST /api/v1/kb/search` and `POST /api/v1/kb/chat` — streamed search results and RAG generation

All SSE events follow the standard format:

```
event: <event_type>
data: {"key": "value", "timestamp": "2025-03-05T10:00:00Z"}
```

---

## 1. Ingestion Pipeline Stream

### Connection

```
GET /api/v1/ingest/{job_id}/stream
```

- Connect after calling `POST /api/v1/ingest` (which returns `job_id`)
- Late joiners get a replay of up to 200 buffered events
- A `: keepalive` comment is sent every 30s of inactivity
- Connection closes after a `complete` or `error` event

### Event Types

#### `queued`

Job is waiting for a concurrency slot.

```json
{
  "stage": "queued",
  "message": "Job queued — waiting for available slot (3 URL(s))",
  "timestamp": "..."
}
```

#### `progress`

The main event type. The `stage` field tells you what's happening:

| stage | meaning | extra fields |
|-------|---------|--------------|
| `started` | Pipeline acquired a slot and began | `message` |
| `fetch` | Fetching AEM JSON from a URL | `message` |
| `discovery` | Haiku agent discovering content + links | `message` |
| `extraction` | Sonnet agent extracting markdown files | `message` |
| `extraction_complete` | Extraction finished for a URL | `message`, `total_nodes` |
| `processing` | Starting to process a file | `message`, `current`, `total` |
| `validation` | Validator agent running on a file | `message`, `current`, `total` |
| `validated` | Validation done, file routed | `message`, `filename`, `status`, `score` |
| `s3_upload` | Uploading approved file to S3 | `message` |

`status` in `validated` is one of: `approved`, `pending_review`, `auto_rejected`.

```json
{
  "stage": "validated",
  "message": "faq-page.md → approved (score: 0.85)",
  "filename": "faq-page.md",
  "status": "approved",
  "score": 0.85,
  "timestamp": "..."
}
```

```json
{
  "stage": "processing",
  "message": "Processing file 3/10: terms-of-use.md",
  "current": 3,
  "total": 10,
  "timestamp": "..."
}
```

#### `crawl_page_start`

Emitted when the pipeline begins processing a URL.

```json
{
  "url": "https://...",
  "depth": 0,
  "page_index": 1,
  "timestamp": "..."
}
```

#### `crawl_page_complete`

Emitted when a single URL finishes processing.

```json
{
  "url": "https://...",
  "depth": 0,
  "files_extracted": 5,
  "deep_links_found": 3,
  "timestamp": "..."
}
```

#### `crawl_page_error`

A URL failed during processing. Non-terminal — other URLs continue.

```json
{
  "url": "https://...",
  "error": "AEM endpoint returned HTTP 500",
  "timestamp": "..."
}
```

#### `crawl_summary`

Emitted once after all URLs are processed, before `complete`.

```json
{
  "total_pages": 5,
  "total_files": 23,
  "failed_count": 1,
  "deep_links_discovered": 8,
  "timestamp": "..."
}
```

#### `deep_links_discovered`

Emitted when embedded links were found in content. Only fires if count > 0.

```json
{
  "count": 8,
  "message": "Discovered 8 embedded link(s) in content. Review them in the source detail page.",
  "timestamp": "..."
}
```

#### `extraction_batching`

Extraction is splitting content into batches for the LLM.

```json
{
  "total_batches": 3,
  "total_nodes": 15,
  "timestamp": "..."
}
```

#### `extraction_batch_start`

A batch is starting extraction.

```json
{
  "batch_index": 2,
  "total_batches": 3,
  "timestamp": "..."
}
```

#### `extraction_complete`

All extraction batches finished.

```json
{
  "total_results": 15,
  "timestamp": "..."
}
```

#### `tool_call`

An agent invoked a tool.

```json
{
  "agent": "extractor",
  "tool": "html_to_markdown",
  "message": "Converting HTML node to markdown",
  "timestamp": "..."
}
```

`agent` is `"extractor"` or `"validator"`.

#### `agent_log`

Streaming LLM text from an agent.

```json
{
  "agent": "extractor",
  "chunk": "Analyzing the content structure...",
  "timestamp": "..."
}
```

#### `complete`

Terminal event. Pipeline finished successfully.

```json
{
  "message": "Pipeline completed",
  "files_created": 10,
  "files_auto_approved": 6,
  "files_pending_review": 3,
  "files_auto_rejected": 1,
  "duplicates_skipped": 2,
  "timestamp": "..."
}
```

#### `error`

Terminal event. Pipeline failed.

```json
{
  "message": "AEM endpoint returned HTTP 500: ...",
  "timestamp": "..."
}
```

### Terminal Events

Only `complete` and `error` are terminal — close the EventSource after receiving either one. All other events are informational.

### Event Flow (typical)

```
queued
  → progress (started)
    → crawl_page_start
      → progress (fetch)
      → progress (discovery)
      → progress (extraction)
      → extraction_batching
      → extraction_batch_start (×N)
      → extraction_complete
      → progress (extraction_complete)
      → progress (processing) ×N
        → progress (validation)
        → tool_call / agent_log (interleaved)
        → progress (validated)
        → progress (s3_upload)  [if approved]
    → crawl_page_complete
  → crawl_summary
  → deep_links_discovered  [if any]
→ complete
```

---

## 2. Knowledge Base Streams

Both KB endpoints return SSE via `Content-Type: text/event-stream`. These are standard HTTP responses (not EventSource-compatible long-lived connections) — use `fetch()` with a streaming body reader.

### POST /kb/search

Streams ranked search results.

#### Events

| event | payload | description |
|-------|---------|-------------|
| `search_start` | `{query, total}` | Search began, `total` results found |
| `result` | see below | One search result |
| `search_end` | `{query, total}` | All results sent |
| `error` | `{message}` | Search failed |

`result` payload (local Postgres mode):

```json
{
  "id": "uuid",
  "title": "Page Title",
  "filename": "page-title.md",
  "content_type": "faq",
  "component_type": "text",
  "doc_type": "FAQ",
  "source_url": "https://...",
  "region": "US",
  "brand": "BrandName",
  "md_content": "# Full markdown content...",
  "rank": 0.85
}
```

`result` payload (Bedrock KB mode):

```json
{
  "content": "Retrieved text chunk...",
  "s3_uri": "s3://bucket/key",
  "score": 0.92,
  "metadata": {}
}
```

### POST /kb/chat

RAG endpoint — retrieves context then streams generated text.

#### Events

| event | payload | description |
|-------|---------|-------------|
| `sources` | `{query, sources[]}` | Retrieved context sources |
| `token` | `{text}` | Generated text chunk (may arrive many times) |
| `done` | `{query}` | Generation complete |
| `error` | `{message}` | Generation failed |

`sources` payload:

```json
{
  "query": "how do I reset my password?",
  "sources": [
    {
      "id": "uuid",
      "title": "Account Recovery",
      "source_url": "https://..."
    }
  ]
}
```

`token` events arrive incrementally — concatenate `text` values to build the full response.

### Frontend Integration

```javascript
// Ingestion pipeline
const es = new EventSource(`/api/v1/ingest/${jobId}/stream`);

es.addEventListener("progress", (e) => {
  const { stage, current, total, message } = JSON.parse(e.data);
  updateProgress(stage, current, total, message);
});

es.addEventListener("crawl_page_complete", (e) => {
  const { url, files_extracted, deep_links_found } = JSON.parse(e.data);
  addPageResult(url, files_extracted, deep_links_found);
});

es.addEventListener("deep_links_discovered", (e) => {
  const { count } = JSON.parse(e.data);
  showDeepLinksNotification(count);
});

es.addEventListener("complete", (e) => {
  showSummary(JSON.parse(e.data));
  es.close();
});

es.addEventListener("error", (e) => {
  if (e.data) showError(JSON.parse(e.data).message);
  es.close();
});


// KB Chat (fetch-based streaming)
async function streamChat(query) {
  const resp = await fetch("/api/v1/kb/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, context_limit: 5 }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    let currentEvent = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) currentEvent = line.slice(7);
      else if (line.startsWith("data: ")) {
        const data = JSON.parse(line.slice(6));
        if (currentEvent === "sources") renderSources(data.sources);
        else if (currentEvent === "token") appendToken(data.text);
        else if (currentEvent === "done") onChatComplete();
        else if (currentEvent === "error") showError(data.message);
      }
    }
  }
}
```
