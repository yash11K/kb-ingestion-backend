# SSE Streaming

## Overview

The system provides real-time pipeline event streaming via Server-Sent Events (SSE). Clients connect to `GET /api/v1/ingest/{job_id}/stream` after starting an ingestion job to receive live progress updates, agent activity, and completion/error notifications.

## Connection Lifecycle

1. Client calls `POST /api/v1/ingest` → receives `job_id`
2. Client opens `GET /api/v1/ingest/{job_id}/stream`
3. Server streams events until `complete` or `error`
4. Client closes connection after terminal event

**Late joiners**: If the client connects after the pipeline has started, it receives a replay of the most recent buffered events (up to 200) before continuing with live events.

**Keepalive**: A `: keepalive\n\n` SSE comment is sent every 30 seconds of inactivity to prevent proxy/browser timeouts.

## Event Types

All events have an `event` field and a JSON `data` payload with a `timestamp` (ISO 8601 UTC).

### progress

Pipeline stage updates. Primary event for building a progress UI.

| Stage | Description | Extra Fields |
|-------|-------------|--------------|
| `started` | Pipeline kicked off | `message` |
| `extraction` | Fetching AEM content | `message` |
| `extraction_complete` | Extraction finished | `message`, `total_nodes` |
| `processing` | Processing a file | `message`, `current`, `total` |
| `duplicate_skipped` | File skipped (hash exists) | `message`, `filename` |
| `validation` | Validation started | `message`, `current`, `total` |
| `validated` | Validation complete | `message`, `filename`, `status`, `score` |
| `validation_error` | Validation failed | `message`, `filename` |
| `s3_upload` | Uploading to S3 | `message` |

### tool_call

Emitted when a Strands agent invokes a tool.

| Field | Type | Description |
|-------|------|-------------|
| `agent` | string | `"extractor"` or `"validator"` |
| `tool` | string | Tool name (e.g. `"html_to_markdown"`) |
| `message` | string | Human-readable description |

### agent_log

Streaming output from agents. Contains either `chunk` (LLM text) or `message` (status).

### complete

Terminal event. Pipeline finished successfully. Contains final counters: `files_created`, `files_auto_approved`, `files_pending_review`, `files_auto_rejected`, `duplicates_skipped`.

### error

Terminal event. Pipeline failed. Contains `message` with error description.

## Stream Manager Architecture

The `StreamManager` class manages per-job event streams:

- **`register(job_id)`**: Creates a new job stream when a pipeline starts
- **`subscribe(job_id)`**: Returns an `asyncio.Queue` for a subscriber (with replay of buffered events)
- **`publish(job_id, event, data)`**: Fans out an `SSEEvent` to all subscribers
- **`finish(job_id)`**: Marks the stream as finished
- **`cleanup(job_id)`**: Removes the stream entirely

Each job stream maintains a rolling buffer of up to 200 events for late-joiner replay.

## Frontend Integration Example

```javascript
const es = new EventSource(`/api/v1/ingest/${jobId}/stream`);

es.addEventListener("progress", (e) => {
  const data = JSON.parse(e.data);
  updateProgressBar(data.current, data.total);
});

es.addEventListener("complete", (e) => {
  const data = JSON.parse(e.data);
  showSummary(data);
  es.close();
});

es.addEventListener("error", (e) => {
  if (e.data) showError(JSON.parse(e.data).message);
  es.close();
});
```
