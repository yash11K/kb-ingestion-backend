# Requirements Document

## Introduction

This feature migrates the AEM Knowledge Base Ingestion System's database layer from raw asyncpg with manual SQL to SQLAlchemy (async) with Alembic for schema migrations. It also introduces Docker Compose with environment-specific configurations (dev, preprod) to standardize how the application is run across environments. The existing 7 SQL migrations (001–007) will be represented as an Alembic baseline. The application connects to remote Neon PostgreSQL today and will move to Aurora PostgreSQL in the future; the connection string is the only thing that changes between databases.

## Glossary

- **Application**: The FastAPI-based AEM Knowledge Base Ingestion System defined in `src/main.py`
- **DB_Layer**: The database access code currently in `src/db/connection.py` and `src/db/queries.py`
- **SQLAlchemy_Engine**: The SQLAlchemy `AsyncEngine` instance that replaces the raw asyncpg connection pool
- **SQLAlchemy_Session**: The SQLAlchemy `AsyncSession` used for executing ORM queries
- **Alembic_Runner**: The Alembic migration tool configured to manage schema versioning
- **Docker_Compose**: The Docker Compose configuration that orchestrates the Application container
- **Env_File**: An environment-specific dotenv file (`.env.dev`, `.env.preprod`) loaded by Docker_Compose
- **Baseline_Migration**: A single Alembic revision that captures the full current schema (migrations 001–007) as the starting point

---

## Requirements

### Requirement 1: SQLAlchemy Model Definitions

**User Story:** As a developer, I want the database tables defined as SQLAlchemy ORM models, so that I can interact with the database using Python objects instead of raw SQL strings.

#### Acceptance Criteria

1. THE DB_Layer SHALL define SQLAlchemy ORM model classes for all six existing tables: `kb_files`, `ingestion_jobs`, `revalidation_jobs`, `sources`, `nav_tree_cache`, and `deep_links`
2. WHEN a SQLAlchemy model is defined, THE DB_Layer SHALL map every column, type, default, and constraint to match the current PostgreSQL schema produced by migrations 001 through 007
3. THE DB_Layer SHALL define all foreign key relationships between models: `ingestion_jobs.source_id → sources.id`, `kb_files.source_id → sources.id`, `kb_files.job_id → ingestion_jobs.id`, `deep_links.source_id → sources.id`, `deep_links.job_id → ingestion_jobs.id`
4. THE DB_Layer SHALL define all existing indexes as SQLAlchemy index declarations on the corresponding model columns
5. THE DB_Layer SHALL define the `search_vector` column on the `kb_files` model as a `TSVector` type with the associated GIN index

---

### Requirement 2: Async SQLAlchemy Engine and Session Management

**User Story:** As a developer, I want the application to use SQLAlchemy's async engine and session factory, so that database connections are managed through a standard ORM layer instead of a raw asyncpg pool.

#### Acceptance Criteria

1. THE DB_Layer SHALL create an `AsyncEngine` using `create_async_engine` with the `asyncpg` dialect and SSL required
2. THE DB_Layer SHALL create an `async_sessionmaker` bound to the AsyncEngine for producing `AsyncSession` instances
3. WHEN the Application starts, THE Application SHALL initialize the SQLAlchemy_Engine using the `DATABASE_URL` from the active Env_File
4. WHEN the Application shuts down, THE Application SHALL dispose of the SQLAlchemy_Engine to release all connections
5. THE DB_Layer SHALL expose an async dependency (compatible with FastAPI `Depends`) that yields an `AsyncSession` per request and commits or rolls back on completion
6. THE DB_Layer SHALL configure the `AsyncEngine` with `statement_cache_size=0` to maintain compatibility with PgBouncer-style connection poolers used by Neon PostgreSQL

---

### Requirement 3: Query Layer Migration

**User Story:** As a developer, I want all raw SQL queries in `src/db/queries.py` replaced with SQLAlchemy ORM operations, so that the query layer is type-safe and maintainable.

#### Acceptance Criteria

1. THE DB_Layer SHALL rewrite every function in `src/db/queries.py` to use SQLAlchemy ORM queries via `AsyncSession` instead of raw asyncpg pool operations
2. THE DB_Layer SHALL preserve the existing function signatures (names, parameters, return types) for all public query functions so that callers in `src/api/`, `src/services/`, and `src/tools/` require minimal changes
3. WHEN a query function currently accepts an `asyncpg.Pool` parameter, THE DB_Layer SHALL replace that parameter with an `AsyncSession` parameter
4. THE DB_Layer SHALL preserve the existing JSONB serialization and deserialization behavior for `validation_breakdown`, `validation_issues`, and `tree_data` columns
5. WHEN a query function uses dynamic SET clause construction (as in `update_kb_file_status` and `update_ingestion_job`), THE DB_Layer SHALL implement equivalent dynamic update logic using SQLAlchemy's `update()` construct

---

### Requirement 4: Caller Migration

**User Story:** As a developer, I want all modules that call the DB layer updated to use SQLAlchemy sessions, so that the entire application uses a single consistent database access pattern.

#### Acceptance Criteria

1. WHEN a module in `src/api/`, `src/services/`, or `src/tools/` calls a function from `src/db/queries.py`, THE Application SHALL pass an `AsyncSession` instead of an `asyncpg.Pool`
2. THE Application SHALL replace the `app.state.db_pool` attribute with a session factory or equivalent SQLAlchemy construct in the FastAPI lifespan
3. THE Application SHALL update `reset_all.py` to use SQLAlchemy_Session for table truncation instead of raw asyncpg connections
4. THE Application SHALL remove the `asyncpg` direct dependency from `requirements.txt` after migration is complete (asyncpg remains as the SQLAlchemy async driver)

---

### Requirement 5: Alembic Configuration and Baseline Migration

**User Story:** As a developer, I want Alembic configured for async migrations with a baseline that represents the current schema, so that future schema changes are tracked and applied automatically.

#### Acceptance Criteria

1. THE Alembic_Runner SHALL be initialized with an `alembic/` directory at the project root containing `alembic.ini`, `env.py`, and a `versions/` folder
2. THE Alembic_Runner SHALL configure `env.py` to use `async` migration mode with the SQLAlchemy `AsyncEngine`
3. THE Alembic_Runner SHALL read the database URL from the `DATABASE_URL` environment variable, not from a hardcoded value
4. THE Alembic_Runner SHALL contain a single Baseline_Migration revision that represents the complete schema produced by SQL migrations 001 through 007, including all tables, columns, indexes, constraints, triggers, and extensions
5. WHEN `alembic upgrade head` is run against an empty database, THE Alembic_Runner SHALL produce a schema identical to the one created by running SQL migrations 001 through 007 sequentially
6. WHEN `alembic upgrade head` is run against a database that already has the current schema, THE Alembic_Runner SHALL apply no changes (the baseline is marked as already applied via `alembic stamp head`)
7. THE Alembic_Runner SHALL include the `search_vector` trigger function (`kb_files_search_vector_update`) in the baseline migration using raw SQL execution within the Alembic `op.execute()` call

---

### Requirement 6: Removal of Legacy Migration Infrastructure

**User Story:** As a developer, I want the old manual migration files and runner removed, so that there is a single source of truth for schema management.

#### Acceptance Criteria

1. WHEN the Alembic baseline is verified, THE Application SHALL remove the `src/db/migrations/` directory containing SQL files 001 through 007
2. WHEN the Alembic baseline is verified, THE Application SHALL remove the `run_migration.py` script from the project root
3. THE Application SHALL update any documentation references that point to the old migration runner or SQL migration files

---

### Requirement 7: Docker Compose Configuration

**User Story:** As a developer, I want a Docker Compose setup with environment-specific profiles, so that I can run the application consistently across dev and preprod environments.

#### Acceptance Criteria

1. THE Docker_Compose SHALL define a `docker-compose.yml` file at the project root with a service named `app` that builds and runs the Application
2. THE Docker_Compose SHALL define a `Dockerfile` at the project root that builds the Application image using a Python base image, installs dependencies from `requirements.txt`, and runs the FastAPI server via uvicorn
3. THE Docker_Compose SHALL support two profiles: `dev` and `preprod`
4. WHEN the `dev` profile is active, THE Docker_Compose SHALL load environment variables from `.env.dev`
5. WHEN the `preprod` profile is active, THE Docker_Compose SHALL load environment variables from `.env.preprod`
6. THE Docker_Compose SHALL forward the `DATABASE_URL` from the active Env_File to the Application container without modification
7. THE Docker_Compose SHALL forward AWS credential environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`) from the host environment to the Application container

---

### Requirement 8: Environment-Specific Configuration Files

**User Story:** As a developer, I want separate `.env` files per environment, so that I can switch between dev and preprod databases and settings by choosing a profile.

#### Acceptance Criteria

1. THE Application SHALL provide `.env.dev` and `.env.preprod` template files at the project root
2. WHEN an Env_File is loaded, THE Application SHALL read `DATABASE_URL`, `S3_BUCKET_NAME`, `BEDROCK_MODEL_ID`, `BEDROCK_KB_ID`, and all other settings defined in `src/config.py` from that file
3. THE Application SHALL update `src/config.py` to accept a `DATABASE_URL` in SQLAlchemy format (`postgresql+asyncpg://...`) instead of the raw asyncpg format (`postgresql://...`)
4. THE Application SHALL add `.env.dev` and `.env.preprod` to `.gitignore` to prevent credentials from being committed
5. THE Application SHALL provide a `.env.example` file documenting all required and optional environment variables with placeholder values

---

### Requirement 9: Database URL Compatibility

**User Story:** As a developer, I want the database URL to work seamlessly with both Neon PostgreSQL and future Aurora PostgreSQL, so that switching databases requires only a URL change.

#### Acceptance Criteria

1. THE DB_Layer SHALL accept a `DATABASE_URL` using the `postgresql+asyncpg://` scheme as required by SQLAlchemy's async dialect
2. WHEN the `DATABASE_URL` contains SSL parameters (such as `sslmode=require`), THE SQLAlchemy_Engine SHALL pass those parameters to the underlying asyncpg driver
3. THE DB_Layer SHALL not contain any database-vendor-specific logic (no Neon-specific or Aurora-specific code paths)
4. IF the `DATABASE_URL` is missing or malformed, THEN THE Application SHALL fail at startup with a descriptive error message indicating the expected URL format
