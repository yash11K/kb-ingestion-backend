# Architecture Overview

## System Summary

The AEM KB Ingestion System is a FastAPI application that ingests content from Adobe Experience Manager (AEM) `model.json` endpoints, processes it through two AI agents (powered by AWS Strands SDK + Amazon Bedrock Claude Sonnet), and manages the resulting Markdown files through a quality-gated lifecycle into Amazon S3.

## High-Level Architecture

```
┌─────────────┐     POST /ingest        ┌──────────────────────────────────────────────┐
│  API Client  │ ──────────────────────► │              FastAPI Application             │
│  (Frontend)  │ ◄── 202 { job_id }      │                                              │
│              │                         │  ┌──────────┐   ┌────────────────────────┐   │
│              │  GET /ingest/:id/stream  │  │  Router   │──►│  Background Pipeline   │   │
│              │ ◄── SSE events ──────── │  └──────────┘   │                        │   │
└─────────────┘                         │                  │  ┌──────────────────┐  │   │
                                        │                  │  │ Extractor Agent   │  │   │
                                        │                  │  │ (Strands+Bedrock) │  │   │
                                        │                  │  └────────┬─────────┘  │   │
                                        │                  │           │            │   │
                                        │                  │  ┌────────▼─────────┐  │   │
                                        │                  │  │ Validator Agent   │  │   │
                                        │                  │  │ (Strands+Bedrock) │  │   │
                                        │                  │  └────────┬─────────┘  │   │
                                        │                  │           │            │   │
                                        │                  │  ┌────────▼─────────┐  │   │
                                        │                  │  │  Score Router     │  │   │
                                        │                  │  │  ≥0.7 → approved  │  │   │
                                        │                  │  │  ≥0.2 → review   │  │   │
                                        │                  │  │  <0.2 → rejected │  │   │
                                        │                  │  └──────────────────┘  │   │
                                        │                  └────────────────────────┘   │
                                        └──────────┬───────────────────┬───────────────┘
                                                   │                   │
                                          ┌────────▼────────┐  ┌──────▼──────┐
                                          │    NeonDB        │  │  Amazon S3  │
                                          │  (PostgreSQL)    │  │  (Markdown) │
                                          │                  │  │             │
                                          │  • kb_files      │  │  {DocType}/ │
                                          │  • ingestion_jobs│  │  {Brand}/   │
                                          │  • revalidation_ │  │  {Date}/    │
                                          │    jobs           │  │  {file}.md  │
                                          └─────────────────┘  └─────────────┘
```

## Key Design Decisions

### Two-Agent Architecture
Extraction and validation are handled by separate Strands agents. This separation allows each agent to have a focused system prompt, independent tool sets, and the ability to evolve independently. The Extractor Agent handles content transformation (deterministic tools), while the Validator Agent handles quality assessment (LLM reasoning).

### Pre-Filtering Outside the LLM Context
Large AEM JSON payloads are pre-filtered in Python before being passed to the Extractor Agent. The `filter_by_component_type` logic is deterministic and doesn't need LLM reasoning, so running it outside the agent's context window prevents `MaxTokensReachedException` for large pages.

### Background Task Execution
Ingestion and batch revalidation run as FastAPI `BackgroundTasks`, returning 202 immediately. This keeps the API responsive while long-running AI agent calls process in the background. Real-time progress is streamed via SSE.

### Content-Hash Deduplication
SHA-256 hashes are computed from the Markdown body only (excluding YAML frontmatter). This enables idempotent re-ingestion — the same AEM URL can be ingested multiple times without creating duplicates, even if frontmatter metadata changes.

### Score-Based Routing with Human Review
A three-tier routing system balances automation with quality control:
- **≥ 0.7**: Auto-approved and uploaded to S3
- **0.2 – 0.7**: Routed to human review queue
- **< 0.2**: Auto-rejected

Thresholds are configurable via environment variables.

### Async-First Stack
The entire stack is async: FastAPI, asyncpg (native async PostgreSQL driver), httpx for HTTP calls, and `asyncio.to_thread` for the synchronous boto3 S3 client. This ensures non-blocking I/O throughout.

## Project Structure

```
aem-kb-system/
├── src/
│   ├── main.py                    # FastAPI app factory, lifespan events
│   ├── config.py                  # Pydantic Settings (env vars)
│   ├── api/
│   │   ├── router.py              # Top-level router aggregation (/api/v1)
│   │   ├── ingest.py              # POST /ingest, GET /ingest/{job_id}, GET /jobs
│   │   ├── queue.py               # Review queue CRUD endpoints
│   │   ├── files.py               # File listing and detail endpoints
│   │   ├── stats.py               # Aggregate statistics endpoint
│   │   ├── revalidate.py          # Single and batch revalidation endpoints
│   │   └── stream.py              # SSE streaming endpoint
│   ├── agents/
│   │   ├── extractor.py           # Extractor Agent (fetch + transform)
│   │   └── validator.py           # Validator Agent (score + classify)
│   ├── tools/
│   │   ├── fetch_aem.py           # HTTP fetch for AEM model.json
│   │   ├── filter_components.py   # Recursive :items traversal + filtering
│   │   ├── html_converter.py      # HTML → Markdown via markdownify
│   │   ├── md_generator.py        # Markdown file generation with frontmatter
│   │   ├── duplicate_checker.py   # Content hash lookup (async)
│   │   └── frontmatter_parser.py  # YAML frontmatter parsing + validation
│   ├── services/
│   │   ├── pipeline.py            # Ingestion pipeline orchestration
│   │   ├── revalidation.py        # Single/batch revalidation service
│   │   ├── s3_upload.py           # S3 upload with structured keys
│   │   └── stream_manager.py      # SSE event broadcasting + replay
│   ├── db/
│   │   ├── connection.py          # asyncpg pool management
│   │   ├── queries.py             # All SQL query functions
│   │   └── migrations/
│   │       ├── 001_initial.sql    # kb_files + ingestion_jobs tables
│   │       ├── 002_revalidation_jobs.sql
│   │       └── 003_add_doc_type.sql
│   └── models/
│       └── schemas.py             # All Pydantic models
├── tests/                         # pytest + hypothesis test suite
├── doc/                           # This documentation
├── check_infra.py                 # Infrastructure diagnostic script
├── reset_all.py                   # DB truncate + S3 empty script
├── pyproject.toml                 # Project metadata and dependencies
└── .env                           # Environment configuration
```

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web Framework | FastAPI | Async REST API with automatic OpenAPI docs |
| AI Agents | AWS Strands Agents SDK | Agent orchestration with tool calling |
| LLM | Amazon Bedrock (Claude Sonnet) | Content extraction reasoning + quality validation |
| Database | NeonDB (PostgreSQL) | Serverless PostgreSQL for file and job tracking |
| DB Driver | asyncpg | Native async PostgreSQL driver |
| Object Storage | Amazon S3 | Approved Markdown file storage |
| AWS SDK | boto3 | S3 operations |
| HTTP Client | httpx | Async HTTP for AEM endpoint fetching |
| HTML Conversion | markdownify | HTML → Markdown transformation |
| Frontmatter | python-frontmatter | YAML frontmatter parsing/serialization |
| Config | pydantic-settings | Type-safe environment variable loading |
| Testing | pytest, hypothesis, respx, moto | Unit, property-based, and mock testing |

## Component Dependency Graph

```
main.py (app factory)
  ├── config.py (Settings)
  ├── db/connection.py (asyncpg pool)
  ├── services/stream_manager.py (SSE)
  ├── agents/extractor.py
  │     ├── tools/filter_components.py
  │     ├── tools/html_converter.py
  │     └── tools/md_generator.py
  ├── agents/validator.py
  │     ├── tools/duplicate_checker.py
  │     │     └── db/queries.py
  │     └── tools/frontmatter_parser.py
  ├── services/pipeline.py
  │     ├── agents/extractor.py
  │     ├── agents/validator.py
  │     ├── services/s3_upload.py
  │     └── db/queries.py
  ├── services/revalidation.py
  │     ├── agents/validator.py
  │     ├── services/s3_upload.py
  │     └── db/queries.py
  └── api/router.py
        ├── api/ingest.py
        ├── api/queue.py
        ├── api/files.py
        ├── api/stats.py
        ├── api/revalidate.py
        └── api/stream.py
```
