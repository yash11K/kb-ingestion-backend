# Ingestion Pipeline

## Overview

The ingestion pipeline is the core workflow of the system. It takes an AEM `model.json` URL and produces validated, scored Markdown files stored in the database and (for high-quality content) uploaded to S3.

## Pipeline Sequence

```
Client                  API              Pipeline           Extractor         Validator          DB              S3
  │                      │                  │                  │                 │                │               │
  │  POST /ingest        │                  │                  │                 │                │               │
  │  {url,region,brand}  │                  │                  │                 │                │               │
  │─────────────────────►│                  │                  │                 │                │               │
  │                      │  create job      │                  │                 │                │               │
  │                      │─────────────────────────────────────────────────────────────────────►│               │
  │  202 {job_id}        │                  │                  │                 │                │               │
  │◄─────────────────────│                  │                  │                 │                │               │
  │                      │  BackgroundTask  │                  │                 │                │               │
  │                      │─────────────────►│                  │                 │                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  1. Fetch AEM    │                 │                │               │
  │                      │                  │─────────────────►│                 │                │               │
  │                      │                  │                  │── HTTP GET ──►AEM               │               │
  │                      │                  │                  │◄── JSON ──────                  │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  2. Pre-filter   │                 │                │               │
  │                      │                  │  (Python direct) │                 │                │               │
  │                      │                  │─────────────────►│                 │                │               │
  │                      │                  │  ContentNodes[]  │                 │                │               │
  │                      │                  │◄─────────────────│                 │                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  3. Agent: convert + generate     │                │               │
  │                      │                  │─────────────────►│                 │                │               │
  │                      │                  │  MarkdownFile[]  │                 │                │               │
  │                      │                  │◄─────────────────│                 │                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  FOR EACH FILE:                   │                │               │
  │                      │                  │  ─────────────────────────────────────────────────────────────────  │
  │                      │                  │  4. Check duplicate hash          │                │               │
  │                      │                  │──────────────────────────────────────────────────►│               │
  │                      │                  │  (skip if exists)                 │                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  5. Insert to DB (pending_review) │                │               │
  │                      │                  │──────────────────────────────────────────────────►│               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  6. Validate     │                 │                │               │
  │                      │                  │─────────────────────────────────►│                │               │
  │                      │                  │  ValidationResult                 │                │               │
  │                      │                  │◄─────────────────────────────────│                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  7. Route by score                │                │               │
  │                      │                  │  ≥0.7 → approved │                 │                │               │
  │                      │                  │  ≥0.2 → review   │                 │                │               │
  │                      │                  │  <0.2 → rejected │                 │                │               │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  8. Upload if approved            │                │               │
  │                      │                  │──────────────────────────────────────────────────────────────────►│
  │                      │                  │  (status → in_s3)                 │                │               │
  │                      │                  │  ─────────────────────────────────────────────────────────────────  │
  │                      │                  │                  │                 │                │               │
  │                      │                  │  9. Update job counters + completed                │               │
  │                      │                  │──────────────────────────────────────────────────►│               │
```

## Step-by-Step Breakdown

### Step 1: API Request

The client sends `POST /api/v1/ingest` with:
```json
{
  "url": "https://aem-instance/content/page.model.json",
  "region": "US",
  "brand": "BrandName"
}
```

The API creates an `ingestion_jobs` record (status: `in_progress`) and launches the pipeline as a `BackgroundTask`. The client receives a 202 with the `job_id` immediately.

### Step 2: Fetch AEM JSON

The `ExtractorAgent.extract()` method fetches the AEM JSON directly via `httpx.get()` (not through the LLM). This is a deliberate design choice — the HTTP fetch is deterministic and doesn't need AI reasoning.

The raw payload size is logged. If it exceeds `MAX_PAYLOAD_BYTES` (default 500KB), a warning is emitted.

### Step 3: Pre-Filter Content Nodes

The `filter_by_component_type_direct()` function runs as a plain Python call (not an LLM tool) to avoid sending the full JSON through the agent's context window. It:

1. Recursively traverses all `:items` objects in the AEM JSON
2. For each node with a `:type` field:
   - Checks against the **denylist** first (denylist takes precedence)
   - If not denied, checks against the **allowlist**
   - Matching nodes become `ContentNode` objects
3. Always recurses into nested `:items` regardless of match/deny

The glob-style matching strips the `*/` prefix and uses `endswith()`:
- Pattern `*/text` matches `core/components/text`
- Pattern `*/accordionitem` matches `avis/components/content/accordionitem`

### Step 4: Agent Extraction

The filtered `ContentNode` list is serialized to JSON and passed to the Strands Extractor Agent. The agent uses two tools:

1. **`html_to_markdown`**: Converts each node's HTML content to clean Markdown using `markdownify`, then strips any residual HTML tags
2. **`generate_md_file`**: Creates a complete Markdown file with YAML frontmatter containing all metadata fields, computes the SHA-256 content hash, and generates a slug-based filename

The agent returns a list of `MarkdownFile` objects.

### Step 5: Duplicate Detection

For each `MarkdownFile`, the pipeline queries `kb_files` by `content_hash`. If a match exists, the file is skipped and `duplicates_skipped` is incremented. This makes re-ingestion of the same URL idempotent.

### Step 6: Database Insertion

Non-duplicate files are inserted into `kb_files` with status `pending_review` and null validation fields. The `files_created` counter is incremented.

### Step 7: Validation

The `ValidatorAgent` scores each file on three dimensions:

| Dimension | Range | Method |
|-----------|-------|--------|
| `metadata_completeness` | 0.0 – 0.3 | Parses YAML frontmatter, checks 10 required fields |
| `semantic_quality` | 0.0 – 0.5 | LLM evaluates coherence, readability, completeness |
| `uniqueness` | 0.0 – 0.2 | Checks content_hash against DB (0.2 if unique, 0.0 if duplicate) |

The total score is the sum of sub-scores (0.0 – 1.0). The agent also classifies the document type (TnC, FAQ, ProductGuide, Support, Marketing, General).

If validation fails for a single file (e.g., Bedrock timeout), the error is caught, logged, and the file remains `pending_review` with null validation fields. The pipeline continues to the next file.

### Step 8: Score-Based Routing

| Score | Status | Action |
|-------|--------|--------|
| ≥ 0.7 | `approved` | Upload to S3, then status → `in_s3` |
| 0.2 – 0.7 | `pending_review` | Awaits human review |
| < 0.2 | `auto_rejected` | No further action |

### Step 9: S3 Upload

Approved files are uploaded to S3 with the key structure:
```
{DocType}/{Brand}/{YYYY-MM-DD}/{filename}.md
```

Where `DocType` is the AI-classified document type (e.g., `FAQ`, `TnC`, `ProductGuide`).

S3 object metadata includes `file_id` and `content_hash`. ContentType is set to `text/markdown`.

If the S3 upload fails, the file retains its `approved` status and the error is logged for later retry.

### Step 10: Job Completion

The ingestion job is updated with final counters:
- `files_created`: Total files inserted (excluding duplicates)
- `files_auto_approved`: Files scoring ≥ 0.7
- `files_pending_review`: Files scoring 0.2 – 0.7 (includes validation failures)
- `files_auto_rejected`: Files scoring < 0.2
- `duplicates_skipped`: Files skipped due to existing content hash

Status is set to `completed` with a `completed_at` timestamp.

## Error Handling

| Failure Point | Behavior |
|---------------|----------|
| AEM fetch fails (timeout, non-200, invalid JSON) | Entire job marked `failed` with error message |
| Single file validation exception | File stays `pending_review` with null scores; pipeline continues |
| S3 upload failure | File retains `approved` status; error logged |
| Unrecoverable pipeline error | Job marked `failed`; SSE `error` event emitted |

## SSE Streaming During Pipeline

The pipeline emits real-time events via the `StreamManager` throughout execution. Clients connect to `GET /ingest/{job_id}/stream` to receive:

- `progress` events for each pipeline stage
- `tool_call` events when agents invoke tools
- `agent_log` events for streaming LLM output
- `complete` or `error` terminal events

See [SSE Streaming](./sse-streaming.md) for the full event specification.

## Current Input Model

Today, the ingestion endpoint requires explicit `url`, `region`, and `brand` parameters from the caller. The system also supports an optional `component_types` override to customize the allowlist per-request.

### Toward Agentic Automation

The current manual input model is a stepping stone. The roadmap envisions progressively more autonomous behavior:

1. **Phase 1 (Current)**: Human provides URL + region + brand explicitly
2. **Phase 2**: Agent auto-detects region and brand from AEM page metadata/URL patterns
3. **Phase 3**: Agent crawls AEM sitemaps to discover ingestible URLs automatically
4. **Phase 4**: Agent monitors AEM for content changes and triggers re-ingestion proactively

See [Future Roadmap](./roadmap.md) for details on the agentic evolution plan.
