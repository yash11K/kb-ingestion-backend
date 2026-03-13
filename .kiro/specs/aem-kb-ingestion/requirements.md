# Requirements Document

## Introduction

The AEM Knowledge Base Ingestion System fetches content from Adobe Experience Manager (AEM) model.json endpoints, extracts meaningful content nodes, transforms them into standalone Markdown files with YAML frontmatter metadata, validates them using an AI agent, and routes them based on validation scores. Files are tracked through their full lifecycle in a NeonDB (PostgreSQL) database, with approved files uploaded to Amazon S3. The system uses two AI agents built on AWS Strands Agents SDK with Amazon Bedrock: an Extractor Agent for content processing and a Validator Agent for quality scoring.

## Glossary

- **AEM_Endpoint**: A URL pointing to an Adobe Experience Manager model.json resource that returns deeply nested JSON content
- **Model_JSON**: The JSON response from an AEM_Endpoint containing recursive `:items` nodes with `:type` fields indicating component types
- **Content_Node**: A single meaningful content element within Model_JSON identified by its `:type` field matching the Allowlist
- **Allowlist**: The configurable set of AEM component types eligible for extraction (e.g., `*/accordionitem`, `*/text`, `*/richtext`, `*/tabitem`, `*/termsandconditions`, `*/policytext`, `*/contentfragment`, `*/teaser`, `*/hero`, `*/accordion`, `*/tabs`)
- **Denylist**: The configurable set of AEM component types to skip during extraction (e.g., `*/responsivegrid`, `*/container`, `*/page`, `*/header`, `*/footer`, `*/navigation`, `*/breadcrumb`, `*/image`, `*/button`, `*/separator`, `*/spacer`, `*/experiencefragment`, `*/languagenavigation`, `*/search`)
- **Markdown_File**: A standalone `.md` file containing YAML frontmatter metadata and a Markdown body representing one atomic content unit
- **YAML_Frontmatter**: The metadata block at the top of a Markdown_File containing fields: title, content_type, source_url, component_type, aem_node_id, modify_date, extracted_at, parent_context, region, brand
- **Content_Hash**: A SHA-256 hash computed from the Markdown body only (excluding YAML_Frontmatter), used for duplicate detection
- **Extractor_Agent**: The AI agent (Strands + Bedrock) that processes Model_JSON and produces Markdown_Files with tools: fetch_aem_json, filter_by_component_type, html_to_markdown, generate_md_file
- **Validator_Agent**: The AI agent (Strands + Bedrock) that scores a Markdown_File from 0.0 to 1.0 based on metadata completeness (0.0–0.3), semantic quality (0.0–0.5), and uniqueness (0.0–0.2) with tools: check_duplicate, parse_frontmatter
- **Validation_Score**: A float from 0.0 to 1.0 assigned by the Validator_Agent
- **Validation_Breakdown**: The per-category score breakdown: metadata_completeness, semantic_quality, uniqueness
- **Ingestion_Job**: A tracked unit of work representing one ingestion request from a single AEM_Endpoint URL
- **KB_Files_Table**: The PostgreSQL table tracking all Markdown_Files through their lifecycle
- **Ingestion_Jobs_Table**: The PostgreSQL table tracking each Ingestion_Job
- **Pipeline_Service**: The orchestration service that coordinates the Extractor_Agent, Validator_Agent, scoring/routing, and S3 upload
- **S3_Bucket**: The Amazon S3 bucket where approved Markdown_Files are uploaded
- **Review_Queue**: The set of Markdown_Files with status `pending_review` awaiting human evaluation
- **Region**: A metadata field identifying the geographic region associated with the content (e.g., US, EU, APAC)
- **Brand**: A metadata field identifying the brand associated with the content

## Requirements

### Requirement 1: Fetch AEM Model JSON

**User Story:** As a knowledge base operator, I want the system to fetch content from an AEM model.json URL, so that I can ingest AEM-managed content into the knowledge base.

#### Acceptance Criteria

1. WHEN a valid AEM_Endpoint URL is provided, THE Extractor_Agent SHALL fetch the Model_JSON using an async HTTP GET request via httpx and return the parsed JSON object.
2. IF the AEM_Endpoint returns a non-200 HTTP status code, THEN THE Extractor_Agent SHALL record the error message in the Ingestion_Job and set the Ingestion_Job status to `failed`.
3. IF the AEM_Endpoint does not respond within 30 seconds, THEN THE Extractor_Agent SHALL abort the request and record a timeout error in the Ingestion_Job.
4. IF the response body is not valid JSON, THEN THE Extractor_Agent SHALL record a parse error in the Ingestion_Job and set the Ingestion_Job status to `failed`.

### Requirement 2: Filter Content Nodes by Component Type

**User Story:** As a knowledge base operator, I want the system to extract only meaningful content nodes from the AEM JSON, so that structural and presentational components are excluded.

#### Acceptance Criteria

1. WHEN Model_JSON is received, THE Extractor_Agent SHALL recursively traverse all `:items` objects to discover every node with a `:type` field.
2. THE Extractor_Agent SHALL check each discovered node against the Denylist first; any node whose `:type` matches a Denylist entry SHALL be skipped.
3. THE Extractor_Agent SHALL extract only nodes whose `:type` matches an entry in the Allowlist.
4. THE Extractor_Agent SHALL record the total number of discovered Content_Nodes in the Ingestion_Job `total_nodes_found` field.
5. WHILE traversing nested `:items`, THE Extractor_Agent SHALL preserve the parent node path as `parent_context` metadata for each extracted Content_Node.

### Requirement 3: Transform Content Nodes to Markdown Files

**User Story:** As a knowledge base operator, I want each extracted content node converted into a standalone Markdown file with metadata, so that the knowledge base contains well-structured, self-contained documents.

#### Acceptance Criteria

1. WHEN a Content_Node is extracted, THE Extractor_Agent SHALL produce exactly one Markdown_File per atomic content unit (one FAQ question-answer pair, one policy section, one text block).
2. THE Extractor_Agent SHALL convert all HTML content within a Content_Node to clean Markdown syntax using the markdownify library.
3. THE Extractor_Agent SHALL generate YAML_Frontmatter containing all required fields: title, content_type, source_url, component_type, aem_node_id, modify_date, extracted_at, parent_context, region, and brand.
4. THE Extractor_Agent SHALL populate the `modify_date` field from the `repo:modifyDate` value in the dataLayer object of the Model_JSON, formatted in ISO 8601 UTC.
5. THE Extractor_Agent SHALL populate the `extracted_at` field with the current UTC timestamp in ISO 8601 format.
6. THE Extractor_Agent SHALL compute a Content_Hash (SHA-256) from the Markdown body only, excluding the YAML_Frontmatter.
7. THE Extractor_Agent SHALL populate the `region` and `brand` fields from the AEM content metadata or from user-provided parameters for the ingestion request.

### Requirement 4: Validate Markdown Files

**User Story:** As a knowledge base operator, I want each generated Markdown file validated by an AI agent, so that only high-quality content enters the knowledge base.

#### Acceptance Criteria

1. WHEN a Markdown_File is produced by the Extractor_Agent, THE Validator_Agent SHALL parse the YAML_Frontmatter and score metadata completeness from 0.0 to 0.3 based on the presence and validity of all required frontmatter fields.
2. THE Validator_Agent SHALL score semantic quality from 0.0 to 0.5 based on content coherence, readability, and completeness of the Markdown body.
3. THE Validator_Agent SHALL score uniqueness from 0.0 to 0.2 by checking the Content_Hash against existing records in the KB_Files_Table.
4. THE Validator_Agent SHALL compute the final Validation_Score as the sum of metadata_completeness, semantic_quality, and uniqueness sub-scores.
5. THE Validator_Agent SHALL return the Validation_Score, the Validation_Breakdown, and a list of specific issues found.

### Requirement 5: Route Files Based on Validation Score

**User Story:** As a knowledge base operator, I want files automatically routed based on their validation score, so that high-quality files are approved immediately and low-quality files are flagged or rejected.

#### Acceptance Criteria

1. WHEN the Validation_Score is greater than or equal to 0.7, THE Pipeline_Service SHALL set the Markdown_File status to `approved` in the KB_Files_Table.
2. WHEN the Validation_Score is less than 0.7 and greater than or equal to 0.2, THE Pipeline_Service SHALL set the Markdown_File status to `pending_review` in the KB_Files_Table.
3. WHEN the Validation_Score is less than 0.2, THE Pipeline_Service SHALL set the Markdown_File status to `auto_rejected` in the KB_Files_Table.
4. THE Pipeline_Service SHALL store the Validation_Score, Validation_Breakdown, and validation issues in the KB_Files_Table for every Markdown_File regardless of routing outcome.

### Requirement 6: Upload Approved Files to S3

**User Story:** As a knowledge base operator, I want approved Markdown files uploaded to S3, so that they are available in the knowledge base storage.

#### Acceptance Criteria

1. WHEN a Markdown_File status is set to `approved`, THE Pipeline_Service SHALL upload the file to the S3_Bucket using boto3.
2. THE Pipeline_Service SHALL use the S3 key structure `knowledge-base/{content_type}/{YYYY-MM}/{filename}` where YYYY-MM is derived from the `extracted_at` date.
3. THE Pipeline_Service SHALL set the S3 object ContentType to `text/markdown` and include `file_id` and `content_hash` as S3 object metadata.
4. WHEN the S3 upload succeeds, THE Pipeline_Service SHALL update the Markdown_File status to `in_s3` and record the `s3_bucket`, `s3_key`, and `s3_uploaded_at` fields in the KB_Files_Table.
5. IF the S3 upload fails, THEN THE Pipeline_Service SHALL retain the `approved` status and log the error for retry.

### Requirement 7: Track Files in Database

**User Story:** As a knowledge base operator, I want all files tracked in a database through their full lifecycle, so that I have complete visibility into the ingestion pipeline.

#### Acceptance Criteria

1. WHEN a Markdown_File is created, THE Pipeline_Service SHALL insert a record into the KB_Files_Table with status `pending_validation` and all available metadata including region and brand.
2. THE KB_Files_Table SHALL store: id, filename, title, content_type, content_hash, source_url, component_type, aem_node_id, md_content, modify_date, parent_context, region, brand, validation_score, validation_breakdown, validation_issues, status, s3_bucket, s3_key, s3_uploaded_at, reviewed_by, reviewed_at, review_notes, created_at, and updated_at.
3. WHEN the status of a Markdown_File changes, THE Pipeline_Service SHALL update the `status` and `updated_at` fields in the KB_Files_Table.
4. THE Pipeline_Service SHALL enforce the status lifecycle: `pending_validation` → `approved` / `pending_review` / `auto_rejected`; `approved` → `in_s3`; `pending_review` → `approved` / `rejected`; `approved` (from review) → `in_s3`.

### Requirement 8: Track Ingestion Jobs

**User Story:** As a knowledge base operator, I want each ingestion request tracked as a job, so that I can monitor progress and diagnose failures.

#### Acceptance Criteria

1. WHEN an ingestion request is received, THE Pipeline_Service SHALL create a record in the Ingestion_Jobs_Table with status `in_progress` and the `started_at` timestamp.
2. THE Ingestion_Jobs_Table SHALL store: id, source_url, status, total_nodes_found, files_created, files_auto_approved, files_pending_review, files_auto_rejected, error_message, started_at, and completed_at.
3. WHEN all Content_Nodes from an Ingestion_Job have been processed, THE Pipeline_Service SHALL update the Ingestion_Job status to `completed` and record the `completed_at` timestamp along with final counts for files_created, files_auto_approved, files_pending_review, and files_auto_rejected.
4. IF an unrecoverable error occurs during ingestion, THEN THE Pipeline_Service SHALL set the Ingestion_Job status to `failed` and record the error_message.

### Requirement 9: Idempotent Ingestion via Duplicate Detection

**User Story:** As a knowledge base operator, I want re-ingesting the same AEM URL to skip already-ingested content, so that duplicates are not created.

#### Acceptance Criteria

1. WHEN a new Markdown_File is created, THE Pipeline_Service SHALL compute the Content_Hash and query the KB_Files_Table for any existing record with the same Content_Hash.
2. IF a record with the same Content_Hash already exists in the KB_Files_Table, THEN THE Pipeline_Service SHALL skip creating a new record and increment a `duplicates_skipped` counter on the Ingestion_Job.
3. THE Validator_Agent SHALL use the check_duplicate tool to verify Content_Hash uniqueness as part of the uniqueness scoring.

### Requirement 10: Ingestion API Endpoint

**User Story:** As a knowledge base operator, I want to trigger ingestion via an API call, so that I can integrate the system with other tools and workflows.

#### Acceptance Criteria

1. WHEN a POST request is received at `/api/v1/ingest` with a JSON body containing `url`, `region`, and `brand` fields, THE API SHALL validate the input and start an Ingestion_Job.
2. THE API SHALL return a 202 Accepted response with the Ingestion_Job `id` and status.
3. IF the `url` field is missing or not a valid URL, THEN THE API SHALL return a 422 Unprocessable Entity response with a descriptive error message.
4. IF the `region` or `brand` fields are missing, THEN THE API SHALL return a 422 Unprocessable Entity response indicating the missing required fields.

### Requirement 11: Ingestion Job Status API

**User Story:** As a knowledge base operator, I want to check the status of an ingestion job, so that I can monitor its progress.

#### Acceptance Criteria

1. WHEN a GET request is received at `/api/v1/ingest/{job_id}`, THE API SHALL return the full Ingestion_Job record including all counters and timestamps.
2. IF the `job_id` does not exist in the Ingestion_Jobs_Table, THEN THE API SHALL return a 404 Not Found response.

### Requirement 12: Human Review Queue API

**User Story:** As a content reviewer, I want to browse, review, accept, reject, and update files pending review, so that I can ensure content quality before it enters the knowledge base.

#### Acceptance Criteria

1. WHEN a GET request is received at `/api/v1/queue`, THE API SHALL return a paginated list of Markdown_Files with status `pending_review`, supporting filtering by region, brand, content_type, and component_type.
2. WHEN a GET request is received at `/api/v1/queue/{file_id}`, THE API SHALL return the full KB_Files_Table record including md_content, validation_score, validation_breakdown, and validation_issues.
3. IF the `file_id` does not exist or does not have status `pending_review`, THEN THE API SHALL return a 404 Not Found response for queue endpoints.
4. WHEN a POST request is received at `/api/v1/queue/{file_id}/accept` with `reviewed_by` in the body, THE API SHALL set the file status to `approved`, record `reviewed_by` and `reviewed_at`, and trigger S3 upload.
5. WHEN a POST request is received at `/api/v1/queue/{file_id}/reject` with `reviewed_by` and `review_notes` in the body, THE API SHALL set the file status to `rejected` and record `reviewed_by`, `reviewed_at`, and `review_notes`.
6. WHEN a PUT request is received at `/api/v1/queue/{file_id}/update` with updated `md_content` in the body, THE API SHALL update the md_content, recompute the Content_Hash, and set `updated_at` without changing the file status.

### Requirement 13: Files Listing API

**User Story:** As a knowledge base operator, I want to browse all tracked files, so that I can audit the knowledge base contents.

#### Acceptance Criteria

1. WHEN a GET request is received at `/api/v1/files`, THE API SHALL return a paginated list of all Markdown_Files in the KB_Files_Table, supporting filtering by status, region, brand, content_type, and component_type.
2. WHEN a GET request is received at `/api/v1/files/{file_id}`, THE API SHALL return the full KB_Files_Table record for the specified file.
3. IF the `file_id` does not exist, THEN THE API SHALL return a 404 Not Found response.

### Requirement 14: Database Schema Migration

**User Story:** As a developer, I want the database schema managed through migration scripts, so that schema changes are versioned and reproducible.

#### Acceptance Criteria

1. THE System SHALL provide SQL migration scripts that create the `kb_files` and `ingestion_jobs` tables with all specified columns and appropriate data types.
2. THE `kb_files` table SHALL include columns for region and brand as non-nullable text fields.
3. THE System SHALL create indexes on `kb_files.content_hash`, `kb_files.status`, `kb_files.region`, and `kb_files.brand` for query performance.
4. THE System SHALL create an index on `ingestion_jobs.status` for query performance.

### Requirement 15: Markdown Parsing and Printing Round-Trip

**User Story:** As a developer, I want to ensure that parsing a Markdown file with frontmatter and re-serializing it produces an equivalent result, so that no data is lost during processing.

#### Acceptance Criteria

1. WHEN a valid Markdown_File with YAML_Frontmatter is parsed using the python-frontmatter library, THE System SHALL extract the frontmatter metadata and Markdown body as separate objects.
2. WHEN the parsed frontmatter metadata and Markdown body are re-serialized into a Markdown_File, THE System SHALL produce output that, when parsed again, yields equivalent frontmatter metadata and an equivalent Markdown body (round-trip property).
3. FOR ALL valid Markdown_Files produced by the Extractor_Agent, parsing then serializing then parsing SHALL produce equivalent frontmatter metadata and Markdown body.
