# Architecture Overview

## System Summary

The AEM KB Ingestion System is a FastAPI application that ingests content from Adobe Experience Manager (AEM) `model.json` endpoints, processes it through three AI agents (powered by AWS Strands SDK + Amazon Bedrock Claude Sonnet and Haiku), and manages the resulting Markdown files through a quality-gated lifecycle into Amazon S3.

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
                                          │  • sources       │  │  {DocType}/ │
                                          │  • ingestion_jobs│  │  {Brand}/   │
                                          │  • kb_files      │  │  {Date}/    │
                                          │  • revalidation_ │  │  {file}.md  │
                                          │    jobs           │  │             │
                                          │  • nav_tree_cache│  │             │
                                          │  • deep_links    │  │             │
                                          └─────────────────┘  └─────────────┘
```

## Key Design Decisions

### Three-Agent Architecture
Content processing uses three specialized Strands agents:
1. **Discovery Agent** (Claude Haiku) — fast, cheap content discovery and deep link detection from raw AEM JSON
2. **Extractor Agent** (Claude Sonnet) — transforms discovered content into structured Markdown files
3. **Validator Agent** (Claude Sonnet) — scores quality and classifies document type

This separation allows each agent to have a focused system prompt, independent tool sets, and the ability to evolve independently. Haiku handles the high-volume discovery pass cheaply, while Sonnet handles the reasoning-heavy extraction and validation.

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
The entire stack is async: FastAPI, SQLAlchemy 2.0 async ORM (with asyncpg driver), httpx for HTTP calls, and `asyncio.to_thread` for the synchronous boto3 S3 client. This ensures non-blocking I/O throughout.

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
│   │   ├── stream.py              # SSE streaming endpoint
│   │   ├── sources.py             # Source management endpoints
│   │   ├── nav.py                 # Navigation tree parsing endpoint
│   │   ├── query.py               # KB search and RAG chat endpoints
│   │   └── context.py             # Context agent endpoint
│   ├── agents/
│   │   ├── discovery.py           # Discovery Agent (Haiku — content + link discovery)
│   │   ├── extractor.py           # Extractor Agent (Sonnet — transform to Markdown)
│   │   ├── validator.py           # Validator Agent (Sonnet — score + classify)
│   │   └── context_agent.py       # Context Agent (RAG chat)
│   ├── tools/
│   │   ├── fetch_aem.py           # HTTP fetch for AEM model.json
│   │   ├── aem_pruner.py          # AEM JSON pruning for large payloads
│   │   ├── md_generator.py        # Markdown file generation with frontmatter
│   │   ├── duplicate_checker.py   # Content hash lookup (async)
│   │   └── file_context.py        # File context retrieval for RAG
│   ├── services/
│   │   ├── pipeline.py            # Ingestion pipeline orchestration
│   │   ├── revalidation.py        # Single/batch revalidation service
│   │   ├── s3_upload.py           # S3 upload with structured keys
│   │   ├── stream_manager.py      # SSE event broadcasting + replay
│   │   ├── kb_query.py            # KB search and RAG query service
│   │   ├── nav_parser.py          # AEM navigation tree parser
│   │   └── context_cache.py       # Context caching for RAG
│   ├── db/
│   │   ├── models.py              # SQLAlchemy ORM models (6 tables)
│   │   ├── queries.py             # All async query functions
│   │   └── session.py             # AsyncEngine + session factory
│   ├── models/
│   │   └── schemas.py             # All Pydantic models
│   └── utils/
│       └── url_inference.py       # Region/brand inference from URLs
├── alembic/
│   ├── env.py                     # Async migration environment
│   └── versions/                  # Migration scripts (001 → 004)
├── tests/                         # pytest + hypothesis test suite
├── doc/                           # This documentation
├── check_infra.py                 # Infrastructure diagnostic script
├── reset_all.py                   # DB truncate + S3 empty script
├── pyproject.toml                 # Project metadata and dependencies
├── docker-compose.yml             # Docker Compose with dev/preprod profiles
├── Dockerfile                     # Container image definition
└── .env                           # Environment configuration
```

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web Framework | FastAPI | Async REST API with automatic OpenAPI docs |
| AI Agents | AWS Strands Agents SDK | Agent orchestration with tool calling |
| LLM (Extraction/Validation) | Amazon Bedrock (Claude Sonnet) | Content extraction reasoning + quality validation |
| LLM (Discovery) | Amazon Bedrock (Claude Haiku) | Fast content discovery + deep link detection |
| Database | NeonDB (PostgreSQL) | Serverless PostgreSQL for file and job tracking |
| ORM | SQLAlchemy 2.0 (async) | Declarative ORM with async session management |
| Migrations | Alembic | Schema migration management |
| DB Driver | asyncpg | Native async PostgreSQL driver (via SQLAlchemy) |
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
  ├── db/session.py (AsyncEngine + session factory)
  ├── services/stream_manager.py (SSE)
  ├── agents/discovery.py (Haiku discovery)
  │     └── tools/aem_pruner.py
  ├── agents/extractor.py (Sonnet extraction)
  │     └── tools/md_generator.py
  ├── agents/validator.py (Sonnet validation)
  │     ├── tools/duplicate_checker.py
  │     │     └── db/queries.py
  │     └── tools/file_context.py
  ├── agents/context_agent.py (RAG chat)
  ├── services/pipeline.py
  │     ├── agents/discovery.py
  │     ├── agents/extractor.py
  │     ├── agents/validator.py
  │     ├── services/s3_upload.py
  │     ├── services/stream_manager.py
  │     └── db/queries.py
  ├── services/revalidation.py
  │     ├── agents/validator.py
  │     ├── services/s3_upload.py
  │     └── db/queries.py
  ├── services/kb_query.py
  ├── services/context_cache.py
  └── api/router.py
        ├── api/ingest.py
        ├── api/queue.py
        ├── api/files.py
        ├── api/stats.py
        ├── api/revalidate.py
        ├── api/stream.py
        ├── api/sources.py
        ├── api/nav.py
        ├── api/query.py
        └── api/context.py
```
