# Requirements Document

## Introduction

The AEM KB Ingestion System discovers deep links (internal page links and document links) during content extraction. Currently, all deep links are stored as references in the database for later manual review or crawl-based ingestion. However, when a deep link points to a PDF file, the system should handle it differently: instead of running the PDF through the LLM extraction → validation → markdown pipeline, the raw PDF binary should be downloaded and stored as-is in S3. Metadata (brand, region, namespace, source page, anchor text) is inferred from the link and its parent source and persisted in the `kb_files` table with a `file_type` discriminator so that PDFs appear alongside markdown files in the "all files" listing.

## Glossary

- **DeepLink**: A URL discovered during AEM content extraction that points to another AEM page or an external document (e.g. PDF). Stored in the `deep_links` table.
- **PDF DeepLink**: A deep link whose URL path ends with the `.pdf` extension (after stripping query parameters and fragments).
- **Pipeline**: The `PipelineService` class in `src/services/pipeline.py` that orchestrates the full ingestion workflow.
- **S3UploadService**: The service in `src/services/s3_upload.py` that uploads files to S3.
- **kb_files**: The database table that stores records for all ingested knowledge base files (markdown and PDF).
- **file_type**: A discriminator column on `kb_files` that distinguishes between `markdown` and `pdf` records.

## Requirements

### Requirement 1: PDF Extension Detection

**User Story:** As a system operator, I want the pipeline to automatically detect when a deep link URL points to a PDF file, so that PDFs are routed to the correct storage path without manual intervention.

#### Acceptance Criteria

1. THE system SHALL provide an `is_pdf_link(url: str) -> bool` utility function in `src/utils/url_inference.py`.
2. THE function SHALL return `True` when the URL path (after stripping query parameters, fragments, and trailing slashes) ends with `.pdf` (case-insensitive).
3. THE function SHALL return `False` for all other URL extensions and for URLs with no extension.

### Requirement 2: PDF Download and Raw S3 Upload

**User Story:** As a system operator, I want PDF deep links to be downloaded and stored as raw binary files in S3, so that the original document is preserved without LLM processing.

#### Acceptance Criteria

1. WHEN a confirmed deep link is identified as a PDF, THE Pipeline SHALL download the PDF binary via an HTTP GET request to the deep link URL.
2. THE Pipeline SHALL NOT pass PDF deep links through the extractor, validator, or markdown generation pipeline.
3. THE `S3UploadService` SHALL expose a second upload method `upload_pdf(pdf_bytes, filename, brand, region, namespace, file_id)` that uploads raw bytes with `ContentType="application/pdf"`.
4. THE S3 key for PDFs SHALL follow the pattern `{brand}/{region}/{namespace}/{hash}_{original_filename}.pdf`, where `{hash}` is a short prefix (first 8 characters of the SHA-256 hex digest of the PDF bytes) to avoid filename collisions.
5. IF the PDF download fails (HTTP error, timeout, connection error), THEN THE Pipeline SHALL log the error, emit an SSE event, and continue processing remaining deep links without aborting.

### Requirement 3: Database Record for PDF Files

**User Story:** As a knowledge base consumer, I want PDF files to appear in the "all files" listing alongside markdown files, so that I have a unified view of all ingested content.

#### Acceptance Criteria

1. THE `kb_files` table SHALL include a `file_type` column of type `TEXT NOT NULL DEFAULT 'markdown'`.
2. WHEN a PDF is uploaded to S3, THE Pipeline SHALL insert a `kb_files` record with `file_type='pdf'`.
3. THE PDF `kb_files` record SHALL store: `filename` (the hash-prefixed PDF filename), `source_url` (the deep link URL), `brand`, `region`, `namespace`, `content_hash` (SHA-256 of the PDF bytes), `s3_bucket`, `s3_key`, `s3_uploaded_at`, and `status='approved'`.
4. THE PDF `kb_files` record SHALL have `md_content`, `md_body`, `title`, `content_type`, `component_type`, and `key` set to NULL or empty string (whichever the schema allows).
5. THE PDF `kb_files` record SHALL have `validation_score` set to NULL and `validation_breakdown` set to NULL.
6. EXISTING `kb_files` rows SHALL default to `file_type='markdown'` via the migration.

### Requirement 4: Pipeline Branching for PDF Deep Links

**User Story:** As a developer, I want a clear branching point in the pipeline where PDF deep links are routed to the PDF-specific path, so that the code is maintainable and the existing markdown flow is unaffected.

#### Acceptance Criteria

1. WHEN the Pipeline processes confirmed deep links for ingestion, THE Pipeline SHALL check each deep link URL using `is_pdf_link()` before entering the extraction flow.
2. IF `is_pdf_link()` returns `True`, THEN THE Pipeline SHALL route the deep link to the PDF download → S3 upload → DB insert path.
3. IF `is_pdf_link()` returns `False`, THEN THE Pipeline SHALL route the deep link to the existing AEM JSON fetch → extract → validate → upload path.
4. THE branching logic SHALL be implemented within the BFS crawl loop or the deep link processing section of `_process_single_url`.

### Requirement 5: SSE Events for PDF Processing

**User Story:** As a frontend client, I want to receive SSE events when a PDF is being downloaded and uploaded, so that the UI can show progress for PDF deep links.

#### Acceptance Criteria

1. WHEN the Pipeline begins downloading a PDF, THE Stream_Manager SHALL emit a `progress` event with `stage: "pdf_download"` containing the PDF URL.
2. WHEN the Pipeline completes uploading a PDF to S3, THE Stream_Manager SHALL emit a `progress` event with `stage: "pdf_upload_complete"` containing the filename and S3 key.
3. WHEN a PDF download fails, THE Stream_Manager SHALL emit a `progress` event with `stage: "pdf_download_error"` containing the URL and error message.

### Requirement 6: File Listing Compatibility

**User Story:** As an API consumer, I want the file listing endpoints to return both markdown and PDF files with a `file_type` field, so that the frontend can render them appropriately.

#### Acceptance Criteria

1. THE `FileSummary` and `FileDetail` response models SHALL include a `file_type: str` field defaulting to `"markdown"`.
2. THE file listing queries SHALL return both `markdown` and `pdf` records from `kb_files`.
3. THE `FileDetail` response SHALL handle NULL values for `md_content`, `validation_score`, and `validation_breakdown` gracefully when `file_type` is `"pdf"`.

### Requirement 7: Backward Compatibility

**User Story:** As an existing API consumer, I want the system to behave identically for non-PDF deep links, so that my existing integrations are not affected.

#### Acceptance Criteria

1. WHEN a deep link URL does not end in `.pdf`, THE Pipeline SHALL process it exactly as it does today.
2. THE existing `kb_files` records SHALL receive `file_type='markdown'` via the migration default, with no other changes.
3. ALL existing API responses SHALL remain unchanged except for the addition of the `file_type` field.
