# AEM Knowledge Base Ingestion System — Documentation

Technical documentation for the AEM KB Ingestion System, a Python-based pipeline that fetches content from Adobe Experience Manager (AEM) endpoints, transforms it into structured Markdown files, validates quality using AI agents, and routes content through a human review workflow into Amazon S3.

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture Overview](./architecture.md) | System architecture, component diagram, and design decisions |
| [Ingestion Pipeline](./ingestion-pipeline.md) | End-to-end pipeline flow: fetch → extract → validate → route → upload |
| [AI Agents](./agents.md) | Extractor and Validator agent design, tools, and prompts |
| [API Reference](./api-reference.md) | All REST endpoints with request/response schemas |
| [Data Models](./data-models.md) | Pydantic models, status lifecycle, and content hash logic |
| [Database & DDL](./database.md) | Full DDL, ER diagram, ORM layer, Alembic migrations, and status enums |
| [Configuration](./configuration.md) | Environment variables, thresholds, and component filtering |
| [SSE Streaming](./sse-streaming.md) | Real-time pipeline event streaming specification |
| [Infrastructure & Operations](./operations.md) | Deployment, diagnostics, reset scripts, and S3 key structure |
| [Future Roadmap](./roadmap.md) | Agentic automation vision and planned enhancements |
