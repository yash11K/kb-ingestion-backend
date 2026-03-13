# Implementation Plan: Deep Crawl Ingestion

## Overview

Transform the AEM KB Ingestion System from single-page processing into an opt-in recursive BFS crawler. Implementation proceeds bottom-up: utility functions first, then schema/config changes, then the core crawl loop, then the preview endpoint, and finally wiring and integration. Each task builds on the previous ones so there is no orphaned code.

## Tasks

- [x] 1. Create URL inference utility module
  - [x] 1.1 Create `src/utils/url_inference.py` with `normalize_url`, `infer_brand`, `infer_region`, `infer_namespace`, and `normalize_for_matching` functions
    - `normalize_url(url)`: strip trailing slashes, query params, fragments
    - `infer_brand(url)`: extract brand from domain (`www.avis.com` → `avis`)
    - `infer_region(url, locale_map)`: extract locale from path, map to region code
    - `infer_namespace(url, namespace_list)`: match path segments against namespace list, default `"general"`
    - `normalize_for_matching(url)`: normalize both relative paths and full model.json URLs to a canonical form for confirmed_urls matching
    - _Requirements: 4.5, 15.1, 15.2, 15.3, 15.5, 13.5_

  - [ ]* 1.2 Write property tests for URL inference functions
    - **Property 4: Cycle detection via URL normalization** — `normalize_url` is idempotent
    - **Property 11: Brand inference from URL** — `www.{brand}.com` → `{brand}`
    - **Property 12: Region inference from URL** — locale path segment maps to correct region code
    - **Property 13: Namespace inference from URL** — first matching path segment or `"general"`
    - **Property 6: Confirmed URLs normalization** — relative paths and full model.json URLs normalize to same form
    - **Validates: Requirements 4.5, 15.1, 15.2, 15.3, 15.5, 13.5**

- [x] 2. Update Settings and schema models
  - [x] 2.1 Add crawl configuration fields to `Settings` in `src/config.py`
    - Add `max_crawl_depth` (int, default 3, from `MAX_CRAWL_DEPTH` env var)
    - Add `namespace_list` (list of recognized namespace strings)
    - Add `locale_region_map` (dict mapping locale codes to region codes, from `LOCALE_REGION_MAP` env var as JSON)
    - Add `component_denylist_defaults` and `component_allowlist_defaults` (from `AEM_COMPONENT_DENYLIST` / `AEM_COMPONENT_ALLOWLIST` env vars)
    - _Requirements: 2.1, 2.3, 14.4, 15.4_

  - [x] 2.2 Update `IngestRequest` in `src/models/schemas.py`
    - Add `max_depth: int = Field(default=0, ge=0)` field
    - Add `confirmed_urls: list[str] | None = None` field
    - Remove `brand`, `region`, and `component_types` fields if present
    - _Requirements: 1.1, 1.4, 13.1, 14.5, 15.6_

  - [x] 2.3 Update `MarkdownFile` model in `src/models/schemas.py`
    - Add `key`, `namespace`, `parent_context`, `brand`, `region` fields
    - Remove `aem_node_id`, `modify_date` fields if present
    - _Requirements: 16.2, 16.3, 16.4_

  - [x] 2.4 Update `IngestionJobResponse` in `src/models/schemas.py`
    - Add `max_depth: int = 0`, `pages_crawled: int = 1`, `current_depth: int = 0` fields
    - _Requirements: 8.1, 8.2_

  - [x] 2.5 Create `NavPreviewResponse` and `NavPreviewItem` models in `src/models/schemas.py`
    - `NavPreviewItem`: `url`, `depth`, `parent_url`
    - `NavPreviewResponse`: `root_url`, `total_urls`, `urls_by_depth`, `summary`
    - _Requirements: 11.2, 11.3_

  - [ ]* 2.6 Write property tests for schema validation
    - **Property 1: Negative depth rejection** — `max_depth < 0` raises validation error
    - **Property 2: Depth clamping** — effective depth = `min(max_depth, max_crawl_depth)`
    - **Validates: Requirements 1.4, 1.5, 2.2**

- [x] 3. Database migration for crawl tracking
  - [x] 3.1 Create migration `src/db/migrations/003_crawl_tracking.sql`
    - Add `max_depth INTEGER NOT NULL DEFAULT 0` to `ingestion_jobs`
    - Add `pages_crawled INTEGER NOT NULL DEFAULT 0` to `ingestion_jobs`
    - Add `current_depth INTEGER NOT NULL DEFAULT 0` to `ingestion_jobs`
    - Add `key TEXT` and `namespace TEXT` to `kb_files`
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 3.2 Update DB queries in `src/db/queries.py` to read/write new columns
    - Update job creation to persist `max_depth`
    - Add query to update `pages_crawled` and `current_depth` during crawl
    - Update job response query to include new fields
    - _Requirements: 7.4, 7.5, 8.1_

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement component filtering with allowlist/denylist
  - [x] 5.1 Update `src/tools/filter_components.py` to use configurable allowlist/denylist
    - Implement `filter_by_component_type_direct` using Settings allowlist/denylist
    - Skip nodes whose only meaningful fields are i18n keys, `dataLayer`, `appliedCssClassNames`, or only `id` and `:type` with no text content
    - Skip any component whose `:type` suffix contains `react` or `widget`
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [ ]* 5.2 Write property test for component filtering
    - **Property 17: Component filtering by allowlist/denylist** — only allowlisted, non-denylisted nodes with real content pass through
    - **Validates: Requirements 14.1, 14.2, 14.3**

- [x] 6. Update PostProcessor and S3 upload for revised metadata
  - [x] 6.1 Update `PostProcessor.process()` in `src/agents/extractor.py`
    - Accept `namespace`, `brand`, `region`, and optional `parent_url` parameters
    - Generate revised YAML frontmatter with `key`, `namespace`, `brand`, `region`, `source_url`, `parent_context`, `title`
    - Exclude AEM-specific fields (`:type` paths, `dataLayer`, `repo:modifyDate`, `aem_node_id`)
    - _Requirements: 9.1, 9.2, 16.2, 16.3, 16.4_

  - [ ]* 6.2 Write property tests for PostProcessor
    - **Property 15: Frontmatter correctness** — YAML frontmatter contains exactly the required fields and no AEM-specific fields
    - **Property 16: Parent context enrichment** — child URLs have `parent_context` set; root URLs have it empty
    - **Validates: Requirements 9.1, 9.2, 16.2, 16.3, 16.4**

  - [x] 6.3 Update `S3UploadService._build_key()` in `src/services/s3_upload.py`
    - Build S3 key as `{brand}/{region}/{namespace}/{filename}`
    - _Requirements: 16.1_

  - [ ]* 6.4 Write property test for S3 key structure
    - **Property 14: S3 key structure** — key equals `{brand}/{region}/{namespace}/{filename}`
    - **Validates: Requirements 16.1**

- [x] 7. Implement BFS crawl loop in PipelineService
  - [x] 7.1 Extract `_process_single_url` method in `src/services/pipeline.py`
    - Move per-URL fetch → extract → validate → route → upload logic into `_process_single_url`
    - Accept `parent_url` parameter for child URL context enrichment
    - Return list of discovered child URLs
    - Accumulate file counters (created, approved, pending_review, rejected, duplicates) into job-level totals
    - On error: log, emit `crawl_page_error` SSE event, return empty child list
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 12.1, 12.2_

  - [x] 7.2 Implement `_run_pipeline` BFS loop in `src/services/pipeline.py`
    - Initialize `bfs_queue: deque[(str, int)]` with seed URL at depth 0
    - Initialize `visited: set[str]` for cycle detection using `normalize_url`
    - Dequeue URL, check visited set, call `_process_single_url`, enqueue child URLs at `current_depth + 1`
    - Apply depth limit: skip enqueueing when `depth + 1 > effective_depth`
    - Apply `confirmed_urls` filter: only enqueue URLs present in confirmed list (when provided)
    - Fall back to crawling all discovered child URLs when `confirmed_urls` is null/empty and `max_depth > 0`
    - Emit `crawl_page_start`, `crawl_page_complete`, `crawl_page_skipped` SSE events
    - Emit `crawl_summary` event after loop completes with totals
    - Update `pages_crawled` and `current_depth` in DB during crawl
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 6.1, 6.2, 6.3, 6.4, 7.4, 12.3, 12.4, 13.2, 13.3, 13.4_

  - [x] 7.3 Implement `run()` entry point in `src/services/pipeline.py`
    - Call `infer_brand`, `infer_region`, `infer_namespace` from `url_inference`
    - Clamp `max_depth` to `settings.max_crawl_depth`
    - Persist effective `max_depth` in job record
    - Delegate to `_run_pipeline`
    - _Requirements: 1.2, 1.3, 1.5, 2.2, 7.5, 15.1, 15.2, 15.3_

  - [ ]* 7.4 Write property tests for BFS crawl logic
    - **Property 3: BFS ordering and depth limiting** — all depth-N URLs processed before depth N+1; no URL beyond max_depth
    - **Property 5: Confirmed URLs filtering** — only intersection of discovered and confirmed URLs enqueued
    - **Property 7: Confirmed URLs respect depth and cycle limits** — confirmed URLs still filtered by depth/cycle detection
    - **Property 8: Error resilience continues crawl** — failed URLs don't abort remaining queue
    - **Property 9: Counter accumulation** — job totals equal sum of per-URL counts
    - **Property 10: SSE crawl events** — correct count and types of events emitted
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 4.3, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 12.1, 12.2, 12.3, 12.4, 13.2, 13.3, 13.4**

- [x] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement preview endpoint
  - [x] 9.1 Create `src/api/preview.py` with `GET /preview/nav` endpoint
    - Accept `url` query parameter and optional `max_depth` (default 1)
    - Recursively fetch AEM JSON, filter components, extract child URLs up to `max_depth`
    - Apply same URL normalization and cycle detection as crawl loop
    - Return `NavPreviewResponse` with URL tree grouped by depth level
    - Return 502 for unreachable URLs or invalid JSON
    - Must complete within `aem_request_timeout` — no ExtractorAgent or ValidatorAgent invocation
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [x] 9.2 Register preview router in `src/api/router.py`
    - Add the preview router to the main API router
    - _Requirements: 11.1_

  - [ ]* 9.3 Write property test for preview endpoint
    - **Property 19: Preview matches crawl discovery** — preview returns same URL set as crawl loop would discover
    - **Validates: Requirements 11.6**

- [x] 10. Wire backward compatibility and update ingest endpoint
  - [x] 10.1 Update `POST /ingest` handler in `src/api/ingest.py`
    - Pass `max_depth` and `confirmed_urls` from request to pipeline
    - Ensure omitting `max_depth` defaults to 0 (current behavior preserved)
    - Ensure `IngestResponse` model remains unchanged (`source_id`, `job_id`, `status`)
    - _Requirements: 1.2, 10.1, 10.2, 10.3, 10.4_

  - [x] 10.2 Update pipeline to handle multi-file extraction from single URL
    - Ensure `_process_single_url` handles multiple `MarkdownFile` objects from one extraction
    - Apply validation, routing, and S3 upload to each file independently
    - _Requirements: 17.5_

  - [ ]* 10.3 Write property test for multi-file pipeline handling
    - **Property 18: Multi-file pipeline handling** — N files from one URL result in N separate DB records and S3 uploads
    - **Validates: Requirements 17.5**

  - [ ]* 10.4 Write property test for persisted effective depth
    - **Property 20: Persisted effective depth** — DB record `max_depth` equals `min(requested, settings.max_crawl_depth)`
    - **Validates: Requirements 7.5**

- [x] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Python with Hypothesis is used for all property-based tests
