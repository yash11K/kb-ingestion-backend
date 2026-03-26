# Design Document: SQLAlchemy + Docker Migration

## Overview

This design migrates the AEM Knowledge Base Ingestion System's database layer from raw `asyncpg` with hand-written SQL to SQLAlchemy async ORM with Alembic for schema management, and introduces Docker Compose with environment-specific profiles (`dev`, `preprod`).

The migration touches three layers:

1. **Model layer** — SQLAlchemy ORM models replace the implicit schema defined across 7 SQL migration files.
2. **Query layer** — `src/db/queries.py` is rewritten from raw SQL to SQLAlchemy ORM operations, swapping `asyncpg.Pool` for `AsyncSession`.
3. **Infrastructure layer** — Alembic replaces `run_migration.py` + raw SQL files; Docker Compose standardises how the app is run.

The database URL switches from `postgresql://` (asyncpg native) to `postgresql+asyncpg://` (SQLAlchemy dialect). No database-vendor-specific code is introduced — Neon and Aurora are interchangeable via connection string.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Keep function signatures in `queries.py` stable (swap `pool` → `session`) | Minimises blast radius across 10+ caller modules |
| Single Alembic baseline revision for migrations 001–007 | Avoids replaying 7 incremental ALTERs; new DBs get the final schema in one shot |
| No local Postgres in Docker Compose | The app always hits a remote DB (Neon now, Aurora later); connection string is the only knob |
| `statement_cache_size=0` on the engine | Required for Neon's PgBouncer-style connection pooler |
| ORM models live in `src/db/models.py`, engine/session in `src/db/session.py` | Clean separation; `connection.py` is retired |

---

## Architecture

```mermaid
graph TD
    subgraph "FastAPI Application"
        A[main.py lifespan] -->|creates| B[AsyncEngine]
        B -->|bound to| C[async_sessionmaker]
        C -->|yields per-request| D[AsyncSession]
        D -->|used by| E[src/db/queries.py]
        E -->|called by| F[src/api/*]
        E -->|called by| G[src/services/*]
        E -->|called by| H[src/tools/*]
    end

    subgraph "Schema Management"
        I[alembic/env.py] -->|reads| J[DATABASE_URL env var]
        I -->|uses| B
        K[alembic/versions/001_baseline.py] -->|creates| L[All 6 tables + indexes + triggers]
    end

    subgraph "Docker Compose"
        M[docker-compose.yml] -->|profile: dev| N[.env.dev]
        M -->|profile: preprod| O[.env.preprod]
        M -->|builds| P[Dockerfile]
        P -->|runs| A
    end

    B -->|connects to| Q[(Remote PostgreSQL<br/>Neon / Aurora)]
```

### Request Flow (After Migration)

1. FastAPI lifespan creates `AsyncEngine` + `async_sessionmaker` at startup.
2. Each request gets an `AsyncSession` via a FastAPI dependency (`get_session`).
3. Route handlers call `queries.py` functions, passing the session.
4. Session auto-commits on success, rolls back on exception.
5. On shutdown, `engine.dispose()` releases all connections.

### Migration Flow (Alembic)

1. `alembic upgrade head` reads `DATABASE_URL` from env.
2. For a fresh DB: applies the baseline revision (full schema).
3. For an existing DB: `alembic stamp head` marks the baseline as applied.
4. Future migrations are added as new revisions in `alembic/versions/`.

---

## Components and Interfaces

### 1. `src/db/models.py` (New)

SQLAlchemy ORM model classes for all 6 tables. Each model maps 1:1 to an existing table.

```python
# Key models and their relationships
class Source(Base):
    __tablename__ = "sources"
    # ... columns ...
    ingestion_jobs = relationship("IngestionJob", back_populates="source")
    kb_files = relationship("KBFile", back_populates="source")
    deep_links = relationship("DeepLink", back_populates="source")

class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    # ... columns ...
    source = relationship("Source", back_populates="ingestion_jobs")
    kb_files = relationship("KBFile", back_populates="job")

class KBFile(Base):
    __tablename__ = "kb_files"
    # ... columns ...
    source = relationship("Source", back_populates="kb_files")
    job = relationship("IngestionJob", back_populates="kb_files")

class RevalidationJob(Base):
    __tablename__ = "revalidation_jobs"

class NavTreeCache(Base):
    __tablename__ = "nav_tree_cache"

class DeepLink(Base):
    __tablename__ = "deep_links"
    source = relationship("Source", back_populates="deep_links")
    job = relationship("IngestionJob")
```

### 2. `src/db/session.py` (New — replaces `connection.py`)

Engine and session factory management.

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

async def init_engine(database_url: str) -> AsyncEngine:
    """Create AsyncEngine with asyncpg dialect and SSL."""
    return create_async_engine(
        database_url,
        connect_args={"ssl": "require", "statement_cache_size": 0},
        echo=False,
    )

def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a session per request."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### 3. `src/db/queries.py` (Rewritten)

All ~30 public functions are rewritten from raw SQL to SQLAlchemy ORM. The key change is the first parameter: `pool: asyncpg.Pool` → `session: AsyncSession`.

Example transformation:

```python
# Before
async def get_kb_file(pool: asyncpg.Pool, file_id: UUID) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM kb_files WHERE id = $1", file_id)
    if row is None:
        return None
    return _row_to_dict(row)

# After
async def get_kb_file(session: AsyncSession, file_id: UUID) -> dict | None:
    result = await session.execute(select(KBFile).where(KBFile.id == file_id))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _model_to_dict(row)
```

Return types remain `dict | None` and `tuple[list[dict], int]` to minimise caller changes. A `_model_to_dict()` helper replaces `_row_to_dict()`, handling JSONB columns the same way.

### 4. `src/main.py` (Modified)

Lifespan changes:
- Replace `create_pool()` / `close_pool()` with `init_engine()` / `engine.dispose()`.
- Store `session_factory` on `app.state` instead of `db_pool`.
- All services that currently accept `pool` in their constructors receive `session_factory` instead.

### 5. Caller Modules (Modified)

Every module that currently does `pool = request.app.state.db_pool` switches to obtaining an `AsyncSession`:

- **API routes** (`src/api/*.py`): Use `Depends(get_session)` or get session from `request.app.state.session_factory`.
- **Services** (`PipelineService`, `RevalidationService`, `KBQueryService`): Constructor takes `session_factory` instead of `pool`; creates sessions internally per operation.
- **Tools** (`file_context.py`): `set_db_pool()` → `set_session_factory()`.

### 6. `alembic/` (New)

```
alembic/
├── alembic.ini          # sqlalchemy.url = reads from env
├── env.py               # async migration runner
└── versions/
    └── 001_baseline.py  # full schema from migrations 001-007
```

### 7. Docker Files (New)

```
Dockerfile               # Python image, pip install, uvicorn entrypoint
docker-compose.yml       # app service with dev/preprod profiles
.env.dev                 # dev environment variables (gitignored)
.env.preprod             # preprod environment variables (gitignored)
.env.example             # template with placeholder values (committed)
```

---

## Data Models

### SQLAlchemy ORM Models

All models use `mapped_column` with explicit types. The `Base` declarative base uses `uuid-ossp` for default UUIDs.

#### `Source`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `uuid_generate_v4()` |
| url | Text | NOT NULL, UNIQUE |
| region | Text | NOT NULL |
| brand | Text | NOT NULL |
| nav_root_url | Text | nullable |
| nav_label | Text | nullable |
| nav_section | Text | nullable |
| page_path | Text | nullable |
| last_ingested_at | DateTime(tz) | nullable |
| created_at | DateTime(tz) | NOT NULL, default NOW() |
| updated_at | DateTime(tz) | NOT NULL, default NOW() |

Indexes: `idx_sources_region`, `idx_sources_brand`, `idx_sources_url`

#### `IngestionJob`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `uuid_generate_v4()` |
| source_url | Text | NOT NULL |
| source_id | UUID | FK → sources.id, nullable |
| status | Text | NOT NULL, default `'in_progress'` |
| total_nodes_found | Integer | nullable |
| files_created | Integer | NOT NULL, default 0 |
| files_auto_approved | Integer | NOT NULL, default 0 |
| files_pending_review | Integer | NOT NULL, default 0 |
| files_auto_rejected | Integer | NOT NULL, default 0 |
| duplicates_skipped | Integer | NOT NULL, default 0 |
| error_message | Text | nullable |
| child_urls | ARRAY(Text) | NOT NULL, default `{}` |
| max_depth | Integer | NOT NULL, default 0 |
| pages_crawled | Integer | NOT NULL, default 0 |
| current_depth | Integer | NOT NULL, default 0 |
| started_at | DateTime(tz) | NOT NULL, default NOW() |
| completed_at | DateTime(tz) | nullable |

Indexes: `idx_ingestion_jobs_status`, `idx_ingestion_jobs_source_id`

#### `KBFile`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `uuid_generate_v4()` |
| filename | Text | NOT NULL |
| title | Text | NOT NULL |
| content_type | Text | NOT NULL |
| content_hash | Text | NOT NULL |
| source_url | Text | NOT NULL |
| component_type | Text | NOT NULL |
| aem_node_id | Text | nullable |
| md_content | Text | NOT NULL |
| doc_type | Text | nullable |
| modify_date | DateTime(tz) | nullable |
| parent_context | Text | nullable |
| region | Text | NOT NULL |
| brand | Text | NOT NULL |
| key | Text | nullable |
| namespace | Text | nullable |
| validation_score | Float | nullable |
| validation_breakdown | JSONB | nullable |
| validation_issues | JSONB | nullable |
| status | Text | NOT NULL, default `'pending_review'` |
| s3_bucket | Text | nullable |
| s3_key | Text | nullable |
| s3_uploaded_at | DateTime(tz) | nullable |
| reviewed_by | Text | nullable |
| reviewed_at | DateTime(tz) | nullable |
| review_notes | Text | nullable |
| source_id | UUID | FK → sources.id, nullable |
| job_id | UUID | FK → ingestion_jobs.id, nullable |
| search_vector | TSVector | nullable, GIN index |
| created_at | DateTime(tz) | NOT NULL, default NOW() |
| updated_at | DateTime(tz) | NOT NULL, default NOW() |

Indexes: `idx_kb_files_content_hash`, `idx_kb_files_status`, `idx_kb_files_region`, `idx_kb_files_brand`, `idx_kb_files_source_url`, `idx_kb_files_content_type`, `idx_kb_files_doc_type`, `idx_kb_files_created_at`, `idx_kb_files_source_id`, `idx_kb_files_job_id`, `idx_kb_files_search_vector` (GIN)

Trigger: `trg_kb_files_search_vector` — auto-updates `search_vector` on INSERT/UPDATE of `title` or `md_content`.

#### `RevalidationJob`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `uuid_generate_v4()` |
| status | Text | NOT NULL, default `'in_progress'` |
| total_files | Integer | NOT NULL |
| completed | Integer | NOT NULL, default 0 |
| failed | Integer | NOT NULL, default 0 |
| not_found | Integer | NOT NULL, default 0 |
| error_message | Text | nullable |
| started_at | DateTime(tz) | NOT NULL, default NOW() |
| completed_at | DateTime(tz) | nullable |

Indexes: `idx_revalidation_jobs_status`

#### `NavTreeCache`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `gen_random_uuid()` |
| root_url | Text | NOT NULL, UNIQUE |
| brand | Text | NOT NULL |
| region | Text | NOT NULL |
| tree_data | JSONB | NOT NULL |
| fetched_at | DateTime(tz) | NOT NULL, default NOW() |
| expires_at | DateTime(tz) | NOT NULL |

#### `DeepLink`

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default `gen_random_uuid()` |
| source_id | UUID | FK → sources.id, nullable |
| job_id | UUID | FK → ingestion_jobs.id, nullable |
| url | Text | NOT NULL |
| model_json_url | Text | NOT NULL |
| anchor_text | Text | nullable |
| found_in_node | Text | nullable |
| found_in_page | Text | NOT NULL |
| status | Text | NOT NULL, default `'pending'` |
| created_at | DateTime(tz) | NOT NULL, default NOW() |

Indexes: `idx_deep_links_source`, `idx_deep_links_status`, `idx_deep_links_job`

### Database URL Format Change

| Before (asyncpg native) | After (SQLAlchemy dialect) |
|---|---|
| `postgresql://user:pass@host/db?sslmode=require` | `postgresql+asyncpg://user:pass@host/db?ssl=require` |

The `src/config.py` `Settings` class accepts the new format. SSL parameters are forwarded to the asyncpg driver via `connect_args`.

