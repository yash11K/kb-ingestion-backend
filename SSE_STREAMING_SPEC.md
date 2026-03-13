# SSE Streaming — Pipeline Event Stream Spec

Endpoint for streaming real-time pipeline events (agent logs, tool calls, progress updates) to the frontend via Server-Sent Events.

---

## Endpoint

### GET /api/v1/ingest/{job_id}/stream

Opens a persistent SSE connection that streams events for the duration of an ingestion pipeline run.

**Path Parameters:**
| Param    | Type | Description          |
|----------|------|----------------------|
| `job_id` | UUID | The ingestion job ID |

**Response:** `200 OK` with `Content-Type: text/event-stream`

**Headers returned:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

**Errors:**
- `404` — No active stream for this job (pipeline already completed or job doesn't exist)

---

## Connection Lifecycle

1. Client calls `POST /api/v1/ingest` → receives `job_id`
2. Client immediately opens `GET /api/v1/ingest/{job_id}/stream`
3. Server streams events until a `complete` or `error` event is sent
4. Client should close the connection after receiving a terminal event

**Late joiners:** If the frontend connects after the pipeline has already started, it receives a replay of the most recent buffered events (up to 200) before continuing with live events.

**Keepalive:** If no events are emitted for 30 seconds, the server sends a `: keepalive\n\n` SSE comment to prevent proxy/browser timeouts. These are not dispatched as events by `EventSource` — they're transparent.

---

## Event Types

Every SSE message has an `event` field and a JSON `data` payload. All payloads include a `timestamp` field (ISO 8601 UTC).

### `progress`

Pipeline-level status updates. This is the primary event for building a progress UI.

**Stages and their payloads:**

| `stage`              | Description                              | Extra fields                                      |
|----------------------|------------------------------------------|---------------------------------------------------|
| `started`            | Pipeline kicked off                      | `message`                                         |
| `extraction`         | Fetching and extracting AEM content      | `message`                                         |
| `extraction_complete`| Extraction finished                      | `message`, `total_nodes: int`                     |
| `processing`         | Starting to process a file               | `message`, `current: int`, `total: int`           |
| `duplicate_skipped`  | File skipped (content hash already exists)| `message`, `filename: str`                        |
| `validation`         | Validation started for a file            | `message`, `current: int`, `total: int`           |
| `validated`          | Validation complete, file routed         | `message`, `filename`, `status`, `score: float`   |
| `validation_error`   | Validation failed for a file             | `message`, `filename`                             |
| `s3_upload`          | Uploading approved file to S3            | `message`                                         |

**Example:**
```
event: progress
data: {"stage":"processing","message":"Processing file 3/12: faq-reset-password.md","current":3,"total":12,"timestamp":"2025-03-05T10:00:05Z"}
```

### `tool_call`

Emitted when a Strands agent invokes a tool. Useful for showing agent activity in the UI.

| Field     | Type   | Description                                          |
|-----------|--------|------------------------------------------------------|
| `agent`   | string | Which agent: `"extractor"` or `"validator"`          |
| `tool`    | string | Tool name (e.g. `"html_to_markdown"`, `"check_duplicate"`, `"parse_frontmatter"`, `"generate_md_file"`) |
| `message` | string | Human-readable description                           |

**Example:**
```
event: tool_call
data: {"agent":"extractor","tool":"html_to_markdown","message":"Extractor agent calling tool: html_to_markdown","timestamp":"2025-03-05T10:00:02Z"}
```

### `agent_log`

Streaming output from the Strands agents. Can be text chunks or status messages.

| Field     | Type   | Description                                          |
|-----------|--------|------------------------------------------------------|
| `agent`   | string | `"extractor"` or `"validator"`                       |
| `chunk`   | string | *(optional)* Streaming text chunk from the LLM       |
| `message` | string | *(optional)* Status message (e.g. "completed response") |

One of `chunk` or `message` will be present.

**Example (text chunk):**
```
event: agent_log
data: {"agent":"extractor","chunk":"Processing node /root/items/text_1...","timestamp":"2025-03-05T10:00:03Z"}
```

**Example (status):**
```
event: agent_log
data: {"agent":"validator","message":"Validator agent completed response","timestamp":"2025-03-05T10:00:08Z"}
```

### `complete`

Terminal event. Pipeline finished successfully. Close the connection after receiving this.

| Field                  | Type | Description                    |
|------------------------|------|--------------------------------|
| `message`              | str  | `"Pipeline completed"`         |
| `files_created`        | int  | Total files inserted into DB   |
| `files_auto_approved`  | int  | Files auto-approved (score ≥ 0.7) |
| `files_pending_review` | int  | Files routed to review queue   |
| `files_auto_rejected`  | int  | Files auto-rejected (score < 0.2) |
| `duplicates_skipped`   | int  | Files skipped as duplicates    |

**Example:**
```
event: complete
data: {"message":"Pipeline completed","files_created":10,"files_auto_approved":6,"files_pending_review":3,"files_auto_rejected":1,"duplicates_skipped":2,"timestamp":"2025-03-05T10:01:30Z"}
```

### `error`

Terminal event. Pipeline failed. Close the connection after receiving this.

| Field     | Type   | Description         |
|-----------|--------|---------------------|
| `message` | string | Error description   |

**Example:**
```
event: error
data: {"message":"Request to https://aem.example.com/page.model.json timed out after 30 seconds","timestamp":"2025-03-05T10:00:10Z"}
```

---

## Typical Event Sequence

For a job processing 3 content nodes where 1 is a duplicate:

```
event: progress     → stage: started
event: progress     → stage: extraction
event: tool_call    → agent: extractor, tool: html_to_markdown
event: agent_log    → agent: extractor, chunk: "..."
event: tool_call    → agent: extractor, tool: generate_md_file
event: tool_call    → agent: extractor, tool: html_to_markdown
event: tool_call    → agent: extractor, tool: generate_md_file
event: tool_call    → agent: extractor, tool: html_to_markdown
event: tool_call    → agent: extractor, tool: generate_md_file
event: agent_log    → agent: extractor, message: "completed response"
event: progress     → stage: extraction_complete, total_nodes: 3
event: progress     → stage: processing, current: 1, total: 3
event: progress     → stage: duplicate_skipped, filename: "..."
event: progress     → stage: processing, current: 2, total: 3
event: progress     → stage: validation
event: tool_call    → agent: validator, tool: parse_frontmatter
event: tool_call    → agent: validator, tool: check_duplicate
event: agent_log    → agent: validator, message: "completed response"
event: progress     → stage: validated, status: approved, score: 0.85
event: progress     → stage: s3_upload
event: progress     → stage: processing, current: 3, total: 3
event: progress     → stage: validation
event: tool_call    → agent: validator, tool: parse_frontmatter
event: tool_call    → agent: validator, tool: check_duplicate
event: progress     → stage: validated, status: pending_review, score: 0.55
event: complete     → files_created: 2, files_auto_approved: 1, ...
```

---

## Frontend Integration

### Using EventSource (recommended)

```javascript
const jobId = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const es = new EventSource(`/api/v1/ingest/${jobId}/stream`);

es.addEventListener("progress", (e) => {
  const data = JSON.parse(e.data);
  // data.stage, data.message, data.current, data.total, etc.
  updateProgressUI(data);
});

es.addEventListener("tool_call", (e) => {
  const data = JSON.parse(e.data);
  // data.agent, data.tool, data.message
  appendToActivityLog(data);
});

es.addEventListener("agent_log", (e) => {
  const data = JSON.parse(e.data);
  // data.agent, data.chunk or data.message
  appendToAgentOutput(data);
});

es.addEventListener("complete", (e) => {
  const data = JSON.parse(e.data);
  // data.files_created, data.files_auto_approved, etc.
  showCompletionSummary(data);
  es.close();
});

es.addEventListener("error", (e) => {
  // SSE spec: error can fire for connection issues OR server-sent error events
  if (e.data) {
    const data = JSON.parse(e.data);
    showError(data.message);
  }
  es.close();
});
```

### Notes for frontend

- The `progress` events with `current` and `total` fields are designed for progress bars (`current / total`).
- `tool_call` events are high-frequency during extraction (one per content node per tool). Consider batching or throttling UI updates.
- `agent_log` events with `chunk` can be very frequent (streaming LLM tokens). You may want to debounce these or only show them in a "verbose" / "debug" view.
- The `complete` event payload matches the same counters as `GET /api/v1/ingest/{job_id}` response, so you can use it to update the job status UI without an extra API call.
- If the `EventSource` connection drops (network issue), the browser will auto-reconnect. Late joiners get a replay of recent events, so reconnection is safe.
