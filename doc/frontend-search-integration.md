# Frontend Search Integration Guide

Base URL: `http://54.160.238.80/api/v1`

All KB endpoints return **Server-Sent Events (SSE)** streams. The frontend must consume these using `EventSource` or `fetch` with a streaming reader.

---

## Endpoints

### 1. POST `/kb/chat` — RAG Chat (Retrieval + Generation)

Retrieves relevant KB documents, then streams an AI-generated answer with source citations.

**Request:**

```bash
curl 'http://54.160.238.80/api/v1/kb/chat' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  --data-raw '{"query":"Can I smoke in my rental car?","context_limit":5}'
```

| Field           | Type   | Required | Default | Description                          |
|-----------------|--------|----------|---------|--------------------------------------|
| `query`         | string | Yes      | —       | User question (1–2000 chars)         |
| `context_limit` | int    | No       | 5       | Number of KB docs to retrieve (1–20) |

**SSE Events:**

| Event     | Data Shape                                      | Description                        |
|-----------|------------------------------------------------|------------------------------------|
| `sources` | `{ query, sources: [{ s3_uri, content, source_url }] }` | Retrieved context documents        |
| `token`   | `{ text: "..." }`                               | Streamed text chunk from the model |
| `done`    | `{ query }`                                     | Stream complete                    |
| `error`   | `{ message }`                                   | Error occurred                     |

**Example SSE stream:**

```
event: sources
data: {"query":"Can I smoke in my rental car?","sources":[{"s3_uri":"s3://bucket/avis/smoking-policy-faq.md","content":"--- brand: avis ...","source_url":"https://www.avis.com/en/help/usa-faqs/smoking-policy"}]}

event: token
data: {"text":"No"}

event: token
data: {"text":", you cannot smoke in a"}

event: token
data: {"text":" rental car from Avis."}

event: token
data: {"text":" A cleaning fee of up to $450 may apply."}

event: done
data: {"query":"Can I smoke in my rental car?"}
```

---

### 2. POST `/kb/search` — Full-Text Search

Streams ranked search results from the knowledge base without AI generation.

**Request:**

```bash
curl 'http://54.160.238.80/api/v1/kb/search' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  --data-raw '{"query":"smoking policy","limit":3}'
```

| Field   | Type   | Required | Default | Description                           |
|---------|--------|----------|---------|---------------------------------------|
| `query` | string | Yes      | —       | Search query (1–1000 chars)           |
| `limit` | int    | No       | 10      | Max results to return (1–50)          |

**SSE Events:**

| Event          | Data Shape                                                    | Description          |
|----------------|--------------------------------------------------------------|----------------------|
| `search_start` | `{ query, total }`                                           | Search initiated     |
| `result`       | `{ content, s3_uri, score, metadata, source_url }` | Individual result    |
| `search_end`   | `{ query, total }`                                           | All results sent     |
| `error`        | `{ message }`                                                | Error occurred       |

**Example SSE stream:**

```
event: search_start
data: {"query":"smoking policy","total":3}

event: result
data: {"content":"# Avis 100% Smoke-Free Fleet Policy ...","s3_uri":"s3://bucket/avis/smoking-policy.md","score":0.4509,"metadata":{},"source_url":"https://www.avis.com/en/customer-service/faqs/usa/avis-car-sales/non-smoking"}

event: result
data: {"content":"## Non-Smoking Policy ...","s3_uri":"s3://bucket/avis/rental-policies.md","score":0.4412,"metadata":{},"source_url":"https://www.avis.com/en/customer-service/faqs/usa/rental-locations-vehicles"}

event: search_end
data: {"query":"smoking policy","total":3}
```

---

### 3. POST `/kb/download` — Presigned S3 Download URL

Generates a short-lived presigned URL for downloading a KB file from S3. Useful for "View Source" links.

**Request:**

```bash
curl -X POST 'http://54.160.238.80/api/v1/kb/download' \
  -H 'Content-Type: application/json' \
  --data-raw '{"s3_uri":"s3://bucket/path/to/file.md"}'
```

| Field    | Type   | Required | Description                          |
|----------|--------|----------|--------------------------------------|
| `s3_uri` | string | Yes      | Full S3 URI (e.g. `s3://bucket/key`) |

**Response (JSON, not SSE):**

```json
{ "url": "https://s3.amazonaws.com/bucket/key?X-Amz-Signature=..." }
```

The URL expires after 5 minutes.

---

## Frontend Implementation

### Using `fetch` + ReadableStream (Recommended)

```typescript
async function streamChat(query: string, onToken: (text: string) => void, onSources: (sources: any[]) => void) {
  const response = await fetch('http://54.160.238.80/api/v1/kb/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ query, context_limit: 5 }),
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    let currentEvent = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7);
      } else if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        switch (currentEvent) {
          case 'token':
            onToken(data.text);
            break;
          case 'sources':
            onSources(data.sources);
            break;
          case 'done':
            return;
          case 'error':
            throw new Error(data.message);
        }
      }
    }
  }
}

// Usage:
// const API_BASE = 'http://54.160.238.80/api/v1';
// Replace '/api/v1/kb/chat' with `${API_BASE}/kb/chat` in fetch calls.
```

### Using `fetch` for Search

```typescript
async function streamSearch(query: string, onResult: (result: any) => void): Promise<void> {
  const response = await fetch('http://54.160.238.80/api/v1/kb/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ query, limit: 10 }),
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    let currentEvent = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7);
      } else if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        if (currentEvent === 'result') onResult(data);
        if (currentEvent === 'error') throw new Error(data.message);
      }
    }
  }
}
```

### React Hook Example

```tsx
import { useState, useCallback } from 'react';

function useKBChat() {
  const [answer, setAnswer] = useState('');
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const ask = useCallback(async (query: string) => {
    setAnswer('');
    setSources([]);
    setLoading(true);

    try {
      await streamChat(
        query,
        (text) => setAnswer((prev) => prev + text),
        (s) => setSources(s),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  return { answer, sources, loading, ask };
}
```

---

## Notes

- All SSE endpoints use `POST` (not `GET`), so the native `EventSource` API won't work directly. Use `fetch` with a streaming reader or a library like [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source).
- The `sources` event in `/kb/chat` fires mid-stream (between token chunks), so the frontend should render sources as soon as they arrive.
- CORS is open (`*`) on the backend, so no proxy is needed during local development.
- For production, the backend is available at `http://54.160.238.80`.
