# Requirements Document

## Introduction

The AEM KB Ingestion System currently discovers child URLs from AEM `model.json` content nodes (via `extract_child_urls`) but stops after the initial page — it stores discovered URLs in the database and emits an SSE event, leaving the user to manually submit each child URL as a separate ingestion job. This feature adds an opt-in recursive crawl loop inside the existing pipeline so that discovered child URLs are automatically fetched, extracted, and processed within the same job, up to a configurable depth limit. The crawl is breadth-first, cycle-safe, and fully observable via SSE events.

## Glossary

- **Pipeline**: The `PipelineService` class in `src/services/pipeline.py` that orchestrates the full ingestion workflow (fetch → extract → validate → route → upload).
- **Extractor_Agent**: The `ExtractorAgent` class in `src/agents/extractor.py` that fetches AEM JSON, pre-filters content nodes, and invokes the Strands agent to produce `MarkdownFile` objects.
- **Ingest_API**: The `POST /ingest` endpoint in `src/api/ingest.py` that accepts ingestion requests and launches the Pipeline as a background task.
- **IngestRequest**: The Pydantic request model in `src/models/schemas.py` that defines the shape of the `POST /ingest` request body.
- **Stream_Manager**: The `StreamManager` class in `src/services/stream_manager.py` that broadcasts SSE events to connected clients for a given job.
- **Crawl_Depth**: An integer representing how many levels of child URL discovery to follow. Depth 0 means process only the submitted URL (current behavior). Depth 1 means also process child URLs discovered from the submitted URL. Depth N means follow child URLs N levels deep.
- **Visited_Set**: An in-memory set of normalized URLs used during a single crawl job to prevent processing the same URL more than once (cycle detection).
- **BFS_Queue**: A breadth-first queue of `(url, current_depth)` tuples used to process discovered child URLs level by level within a single pipeline run.
- **Child_URL**: An internal AEM page URL discovered from link fields (e.g. `ctaLink`, `linkUrl`) in filtered content nodes, expanded to a full `*.model.json` URL by `extract_child_urls`.
- **Ingestion_Job**: A database record in the `ingestion_jobs` table that tracks the progress and outcome of a single ingestion request.
- **Crawl_Preview**: The result of a lightweight URL discovery scan that returns the tree of child URLs found in an AEM page, presented to the user for review and selective confirmation before any ingestion job begins.
- **Confirmed_URLs**: The subset of discovered child URLs that the user has explicitly approved for crawling. Only these URLs are enqueued in the BFS_Queue when the ingestion job starts.
- **Component_Denylist**: A system-configured list of AEM component type suffixes that are automatically skipped during extraction because they contain no KB-worthy content (e.g. React widgets, login modals, image-only components, i18n keys).
- **Component_Allowlist**: A system-configured list of AEM component type suffixes that are known to contain KB-worthy content (e.g. text, richtext, accordion, FAQ, contentcardelement).
- **Namespace**: A content category derived from the AEM URL path that maps to an S3 subdirectory (e.g. `products-and-services`, `faq`, `customer-service`). Used for organizing KB documents by topic area.
- **Component_Key**: The key name of an AEM component within the `:items` object (e.g. `contentcardelement`, `contentcardelement_821372053`), used as a stable identifier for a content block in the KB metadata.

## Requirements

### Requirement 1: Opt-In Depth Parameter on Ingest Request

**User Story:** As an API consumer, I want to specify a crawl depth when submitting an ingestion request, so that I can control whether child URLs are automatically followed or not.

#### Acceptance Criteria

1. THE IngestRequest SHALL accept an optional integer field `max_depth` with a default value of 0.
2. WHEN `max_depth` is 0, THE Pipeline SHALL process only the submitted URL and emit `child_urls_discovered` events without following child URLs (preserving current behavior).
3. WHEN `max_depth` is greater than 0, THE Pipeline SHALL automatically fetch, extract, and process discovered child URLs up to the specified depth.
4. IF `max_depth` is less than 0, THEN THE Ingest_API SHALL reject the request with a 422 validation error.
5. IF `max_depth` exceeds the system-configured maximum (default 3), THEN THE Ingest_API SHALL clamp the value to the system maximum and proceed.

### Requirement 2: System-Level Depth Cap Configuration

**User Story:** As a system operator, I want to configure a global maximum crawl depth, so that I can prevent runaway crawls from consuming excessive resources.

#### Acceptance Criteria

1. THE Settings SHALL include a `max_crawl_depth` integer field with a default value of 3.
2. WHEN the Pipeline receives a `max_depth` value exceeding `max_crawl_depth`, THE Pipeline SHALL use `max_crawl_depth` as the effective depth limit.
3. THE Settings SHALL load `max_crawl_depth` from the `MAX_CRAWL_DEPTH` environment variable.

### Requirement 3: Breadth-First Crawl Loop

**User Story:** As an API consumer, I want child URLs to be crawled breadth-first within a single job, so that all pages at a given depth are processed before going deeper.

#### Acceptance Criteria

1. WHEN `max_depth` is greater than 0, THE Pipeline SHALL maintain a BFS_Queue initialized with the submitted URL at depth 0.
2. WHILE the BFS_Queue is not empty, THE Pipeline SHALL dequeue the next URL, extract content from the URL, and enqueue any newly discovered child URLs at `current_depth + 1`.
3. WHEN a discovered child URL has a depth that would exceed `max_depth`, THE Pipeline SHALL skip enqueueing that URL.
4. THE Pipeline SHALL process all URLs at depth N before processing any URL at depth N+1.
5. WHEN `confirmed_urls` is provided in the IngestRequest, THE Pipeline SHALL only enqueue discovered child URLs that are present in the `confirmed_urls` list.

### Requirement 4: Cycle Detection via Visited Set

**User Story:** As a system operator, I want the crawl loop to detect and skip already-visited URLs, so that circular navigation structures in AEM do not cause infinite loops or duplicate processing.

#### Acceptance Criteria

1. THE Pipeline SHALL maintain a Visited_Set of normalized URLs for the duration of a single crawl job.
2. WHEN a URL is dequeued from the BFS_Queue, THE Pipeline SHALL check the URL against the Visited_Set before processing.
3. IF a URL is already present in the Visited_Set, THEN THE Pipeline SHALL skip processing that URL and log a debug message.
4. WHEN a URL is processed, THE Pipeline SHALL add the URL to the Visited_Set immediately before extraction begins.
5. THE Pipeline SHALL normalize URLs before comparison by removing trailing slashes and query parameters.

### Requirement 5: Single-URL Processing Refactor

**User Story:** As a developer, I want the pipeline's per-URL processing logic extracted into a reusable helper, so that both the initial URL and crawled child URLs use the same extraction-validation-routing flow.

#### Acceptance Criteria

1. THE Pipeline SHALL expose a `_process_single_url` method that performs fetch, extract, validate, route, and upload for one URL.
2. WHEN `_process_single_url` is called, THE Pipeline SHALL return the list of child URLs discovered during extraction of that URL.
3. WHEN `_process_single_url` is called, THE Pipeline SHALL accumulate file counters (created, approved, pending_review, rejected, duplicates) into the job-level totals.
4. IF `_process_single_url` encounters a fetch or extraction error for a single URL, THEN THE Pipeline SHALL log the error, emit an SSE error event for that URL, and continue processing the remaining BFS_Queue entries.

### Requirement 6: Crawl Progress SSE Events

**User Story:** As a frontend client, I want to receive real-time SSE events about crawl progress, so that I can display which page is being crawled and overall crawl status.

#### Acceptance Criteria

1. WHEN the Pipeline begins processing a URL from the BFS_Queue, THE Stream_Manager SHALL emit a `crawl_page_start` event containing the URL, current depth, and page index within the crawl.
2. WHEN the Pipeline finishes processing a URL from the BFS_Queue, THE Stream_Manager SHALL emit a `crawl_page_complete` event containing the URL, current depth, number of files extracted, and number of new child URLs discovered.
3. WHEN the entire crawl loop completes, THE Stream_Manager SHALL emit a `crawl_summary` event containing total pages crawled, total files created across all pages, maximum depth reached, and count of URLs skipped due to cycle detection.
4. WHEN a URL is skipped due to cycle detection, THE Stream_Manager SHALL emit a `crawl_page_skipped` event containing the URL and the reason.

### Requirement 7: Database Schema for Crawl Tracking

**User Story:** As a system operator, I want crawl metadata persisted in the ingestion job record, so that I can audit crawl behavior and depth usage after the fact.

#### Acceptance Criteria

1. THE Ingestion_Job table SHALL include a `max_depth` integer column with a default value of 0.
2. THE Ingestion_Job table SHALL include a `pages_crawled` integer column with a default value of 0.
3. THE Ingestion_Job table SHALL include a `current_depth` integer column with a default value of 0.
4. WHEN the Pipeline processes a URL from the BFS_Queue, THE Pipeline SHALL update the `pages_crawled` counter and `current_depth` value in the Ingestion_Job record.
5. WHEN a new ingestion job is created, THE Pipeline SHALL persist the effective `max_depth` value in the Ingestion_Job record.

### Requirement 8: Job Response Model Update

**User Story:** As an API consumer, I want the job status response to include crawl metadata, so that I can see how deep the crawl went and how many pages were processed.

#### Acceptance Criteria

1. THE IngestionJobResponse SHALL include `max_depth`, `pages_crawled`, and `current_depth` fields.
2. WHEN `max_depth` is 0, THE IngestionJobResponse SHALL return `pages_crawled` as 1 and `current_depth` as 0.

### Requirement 9: Parent Context Enrichment for Child URLs

**User Story:** As a knowledge base consumer, I want files extracted from child URLs to carry context about the parent page that linked to them, so that the KB documents have richer navigational context.

#### Acceptance Criteria

1. WHEN the Pipeline processes a child URL discovered during crawl, THE Pipeline SHALL pass the parent page's URL as additional context to the Extractor_Agent.
2. THE MarkdownFile objects produced from child URLs SHALL include the parent page URL in the `parent_context` field of the YAML frontmatter.

### Requirement 10: Backward Compatibility

**User Story:** As an existing API consumer, I want the system to behave identically to today when I do not specify a crawl depth, so that my existing integrations are not affected.

#### Acceptance Criteria

1. WHEN `max_depth` is omitted from the IngestRequest, THE Pipeline SHALL default to depth 0 and process only the submitted URL.
2. WHEN `max_depth` is 0, THE Pipeline SHALL emit `child_urls_discovered` events for discovered child URLs without following them.
3. THE IngestResponse model SHALL remain unchanged (returning `source_id`, `job_id`, and `status`).
4. WHEN `max_depth` is 0, THE Ingestion_Job record SHALL contain `pages_crawled` equal to 1 and `current_depth` equal to 0.

### Requirement 11: Navigation Preview and Confirmation Flow

**User Story:** As a frontend user, I want to preview all discoverable child URLs of an AEM page before starting an ingestion job, so that I can review the link tree, select which URLs to crawl, and confirm before any processing begins.

#### Acceptance Criteria

1. THE Ingest_API SHALL expose a `GET /preview/nav` endpoint that accepts a `url` query parameter and an optional `max_depth` query parameter (default 1).
2. WHEN a valid AEM URL is provided, THE Ingest_API SHALL recursively discover child URLs up to the requested `max_depth`, applying cycle detection, and return a tree structure of discovered URLs grouped by depth level.
3. THE response SHALL include the total count of discovered child URLs, the list of URL paths with their depth level and parent URL, and a summary of the discovery (pages found per depth level).
4. IF the AEM URL is unreachable or returns invalid JSON, THEN THE Ingest_API SHALL return a 502 error with a descriptive message.
5. THE preview endpoint SHALL complete within the configured `aem_request_timeout` without invoking the Extractor_Agent or Validator_Agent.
6. THE preview endpoint SHALL apply the same URL normalization and cycle detection logic as the crawl loop to ensure the preview accurately reflects what the crawl would process.

### Requirement 13: Selective URL Confirmation for Crawl Jobs

**User Story:** As a frontend user, I want to select or deselect specific child URLs from the preview before starting the ingestion job, so that I only crawl pages that are relevant to my KB.

#### Acceptance Criteria

1. THE IngestRequest SHALL accept an optional `confirmed_urls` field containing a list of URL strings that the user has approved for crawling.
2. WHEN `confirmed_urls` is provided and non-empty, THE Pipeline SHALL only enqueue URLs present in the `confirmed_urls` list into the BFS_Queue, ignoring any discovered child URLs not in the list.
3. WHEN `confirmed_urls` is provided, THE Pipeline SHALL still apply cycle detection and depth limits to the confirmed URLs.
4. WHEN `confirmed_urls` is null or empty and `max_depth` is greater than 0, THE Pipeline SHALL fall back to the default behavior of crawling all discovered child URLs up to `max_depth`.
5. THE `confirmed_urls` field SHALL accept both relative paths (e.g. `/en/products`) and full AEM model.json URLs; the Pipeline SHALL normalize them before matching.

### Requirement 14: Intelligent AEM Component Filtering

**User Story:** As a system operator, I want the pipeline to automatically skip AEM components that are structural, React-related, or contain only translations/keys, so that only KB-worthy content components are processed and LLM input tokens are not wasted on noise.

#### Acceptance Criteria

1. THE Pipeline SHALL maintain a system-level denylist of AEM component type suffixes that are never KB-worthy, including but not limited to: `loginModal`, `bookingwidget`, `image`, `ghost`, `divider`, `breadcrumb`, `languagenavigation`, `experiencefragment`, `embed`, `separator`, `search`, `form`, `button`, `carousel` (image-only), and any component whose `:type` suffix contains `react` or `widget`.
2. THE Pipeline SHALL maintain a system-level allowlist of AEM component type suffixes that are KB-worthy, including but not limited to: `text`, `richtext`, `accordion`, `faq`, `table`, `title`, `teaser`, `contentcardelement`, `contentfragmentlist`, `tabs`, `container` (when containing text children).
3. WHEN filtering components, THE Pipeline SHALL skip any node whose only meaningful fields are i18n keys, translation strings, or configuration objects (identified by fields like `i18n`, `dataLayer`, `appliedCssClassNames`, or nodes containing only `id` and `:type` with no text content fields).
4. THE component denylist and allowlist SHALL be configurable via environment variables (`AEM_COMPONENT_DENYLIST` and `AEM_COMPONENT_ALLOWLIST`) as comma-separated type suffixes.
5. THE Pipeline SHALL NOT require the user to specify component types, brands, or regions as input — these SHALL be removed from the IngestRequest model.

### Requirement 15: Brand, Region, and Namespace Inference from URL

**User Story:** As an API consumer, I want the system to automatically infer brand, region, and namespace from the AEM URL, so that I only need to provide the URL and the system handles classification.

#### Acceptance Criteria

1. THE Pipeline SHALL extract the brand from the AEM URL domain (e.g. `www.avis.com` → `avis`, `www.budget.com` → `budget`).
2. THE Pipeline SHALL extract the region code from the AEM URL path segment (e.g. `/en/` → `nam`, `/en-gb/` → `emea`, `/en-au/` → `apac`) using a configurable locale-to-region mapping.
3. THE Pipeline SHALL extract the namespace from the AEM URL path by matching path segments against the configured namespace list (e.g. `/en/products-and-services/products` → `products-and-services`).
4. THE Settings SHALL include a `namespace_list` configuration containing the recognized namespace values: `locations`, `products-and-services`, `protections-and-coverages`, `rental-addons`, `long-term-car-rental`, `one-way-car-rentals`, `miles-points-and-partners`, `meetings-and-groups`, `car-sales`, `faq`, `customer-service`, `travel-guides`.
5. IF the URL path does not match any configured namespace, THEN THE Pipeline SHALL assign the namespace `general`.
6. THE IngestRequest SHALL no longer require `brand`, `region`, or `component_types` fields — these SHALL be inferred automatically from the URL.

### Requirement 16: Revised S3 Storage Path and Metadata Schema

**User Story:** As a knowledge base consumer, I want KB documents stored in a structured S3 path organized by brand, region, and namespace, with clean metadata that uses component keys as identifiers instead of AEM references.

#### Acceptance Criteria

1. THE Pipeline SHALL store generated markdown files in S3 using the path pattern: `s3://{bucket}/{brand}/{region}/{namespace}/{document_filename}.md`.
2. THE MarkdownFile YAML frontmatter SHALL include the following metadata fields: `key` (the AEM component key name, serving as a component-level identifier), `namespace` (inferred from URL), `brand` (inferred from URL), `region` (inferred from URL), `source_url` (the page URL this content was extracted from), `parent_context` (parent page URL if extracted from a child URL crawl), and `title` (extracted from the content).
3. THE MarkdownFile YAML frontmatter SHALL NOT include AEM-specific references such as `:type` paths, `dataLayer` objects, `repo:modifyDate`, or AEM component IDs.
4. THE `key` field SHALL be derived from the AEM component's key name in the `:items` object (e.g. `contentcardelement`, `contentcardelement_821372053`), serving as a stable identifier for that content block.

### Requirement 17: Multi-File Splitting from Single Page Extraction

**User Story:** As a knowledge base consumer, I want the extractor agent to intelligently split a single AEM page into multiple separate markdown files when the page contains distinct content topics, so that each KB document covers one coherent subject.

#### Acceptance Criteria

1. WHEN the Extractor_Agent processes a page containing multiple distinct content components (e.g. multiple `contentcardelement` nodes with different topics), THE Extractor_Agent SHALL produce separate MarkdownFile objects for each distinct content topic.
2. EACH split MarkdownFile SHALL carry its own metadata including the component `key`, `namespace`, `brand`, `region`, `source_url`, and `title` derived from the specific content block.
3. THE Extractor_Agent SHALL determine split boundaries based on content semantics — components that describe the same topic (e.g. a title + body text + CTA for "Mobile Wi-Fi") SHALL be grouped into one file, while unrelated components (e.g. "Mobile Wi-Fi" vs "SiriusXM Radio") SHALL be split into separate files.
4. WHEN a page contains only a single coherent topic, THE Extractor_Agent SHALL produce a single MarkdownFile (no unnecessary splitting).
5. THE Pipeline SHALL handle multiple MarkdownFile objects returned from a single URL extraction, applying validation, routing, and S3 upload to each file independently.

### Requirement 12: Crawl Error Resilience

**User Story:** As a system operator, I want the crawl to continue processing remaining URLs when a single child URL fails, so that one broken page does not abort the entire crawl job.

#### Acceptance Criteria

1. IF a child URL fetch fails (timeout, HTTP error, invalid JSON), THEN THE Pipeline SHALL log the error, emit a `crawl_page_error` SSE event for that URL, and continue with the next URL in the BFS_Queue.
2. IF a child URL extraction fails (agent error), THEN THE Pipeline SHALL log the error, emit a `crawl_page_error` SSE event, and continue with the next URL in the BFS_Queue.
3. WHEN the crawl completes, THE `crawl_summary` event SHALL include the count of failed URLs.
4. THE Ingestion_Job SHALL be marked as `completed` (not `failed`) when at least the root URL was processed, even if some child URLs failed.
