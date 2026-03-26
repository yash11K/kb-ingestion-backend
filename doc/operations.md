# Infrastructure & Operations

## Running the Application

```bash
# Install dependencies
pip install -e ".[dev]"

# Start the server
uvicorn src.main:create_app --factory --reload --port 8000
```

The app is available at `http://localhost:8000`. API docs at `/docs` (Swagger) and `/redoc`.

## Docker

Run the application via Docker Compose using environment-specific profiles:

```bash
# Development
docker compose --profile dev up

# Pre-production
docker compose --profile preprod up
```

The `dev` profile loads `.env.dev` and the `preprod` profile loads `.env.preprod`. AWS credentials are forwarded from the host environment. See `.env.example` for the full list of variables.

## Database Setup

The system uses PostgreSQL (Neon serverless today, Aurora PostgreSQL in the future) with SQLAlchemy 2.0 async ORM. Schema migrations are managed by Alembic.

```bash
# New database — apply all migrations:
alembic upgrade head

# Existing database that already has the schema — stamp without applying:
alembic stamp head
```

The baseline migration (`alembic/versions/001_baseline.py`) creates all 6 tables (`sources`, `ingestion_jobs`, `kb_files`, `revalidation_jobs`, `nav_tree_cache`, `deep_links`), indexes, the `uuid-ossp` extension, and the `search_vector` trigger. Future schema changes are added as new Alembic revisions.

See [Database & DDL](./database.md) for the full schema reference, migration chain, and ORM details.

## Infrastructure Diagnostics

Run `check_infra.py` to verify database and S3 connectivity:

```bash
python check_infra.py
```

This script performs:

1. **Database checks:**
   - Single connection test with SSL
   - Connection pool test (min=2, max=5)
   - Sustained connection test (5s idle + query) to detect NeonDB serverless suspension

2. **S3 checks:**
   - AWS credential verification via STS
   - Bucket existence and access (HeadBucket)
   - Write test (PutObject + cleanup)

## Reset Script

`reset_all.py` truncates all database tables and empties the S3 bucket:

```bash
python reset_all.py
```

This is useful for development/testing. It uses SQLAlchemy to truncate all 6 tables (`kb_files`, `ingestion_jobs`, `revalidation_jobs`, `sources`, `nav_tree_cache`, `deep_links`), then deletes all objects from the configured S3 bucket.

## S3 Key Structure

Approved files are uploaded with the following key pattern:

```
{DocType}/{Brand}/{YYYY-MM-DD}/{filename}.md
```

Examples:
```
FAQ/Avis/2025-06-06/how-do-i-unlock-my-car.md
TnC/Avis/2025-08-14/terms-and-conditions.md
ProductGuide/Avis/2025-03-05/why-buy-a-car-from-avis.md
```

`DocType` is the AI-classified document type from the Validator Agent. S3 object metadata includes `file_id` (UUID) and `content_hash` (SHA-256).

## Application Lifespan

The FastAPI app uses a lifespan context manager (`src/main.py`) that:

**On startup:**
1. Loads settings from environment
2. Creates SQLAlchemy `AsyncEngine` and `async_sessionmaker` (SSL required, `statement_cache_size=0`)
3. Creates boto3 S3 client
4. Instantiates all services (S3Upload, StreamManager, DiscoveryAgent, ExtractorAgent, ValidatorAgent, PipelineService, RevalidationService, KBQueryService, ContextAgent, ContextCache)
5. Attaches everything to `app.state`

**On shutdown:**
1. Disposes the SQLAlchemy engine (releases all connections)

## CORS

The application allows all origins (`*`) for development. This should be restricted in production.

## Logging

Logging is configured at `INFO` level with the format:
```
%(asctime)s %(levelname)s [%(name)s] %(message)s
```

Key log points:
- Raw AEM JSON payload size and estimated token count
- Payload size warnings when exceeding `MAX_PAYLOAD_BYTES`
- Filtered content node count and payload size
- Agent tool calls and completion
- Validation failures per file
- S3 upload failures
- Pipeline completion with counters

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | ≥ 0.110 | Web framework |
| uvicorn | ≥ 0.29 | ASGI server |
| httpx | ≥ 0.27 | Async HTTP client |
| sqlalchemy[asyncio] | ≥ 2.0 | Async ORM and database toolkit |
| alembic | ≥ 1.13 | Schema migration management |
| asyncpg | ≥ 0.29 | PostgreSQL async driver (used by SQLAlchemy) |
| boto3 | ≥ 1.34 | AWS SDK |
| strands-agents | ≥ 0.1 | AI agent framework |
| pydantic-settings | ≥ 2.2 | Config management |
| markdownify | ≥ 0.12 | HTML → Markdown |
| python-frontmatter | ≥ 1.1 | YAML frontmatter |

Dev dependencies: `hypothesis`, `pytest`, `pytest-asyncio`, `respx`, `moto[s3]`
