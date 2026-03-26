# Database & DDL Reference

## Overview

The system uses PostgreSQL (Neon serverless) with SQLAlchemy 2.0 async ORM and Alembic for schema migrations. The database driver is `asyncpg`, accessed through SQLAlchemy's `AsyncEngine`.

Connection string format: `postgresql+asyncpg://user:pass@host:port/dbname?ssl=require`

## Tables

The schema consists of 6 tables:

| Table | Purpose |
|-------|---------|
| `sources` | Tracked AEM page URLs with region/brand metadata |
| `ingestion_jobs` | Ingestion job tracking with progress counters |
| `kb_files` | Markdown files through their quality-gated lifecycle |
| `revalidation_jobs` | Batch revalidation job tracking |
| `nav_tree_cache` | Cached navigation tree data with TTL |
| `deep_links` | Discovered embedded links from AEM content |

## Entity Relationship Diagram

```
sources (1) ──────┬──── (N) ingestion_jobs
    │             │
    │             └──── (N) deep_links
    │
    └──── (N) kb_files ────── (N:1) ingestion_jobs
```

- `sources.id` ← `ingestion_jobs.source_id`
- `sources.id` ← `kb_files.source_id`
- `sources.id` ← `deep_links.source_id`
- `ingestion_jobs.id` ← `kb_files.job_id`
- `ingestion_jobs.id` ← `deep_links.job_id`

`revalidation_jobs` and `nav_tree_cache` are standalone (no foreign keys).

---

## DDL — Table Definitions

### sources

```sql
CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url             TEXT NOT NULL UNIQUE,
    region          TEXT NOT NULL,
    brand           TEXT NOT NULL,
    nav_root_url    TEXT,
    nav_label       TEXT,
    nav_section     TEXT,
    page_path       TEXT,
    last_ingested_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sources_region ON sources (region);
CREATE INDEX idx_sources_brand  ON sources (brand);
CREATE INDEX idx_sources_url    ON sources (url);
```

### ingestion_jobs

```sql
CREATE TABLE ingestion_jobs (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url           TEXT NOT NULL,
    source_id            UUID REFERENCES sources(id),
    status               TEXT NOT NULL DEFAULT 'in_progress',
    total_nodes_found    INTEGER,
    files_created        INTEGER NOT NULL DEFAULT 0,
    files_auto_approved  INTEGER NOT NULL DEFAULT 0,
    files_pending_review INTEGER NOT NULL DEFAULT 0,
    files_auto_rejected  INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped   INTEGER NOT NULL DEFAULT 0,
    error_message        TEXT,
    child_urls           TEXT[] NOT NULL DEFAULT '{}',
    max_depth            INTEGER NOT NULL DEFAULT 0,
    pages_crawled        INTEGER NOT NULL DEFAULT 0,
    current_depth        INTEGER NOT NULL DEFAULT 0,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ
);

CREATE INDEX idx_ingestion_jobs_status    ON ingestion_jobs (status);
CREATE INDEX idx_ingestion_jobs_source_id ON ingestion_jobs (source_id);
```

### kb_files

```sql
CREATE TABLE kb_files (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename             TEXT NOT NULL,
    title                TEXT NOT NULL,
    content_type         TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    source_url           TEXT NOT NULL,
    component_type       TEXT NOT NULL,
    aem_node_id          TEXT,
    md_content           TEXT NOT NULL,
    doc_type             TEXT,
    modify_date          TIMESTAMPTZ,
    parent_context       TEXT,
    region               TEXT NOT NULL,
    brand                TEXT NOT NULL,
    key                  TEXT,
    namespace            TEXT,
    validation_score     FLOAT,
    validation_breakdown JSONB,
    validation_issues    JSONB,
    status               TEXT NOT NULL DEFAULT 'pending_review',
    s3_bucket            TEXT,
    s3_key               TEXT,
    s3_uploaded_at       TIMESTAMPTZ,
    reviewed_by          TEXT,
    reviewed_at          TIMESTAMPTZ,
    review_notes         TEXT,
    source_id            UUID REFERENCES sources(id),
    job_id               UUID REFERENCES ingestion_jobs(id),
    search_vector        TSVECTOR,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_kb_files_content_hash  ON kb_files (content_hash);
CREATE INDEX idx_kb_files_status        ON kb_files (status);
CREATE INDEX idx_kb_files_region        ON kb_files (region);
CREATE INDEX idx_kb_files_brand         ON kb_files (brand);
CREATE INDEX idx_kb_files_source_url    ON kb_files (source_url);
CREATE INDEX idx_kb_files_content_type  ON kb_files (content_type);
CREATE INDEX idx_kb_files_doc_type      ON kb_files (doc_type);
CREATE INDEX idx_kb_files_created_at    ON kb_files (created_at);
CREATE INDEX idx_kb_files_source_id     ON kb_files (source_id);
CREATE INDEX idx_kb_files_job_id        ON kb_files (job_id);
CREATE INDEX idx_kb_files_search_vector ON kb_files USING gin (search_vector);
```

### revalidation_jobs

```sql
CREATE TABLE revalidation_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status          TEXT NOT NULL DEFAULT 'in_progress',
    total_files     INTEGER NOT NULL,
    completed       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    not_found       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_revalidation_jobs_status ON revalidation_jobs (status);
```

### nav_tree_cache

```sql
CREATE TABLE nav_tree_cache (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    root_url    TEXT NOT NULL UNIQUE,
    brand       TEXT NOT NULL,
    region      TEXT NOT NULL,
    tree_data   JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);
```

### deep_links

```sql
CREATE TABLE deep_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID REFERENCES sources(id),
    job_id          UUID REFERENCES ingestion_jobs(id),
    url             TEXT NOT NULL,
    model_json_url  TEXT NOT NULL,
    anchor_text     TEXT,
    found_in_node   TEXT,
    found_in_page   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_deep_links_source_url UNIQUE (source_id, url)
);

CREATE INDEX idx_deep_links_source ON deep_links (source_id);
CREATE INDEX idx_deep_links_status ON deep_links (status);
CREATE INDEX idx_deep_links_job    ON deep_links (job_id);
```

---

## Full-Text Search

`kb_files` has a `search_vector` column (type `TSVECTOR`) with a GIN index. A trigger automatically updates it on INSERT or UPDATE of `title` or `md_content`:

```sql
CREATE OR REPLACE FUNCTION kb_files_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector(
        'english',
        coalesce(NEW.title, '') || ' ' || coalesce(NEW.md_content, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_kb_files_search_vector
    BEFORE INSERT OR UPDATE OF title, md_content ON kb_files
    FOR EACH ROW EXECUTE FUNCTION kb_files_search_vector_update();
```

---

## Extensions

| Extension | Purpose |
|-----------|---------|
| `uuid-ossp` | Provides `uuid_generate_v4()` for primary key defaults |

`gen_random_uuid()` (built-in since PostgreSQL 13) is used by `nav_tree_cache` and `deep_links`.

---

## SQLAlchemy ORM Layer

All tables are mapped in `src/db/models.py` using SQLAlchemy 2.0 declarative style with `Mapped` type annotations. Key patterns:

- UUID primary keys with server-side defaults
- `JSONB` columns for `validation_breakdown`, `validation_issues`, `tree_data`
- `ARRAY(Text)` for `child_urls`
- `TSVECTOR` for full-text search
- Relationships: `Source` ↔ `IngestionJob`, `Source` ↔ `KBFile`, `Source` ↔ `DeepLink`, `IngestionJob` ↔ `KBFile`

### Session Management

`src/db/session.py` provides:

- `init_engine(database_url)` — creates an `AsyncEngine` with SSL required and `statement_cache_size=0` (for PgBouncer/Neon compatibility)
- `create_session_factory(engine)` — returns an `async_sessionmaker` with `expire_on_commit=False`
- `get_session()` — FastAPI dependency that yields a session per request, auto-commits on success and rolls back on exception

### Query Layer

`src/db/queries.py` contains all database operations as async functions that accept an `AsyncSession`. Notable patterns:

- `pg_insert(...).on_conflict_do_update(...)` for upserts (`find_or_create_source`, `upsert_nav_tree_cache`)
- `pg_insert(...).on_conflict_do_nothing(...)` for idempotent batch inserts (`insert_deep_links`)
- Paginated queries return `(list[dict], total_count)` tuples
- Aggregate stats use `func.count().filter(...)` for conditional counting
- All model instances are converted to dicts via `_model_to_dict()` before returning

---

## Alembic Migrations

Migrations are managed by Alembic with async support. The environment (`alembic/env.py`) reads `DATABASE_URL` from the environment and uses `async_engine_from_config`.

### Migration Chain

```
001 (baseline)
 └─► 220b48740c44 (add crawl columns)
      └─► 002 (TEXT → JSONB conversion)
           └─► 003 (fix double-encoded JSONB)
                └─► 004 (unique deep_links constraint)
```

### Migration Details

| Revision | Description |
|----------|-------------|
| `001` | Baseline: creates all 6 tables, indexes, `uuid-ossp` extension, and full-text search trigger |
| `220b48740c44` | Adds `child_urls` (TEXT[]) to `ingestion_jobs`, `search_vector` (TSVECTOR) + GIN index to `kb_files` |
| `002` | Converts `validation_breakdown` and `validation_issues` from TEXT to JSONB |
| `003` | Fixes double-encoded JSONB values (string scalars → proper objects) in `kb_files` and `nav_tree_cache` |
| `004` | Adds `UNIQUE(source_id, url)` constraint on `deep_links`, deduplicates existing rows |

### Running Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Stamp an existing database without running migrations
alembic stamp head

# Generate a new migration from model changes
alembic revision --autogenerate -m "description"

# Downgrade one step
alembic downgrade -1
```

### Configuration

`alembic.ini` sets `script_location = alembic` and leaves `sqlalchemy.url` empty — `env.py` overrides it from the `DATABASE_URL` environment variable at runtime.

---

## Status Enums

### File Status (`kb_files.status`)

| Value | Description |
|-------|-------------|
| `pending_review` | Initial state, awaiting validation or human review |
| `approved` | Validation score ≥ 0.7, or manually approved |
| `auto_rejected` | Validation score < 0.2 |
| `in_s3` | Approved and uploaded to S3 |
| `rejected` | Manually rejected by reviewer |

### Job Status (`ingestion_jobs.status`, `revalidation_jobs.status`)

| Value | Description |
|-------|-------------|
| `in_progress` | Job is running |
| `completed` | Job finished successfully |
| `failed` | Job encountered an unrecoverable error |

### Deep Link Status (`deep_links.status`)

| Value | Description |
|-------|-------------|
| `pending` | Discovered, awaiting user decision |
| `confirmed` | User confirmed for ingestion |
| `dismissed` | User dismissed |
| `ingested` | Ingestion job created and running/completed |
