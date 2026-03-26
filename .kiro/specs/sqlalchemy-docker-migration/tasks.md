# Implementation Plan: SQLAlchemy + Docker Migration

## Overview

Migrate the database layer from raw asyncpg to SQLAlchemy async ORM with Alembic, update all callers, add Docker Compose with dev/preprod profiles, and remove legacy migration infrastructure. Each task builds incrementally so the app remains functional at each checkpoint.

## Tasks

- [x] 1. Add SQLAlchemy and Alembic dependencies
  - Add `sqlalchemy[asyncio]>=2.0` and `alembic>=1.13` to `requirements.txt`
  - Keep `asyncpg>=0.29` (now used as SQLAlchemy's async driver, no longer imported directly by app code)
  - _Requirements: 2.1, 4.4_

- [x] 2. Create SQLAlchemy ORM models and session management
  - [x] 2.1 Create `src/db/models.py` with all 6 ORM model classes
    - Define `Base` declarative base with `uuid-ossp` / `gen_random_uuid()` defaults
    - Implement `Source`, `IngestionJob`, `KBFile`, `RevalidationJob`, `NavTreeCache`, `DeepLink` models
    - Map every column, type, default, constraint, and foreign key per the design data models
    - Declare all indexes as `Index()` on the corresponding columns (including GIN index on `search_vector`)
    - Define `search_vector` column as `TSVector` type on `KBFile`
    - Define all `relationship()` declarations: `Source` ↔ `IngestionJob`, `Source` ↔ `KBFile`, `Source` ↔ `DeepLink`, `IngestionJob` ↔ `KBFile`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 2.2 Create `src/db/session.py` with engine and session factory
    - Implement `init_engine(database_url)` using `create_async_engine` with `asyncpg` dialect, `ssl=require`, and `statement_cache_size=0` in `connect_args`
    - Implement `create_session_factory(engine)` returning `async_sessionmaker[AsyncSession]`
    - Implement `get_session()` async generator as a FastAPI `Depends`-compatible dependency that yields `AsyncSession`, commits on success, rolls back on exception
    - Store module-level `session_factory` reference for use by `get_session`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 9.1, 9.2_

  - [ ]* 2.3 Write unit tests for `src/db/session.py`
    - Test that `init_engine` passes correct `connect_args` (ssl, statement_cache_size)
    - Test that `get_session` commits on success and rolls back on exception
    - _Requirements: 2.1, 2.5, 2.6_

- [x] 3. Rewrite query layer to SQLAlchemy ORM
  - [x] 3.1 Rewrite `src/db/queries.py` — kb_files queries
    - Replace `pool: asyncpg.Pool` parameter with `session: AsyncSession` on: `insert_kb_file`, `update_kb_file_status`, `get_kb_file`, `list_kb_files`, `find_by_content_hash`, `list_review_queue`
    - Replace raw SQL with SQLAlchemy ORM `select()`, `insert()`, `update()` constructs
    - Implement `_model_to_dict()` helper replacing `_row_to_dict()`, preserving JSONB serialization/deserialization for `validation_breakdown`, `validation_issues`
    - Implement dynamic SET clause logic in `update_kb_file_status` using SQLAlchemy `update().values()`
    - Preserve existing return types (`dict | None`, `tuple[list[dict], int]`)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 Rewrite `src/db/queries.py` — sources queries
    - Rewrite `find_or_create_source`, `find_or_create_source_enriched`, `get_source`, `list_sources`, `update_source_last_ingested`, `list_jobs_for_source`, `get_source_stats`
    - Replace `pool` parameter with `session` on all functions
    - Preserve return types and function signatures
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 3.3 Rewrite `src/db/queries.py` — ingestion_jobs queries
    - Rewrite `insert_ingestion_job`, `update_ingestion_job`, `update_crawl_progress`, `get_ingestion_job`, `list_ingestion_jobs`, `get_active_jobs`
    - Implement dynamic update logic in `update_ingestion_job` using SQLAlchemy `update().values()`
    - Replace `pool` parameter with `session` on all functions
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

  - [x] 3.4 Rewrite `src/db/queries.py` — revalidation, nav_tree_cache, deep_links queries
    - Rewrite `insert_revalidation_job`, `update_revalidation_job`, `get_revalidation_job`
    - Rewrite `upsert_nav_tree_cache`, `get_nav_tree_cache` preserving JSONB handling for `tree_data`
    - Rewrite `insert_deep_links`, `list_deep_links`, `list_all_deep_links`, `bulk_update_deep_link_status`, `insert_deep_link_ingestion_jobs`
    - Rewrite `get_stats`
    - Replace `pool` parameter with `session` on all functions
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.5 Write unit tests for rewritten query functions
    - Test `_model_to_dict` JSONB serialization round-trip for `validation_breakdown`, `validation_issues`, `tree_data`
    - Test dynamic update logic in `update_kb_file_status` and `update_ingestion_job`
    - _Requirements: 3.4, 3.5_

- [x] 4. Checkpoint — Verify query layer compiles cleanly
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Update `src/main.py` lifespan and caller modules
  - [x] 5.1 Update `src/main.py` lifespan to use SQLAlchemy engine and session factory
    - Replace `create_pool()` / `close_pool()` with `init_engine()` / `engine.dispose()`
    - Store `session_factory` on `app.state` instead of `db_pool`
    - Update `ValidatorAgent` and `PipelineService` constructors to receive `session_factory`
    - Update `RevalidationService` and `KBQueryService` constructors to receive `session_factory`
    - Replace `set_context_db_pool(pool)` with `set_session_factory(session_factory)` for `file_context` tool
    - Add startup validation: fail with descriptive error if `DATABASE_URL` is missing or malformed
    - _Requirements: 2.3, 2.4, 4.2, 9.4_

  - [x] 5.2 Update API route modules to use `AsyncSession` instead of `db_pool`
    - Update `src/api/ingest.py`: replace `request.app.state.db_pool` with session from `request.app.state.session_factory`, pass `session` to all query calls
    - Update `src/api/files.py`: same pattern
    - Update `src/api/queue.py`: same pattern, including background task `_upload_accepted_file`
    - Update `src/api/sources.py`: same pattern
    - Update `src/api/stats.py`: same pattern
    - Update `src/api/revalidate.py`: same pattern for `insert_revalidation_job` and `get_revalidation_job` calls
    - Update `src/api/nav.py`: same pattern for all nav_tree_cache and deep_links query calls
    - Update `src/api/context.py`: same pattern for `get_kb_file` and `list_deep_links` calls
    - _Requirements: 4.1_

  - [x] 5.3 Update service modules to use `session_factory` instead of `db_pool`
    - Update `src/services/pipeline.py` (`PipelineService`): change constructor `pool` → `session_factory`, create sessions internally per operation
    - Update `src/services/revalidation.py` (`RevalidationService`): change constructor `db_pool` → `session_factory`, create sessions per revalidation operation
    - Update `src/services/kb_query.py` (`KBQueryService`): change constructor `pool` → `session_factory`, create sessions for search/chat queries
    - _Requirements: 4.1_

  - [x] 5.4 Update tool modules to use `session_factory` instead of `db_pool`
    - Update `src/tools/file_context.py`: rename `set_db_pool()` → `set_session_factory()`, update `_db_pool` → `_session_factory`, create session in `get_file_context` tool
    - Update `src/tools/duplicate_checker.py`: rename `set_db_pool()` → `set_session_factory()`, update module-level variable, create session in `check_duplicate` tool
    - Update `src/agents/validator.py`: update import and call to `set_db_pool` → `set_session_factory`
    - _Requirements: 4.1_

  - [ ]* 5.5 Update existing tests for new session-based interfaces
    - Update `tests/test_db/test_connection.py` → rename to `tests/test_db/test_session.py`, test `init_engine` and `get_session`
    - Update `tests/conftest.py` if it references `db_pool`
    - Update any test mocks that patch `asyncpg.Pool` to use `AsyncSession` mocks
    - _Requirements: 4.1, 4.2_

- [x] 6. Checkpoint — Verify all caller migrations compile cleanly
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Update configuration for SQLAlchemy URL format
  - [x] 7.1 Update `src/config.py` to accept SQLAlchemy-format DATABASE_URL
    - Update `database_url` field comment to indicate `postgresql+asyncpg://` scheme
    - Add a `field_validator` on `database_url` that checks for `postgresql+asyncpg://` prefix and raises a descriptive error if malformed
    - _Requirements: 8.3, 9.1, 9.4_

  - [x] 7.2 Remove direct `asyncpg` import from application code
    - Remove `import asyncpg` from `src/db/queries.py`, `src/services/revalidation.py`, `src/services/kb_query.py`, `src/tools/file_context.py`, `src/tools/duplicate_checker.py`, `src/agents/validator.py`
    - Verify no remaining direct `asyncpg` imports outside of SQLAlchemy driver usage
    - _Requirements: 4.4, 9.3_

- [x] 8. Set up Alembic for async migrations with baseline
  - [x] 8.1 Initialize Alembic directory structure
    - Create `alembic.ini` at project root configured to read `sqlalchemy.url` from env
    - Create `alembic/env.py` with async migration runner using `AsyncEngine`, reading `DATABASE_URL` from environment
    - Create `alembic/versions/` directory
    - Import `Base.metadata` from `src/db/models` in `env.py` for autogenerate support
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 8.2 Create baseline migration revision
    - Generate a single Alembic revision `001_baseline.py` representing the complete schema from migrations 001–007
    - Include all 6 tables with all columns, types, defaults, constraints, and foreign keys
    - Include all indexes (including GIN index on `search_vector`)
    - Include `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"` via `op.execute()`
    - Include the `kb_files_search_vector_update()` trigger function and `trg_kb_files_search_vector` trigger via `op.execute()`
    - Write a proper `downgrade()` that drops all tables, triggers, and extensions
    - _Requirements: 5.4, 5.5, 5.7_

  - [ ]* 8.3 Write a test verifying Alembic env.py loads DATABASE_URL from environment
    - Mock environment variable and verify engine creation
    - _Requirements: 5.3_

- [x] 9. Checkpoint — Verify Alembic configuration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Create Docker Compose and environment configuration
  - [x] 10.1 Create `Dockerfile`
    - Use Python base image, copy source, install dependencies from `requirements.txt`
    - Set uvicorn entrypoint: `uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8000`
    - _Requirements: 7.2_

  - [x] 10.2 Create `docker-compose.yml` with dev/preprod profiles
    - Define `app` service that builds from the Dockerfile
    - Configure `dev` profile loading `.env.dev` and `preprod` profile loading `.env.preprod`
    - Forward `DATABASE_URL` from the active env file to the container
    - Forward AWS credential environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`) from host
    - _Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [x] 10.3 Create environment template files
    - Create `.env.example` with all required/optional variables from `src/config.py` using placeholder values, with `DATABASE_URL` showing `postgresql+asyncpg://` format
    - Create `.env.dev` and `.env.preprod` as copies of `.env.example` (gitignored)
    - Add `.env.dev` and `.env.preprod` to `.gitignore`
    - _Requirements: 8.1, 8.2, 8.4, 8.5_

- [x] 11. Remove legacy migration infrastructure
  - [x] 11.1 Delete old migration files and runner
    - Delete `src/db/migrations/` directory (SQL files 001–007)
    - Delete `run_migration.py` from project root
    - Delete `src/db/connection.py` (replaced by `src/db/session.py`)
    - _Requirements: 6.1, 6.2_

  - [x] 11.2 Update `reset_all.py` to use SQLAlchemy session
    - Replace `asyncpg.connect()` with SQLAlchemy `AsyncSession` for table truncation
    - Use `create_async_engine` + session to execute TRUNCATE statements
    - _Requirements: 4.3_

  - [x] 11.3 Update documentation references
    - Update `doc/operations.md`, `doc/configuration.md`, or any docs referencing `run_migration.py` or `src/db/migrations/`
    - Update `BACKEND_GUIDE.md` if it references the old migration workflow
    - _Requirements: 6.3_

- [x] 12. Final checkpoint — Full integration verification
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- The query layer rewrite (task 3) preserves function names and return types to minimize caller changes
- Callers swap `pool` → `session` parameter; services swap `pool` → `session_factory` constructor arg
- No local Postgres in Docker Compose — the app always connects to a remote DB via `DATABASE_URL`
