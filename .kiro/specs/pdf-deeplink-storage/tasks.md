# Implementation Plan: PDF Deep Link Storage

## Overview

Add a PDF-specific branch to the ingestion pipeline. When a confirmed deep link has a `.pdf` extension, bypass the LLM pipeline entirely — download the raw PDF, upload to S3, and insert a `kb_files` record with `file_type='pdf'`. Metadata is inferred from the parent source. PDFs appear in the unified file listing.

## Tasks

- [x] 1. Add `is_pdf_link` utility function
  - [x] 1.1 Add `is_pdf_link(url: str) -> bool` to `src/utils/url_inference.py`
    - Use `urlparse` to extract path, strip trailing slashes, check `.lower().endswith(".pdf")`
    - Handle query params, fragments, edge cases (empty string, no extension)
    - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Database migration and model updates
  - [x] 2.1 Create Alembic migration to add `file_type` column to `kb_files`
    - Add `file_type TEXT NOT NULL DEFAULT 'markdown'` column
    - Make `md_content`, `title`, `content_type`, `component_type` nullable (needed for PDF records that have no markdown content)
    - Add index on `file_type` for filtered queries
    - _Requirements: 3.1, 3.4, 3.6_

  - [x] 2.2 Update `KBFile` DB model in `src/db/models.py`
    - Add `file_type: Mapped[str]` with `server_default=text("'markdown'")`
    - Change `md_content`, `title`, `content_type`, `component_type` from `Mapped[str]` to `Mapped[Optional[str]]` with `nullable=True`
    - _Requirements: 3.1_

  - [x] 2.3 Update `FileSummary` and `FileDetail` in `src/models/schemas.py`
    - Add `file_type: str = "markdown"` to both models
    - Make `title`, `content_type`, `md_content` optional (`str | None`) on `FileDetail`
    - Make `title`, `content_type` optional (`str | None`) on `FileSummary`
    - _Requirements: 6.1, 6.3_

- [x] 3. Add `upload_pdf` method to S3UploadService
  - [x] 3.1 Add `upload_pdf` method to `src/services/s3_upload.py`
    - Accept `pdf_bytes: bytes`, `filename: str`, `brand: str`, `region: str`, `namespace: str`, `file_id: UUID`, `content_hash: str`
    - Build S3 key as `{brand}/{region}/{namespace}/{filename}`
    - Upload with `ContentType="application/pdf"`
    - Return `S3UploadResult`
    - _Requirements: 2.3, 2.4_

- [x] 4. Implement PDF processing in pipeline
  - [x] 4.1 Add `_process_pdf_link` method to `PipelineService` in `src/services/pipeline.py`
    - Emit `progress` SSE with `stage: "pdf_download"` before download
    - HTTP GET the PDF URL via `httpx.get()` with configured timeout
    - Compute SHA-256 of the PDF bytes
    - Build filename as `{hash[:8]}_{original_filename}.pdf` (extract original filename from URL path)
    - Insert `kb_files` record with `file_type='pdf'`, `status='approved'`, nullable markdown fields as None
    - Call `S3UploadService.upload_pdf`
    - Update `kb_files` record with S3 metadata (`s3_bucket`, `s3_key`, `s3_uploaded_at`)
    - Emit `progress` SSE with `stage: "pdf_upload_complete"`
    - On any error: log, emit `progress` SSE with `stage: "pdf_download_error"`, return without raising
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 3.2, 3.3, 3.4, 3.5, 5.1, 5.2, 5.3_

  - [x] 4.2 Add branching logic in pipeline for PDF deep links
    - In the section where confirmed deep links are processed for ingestion, check `is_pdf_link(url)` before entering the AEM extraction flow
    - If PDF → call `_process_pdf_link`
    - If not PDF → existing `_process_single_url` path
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 5. Update file listing queries
  - [x] 5.1 Update DB queries in `src/db/queries.py` to include `file_type` in file listing results
    - Ensure both `markdown` and `pdf` records are returned
    - Map `file_type` to response models
    - _Requirements: 6.2_

  - [x] 5.2 Update file API endpoints in `src/api/files.py` to pass `file_type` through to response models
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 6. Checkpoint — verify all tests pass and PDF flow works end-to-end
    - Run existing tests to confirm backward compatibility
    - Verify no regressions in markdown file processing
    - _Requirements: 7.1, 7.2, 7.3_

## Notes

- PDF files are auto-approved (no validation/scoring needed)
- No size limit enforcement — large PDFs are out of scope for now
- The `is_pdf_link` check is purely path-based (no Content-Type sniffing via HEAD request)
- Hash prefix on filename prevents collisions when different PDFs share the same original filename
