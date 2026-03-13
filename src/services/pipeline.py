"""Pipeline orchestration service.

Coordinates the full ingestion pipeline: fetch → extract → insert to DB →
validate → route → upload approved → complete job.

Supports opt-in BFS crawl loop for recursive child URL processing.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.agents.extractor import ExtractorAgent, PostProcessor
from src.agents.validator import ValidatorAgent
from src.config import Settings
from src.db.queries import (
    find_by_content_hash,
    insert_kb_file,
    update_crawl_progress,
    update_ingestion_job,
    update_kb_file_status,
)
from src.models.schemas import ExtractionOutput, FileStatus, MarkdownFile
from src.services.s3_upload import S3UploadService
from src.services.stream_manager import StreamManager
from src.utils.url_inference import (
    infer_brand,
    infer_namespace,
    infer_region,
    normalize_for_matching,
    normalize_url,
)

logger = logging.getLogger(__name__)


class PipelineService:
    """Orchestrates the AEM content ingestion pipeline."""

    def __init__(
        self,
        extractor: ExtractorAgent,
        validator: ValidatorAgent,
        db_pool: asyncpg.Pool,
        s3_service: S3UploadService,
        settings: Settings,
        stream_manager: StreamManager,
    ) -> None:
        self.extractor = extractor
        self.validator = validator
        self.db_pool = db_pool
        self.s3_service = s3_service
        self.settings = settings
        self.stream_manager = stream_manager

    async def run(
        self,
        job_id: UUID,
        url: str,
        max_depth: int = 0,
        confirmed_urls: list[str] | None = None,
        source_id: UUID | None = None,
    ) -> None:
        """Execute the full ingestion pipeline for a job.

        Infers brand, region, and namespace from the URL, clamps max_depth
        to the system-configured maximum, persists the effective depth in
        the job record, and delegates to the BFS crawl loop.
        """
        self.stream_manager.register(job_id)
        self.stream_manager.publish(job_id, "progress", {
            "stage": "started",
            "message": f"Pipeline started for {url}",
        })
        try:
            # Infer brand, region, namespace from URL
            brand = infer_brand(url)
            region = infer_region(url, self.settings.locale_region_map)
            namespace = infer_namespace(url, self.settings.namespace_list)

            # Clamp max_depth to system-configured maximum
            effective_depth = min(max_depth, self.settings.max_crawl_depth)

            # Persist effective max_depth in job record
            await update_ingestion_job(
                self.db_pool, job_id, max_depth=effective_depth
            )

            logger.info(
                "job_id=%s: brand=%s, region=%s, namespace=%s, "
                "requested_depth=%d, effective_depth=%d",
                job_id, brand, region, namespace, max_depth, effective_depth,
            )

            await self._run_pipeline(
                job_id, url, brand, region, namespace,
                effective_depth, confirmed_urls, source_id,
            )
        except Exception as exc:
            logger.error(
                "Pipeline failed for job_id=%s: %s", job_id, exc, exc_info=True
            )
            await update_ingestion_job(
                self.db_pool,
                job_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc),
            )
            self.stream_manager.publish(job_id, "error", {
                "message": str(exc),
            })
        finally:
            self.stream_manager.finish(job_id)

    async def _run_pipeline(
        self,
        job_id: UUID,
        url: str,
        brand: str,
        region: str,
        namespace: str,
        effective_depth: int,
        confirmed_urls: list[str] | None,
        source_id: UUID | None,
    ) -> None:
        """BFS crawl loop wrapping _process_single_url.

        Initializes a BFS queue with the seed URL at depth 0, processes
        each URL via _process_single_url, and enqueues discovered child
        URLs at current_depth + 1 — subject to depth limits, cycle
        detection, and optional confirmed_urls filtering.
        """
        sm = self.stream_manager

        # Normalize confirmed_urls for matching (if provided)
        confirmed_set: set[str] | None = None
        if confirmed_urls:
            confirmed_set = {normalize_for_matching(u) for u in confirmed_urls}

        # BFS state — each entry is (url, depth, parent_url)
        bfs_queue: deque[tuple[str, int, str | None]] = deque()
        bfs_queue.append((url, 0, None))
        visited: set[str] = set()

        # Job-level counters
        total_files_created = 0
        total_files_auto_approved = 0
        total_files_pending_review = 0
        total_files_auto_rejected = 0
        total_duplicates_skipped = 0
        pages_crawled = 0
        max_depth_reached = 0
        skipped_count = 0
        failed_count = 0
        page_index = 0

        while bfs_queue:
            current_url, depth, parent_url = bfs_queue.popleft()
            normalized = normalize_url(current_url)

            # Cycle detection
            if normalized in visited:
                skipped_count += 1
                sm.publish(job_id, "crawl_page_skipped", {
                    "url": current_url,
                    "reason": "already_visited",
                })
                logger.debug(
                    "Skipping already-visited URL: %s (job_id=%s)",
                    current_url, job_id,
                )
                continue

            visited.add(normalized)
            page_index += 1

            # Emit crawl_page_start
            sm.publish(job_id, "crawl_page_start", {
                "url": current_url,
                "depth": depth,
                "page_index": page_index,
            })

            child_urls, counters = await self._process_single_url(
                current_url, brand, region, namespace,
                job_id, source_id,
                parent_url=parent_url,
            )

            if counters.get("_failed"):
                failed_count += 1
                # crawl_page_error already emitted by _process_single_url
                continue

            # Accumulate counters
            total_files_created += counters.get("files_created", 0)
            total_files_auto_approved += counters.get("files_auto_approved", 0)
            total_files_pending_review += counters.get("files_pending_review", 0)
            total_files_auto_rejected += counters.get("files_auto_rejected", 0)
            total_duplicates_skipped += counters.get("duplicates_skipped", 0)
            pages_crawled += 1
            max_depth_reached = max(max_depth_reached, depth)

            # Update crawl progress in DB
            await update_crawl_progress(self.db_pool, job_id, pages_crawled, depth)

            files_extracted = counters.get("files_created", 0)

            # Emit crawl_page_complete
            sm.publish(job_id, "crawl_page_complete", {
                "url": current_url,
                "depth": depth,
                "files_extracted": files_extracted,
                "new_child_urls": len(child_urls),
            })

            # For depth 0 with max_depth == 0, emit child_urls_discovered
            # but don't follow them (backward compatibility)
            if depth == 0 and effective_depth == 0 and child_urls:
                await update_ingestion_job(
                    self.db_pool, job_id, child_urls=child_urls
                )
                sm.publish(job_id, "child_urls_discovered", {
                    "count": len(child_urls),
                    "urls": child_urls,
                    "message": (
                        f"Discovered {len(child_urls)} child page(s). "
                        "Submit each URL via POST /ingest to extract deeper content."
                    ),
                })
                logger.info(
                    "job_id=%s discovered %d child URLs: %s",
                    job_id, len(child_urls), child_urls,
                )

            # Enqueue child URLs if within depth limit
            if depth + 1 <= effective_depth and child_urls:
                for child_url in child_urls:
                    child_normalized = normalize_url(child_url)
                    if child_normalized in visited:
                        continue

                    # Apply confirmed_urls filter
                    if confirmed_set is not None:
                        child_match = normalize_for_matching(child_url)
                        if child_match not in confirmed_set:
                            continue

                    bfs_queue.append((child_url, depth + 1, current_url))

        # Emit crawl_summary
        sm.publish(job_id, "crawl_summary", {
            "total_pages": pages_crawled,
            "total_files": total_files_created,
            "max_depth_reached": max_depth_reached,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
        })

        # Update job with final counters and completed status
        await update_ingestion_job(
            self.db_pool,
            job_id,
            status="completed",
            files_created=total_files_created,
            files_auto_approved=total_files_auto_approved,
            files_pending_review=total_files_pending_review,
            files_auto_rejected=total_files_auto_rejected,
            duplicates_skipped=total_duplicates_skipped,
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Pipeline completed for job_id=%s: pages=%d, created=%d, approved=%d, "
            "review=%d, rejected=%d, duplicates=%d, skipped=%d, failed=%d",
            job_id, pages_crawled, total_files_created, total_files_auto_approved,
            total_files_pending_review, total_files_auto_rejected,
            total_duplicates_skipped, skipped_count, failed_count,
        )

        sm.publish(job_id, "complete", {
            "message": "Pipeline completed",
            "files_created": total_files_created,
            "files_auto_approved": total_files_auto_approved,
            "files_pending_review": total_files_pending_review,
            "files_auto_rejected": total_files_auto_rejected,
            "duplicates_skipped": total_duplicates_skipped,
        })

    async def _process_single_url(
        self,
        url: str,
        brand: str,
        region: str,
        namespace: str,
        job_id: UUID,
        source_id: UUID | None,
        parent_url: str | None = None,
    ) -> tuple[list[str], dict]:
        """Extract → validate → route → upload for one URL.

        Returns (child_urls, counters_dict) where counters_dict has keys:
        files_created, files_auto_approved, files_pending_review,
        files_auto_rejected, duplicates_skipped.

        On error: logs, emits crawl_page_error SSE event, returns
        ([], {"_failed": True}).
        """
        sm = self.stream_manager
        counters = {
            "files_created": 0,
            "files_auto_approved": 0,
            "files_pending_review": 0,
            "files_auto_rejected": 0,
            "duplicates_skipped": 0,
        }

        try:
            # 1. Fetch & extract
            logger.info("Starting extraction for job_id=%s url=%s", job_id, url)
            sm.publish(job_id, "progress", {
                "stage": "extraction",
                "message": f"Fetching and extracting content from {url}",
            })
            extraction: ExtractionOutput = await self.extractor.extract(
                url, region, brand, job_id, sm
            )

            # PostProcessor now accepts namespace and parent_url
            all_results = extraction.files  # already MarkdownFile objects
            child_urls = extraction.child_urls

            # Re-process through PostProcessor if namespace/parent_url need injection
            # The extractor already calls PostProcessor.process() internally,
            # but without namespace and parent_url. We need to re-process.
            # Actually, looking at the extractor code, it calls:
            #   files = PostProcessor.process(all_results, url, region, brand)
            # We need it to pass namespace and parent_url. Since we can't
            # change the extractor call here, we'll re-run PostProcessor
            # with the extraction results.
            # But extraction.files are already MarkdownFile objects, not
            # ExtractionResult objects. The raw results are lost.
            #
            # The cleanest approach: the extractor returns files processed
            # without namespace/parent_url, so we update the fields directly.
            md_files: list[MarkdownFile] = []
            for f in all_results:
                # Update namespace, parent_context, brand, region on each file
                updated = f.model_copy(update={
                    "namespace": namespace,
                    "parent_context": parent_url or "",
                    "brand": brand,
                    "region": region,
                })
                md_files.append(updated)

            total_nodes = len(md_files)
            logger.info(
                "Extraction complete for job_id=%s url=%s: %d markdown files",
                job_id, url, total_nodes,
            )
            sm.publish(job_id, "progress", {
                "stage": "extraction_complete",
                "message": f"Extraction complete: {total_nodes} markdown files produced",
                "total_nodes": total_nodes,
            })
            await update_ingestion_job(
                self.db_pool, job_id, total_nodes_found=total_nodes
            )

            # 2. Process each file
            for idx, md_file in enumerate(md_files, 1):
                sm.publish(job_id, "progress", {
                    "stage": "processing",
                    "message": f"Processing file {idx}/{total_nodes}: {md_file.filename}",
                    "current": idx,
                    "total": total_nodes,
                })

                # 2b. Insert to DB with status pending_review
                file_id = await self._insert_file(md_file, source_id, job_id)
                counters["files_created"] += 1

                try:
                    # 2c. Validate
                    sm.publish(job_id, "progress", {
                        "stage": "validation",
                        "message": f"Validating file {idx}/{total_nodes}: {md_file.filename}",
                        "current": idx,
                        "total": total_nodes,
                    })
                    validation = await self.validator.validate(
                        md_file, job_id, sm
                    )

                    # 2d. Route based on score
                    status = self._route_by_score(validation.score)

                    # 2e. Store validation results and update status
                    await update_kb_file_status(
                        self.db_pool,
                        file_id,
                        status=status.value,
                        validation_score=validation.score,
                        validation_breakdown=validation.breakdown.model_dump(),
                        validation_issues=validation.issues,
                        doc_type=validation.doc_type,
                    )

                    if status == FileStatus.APPROVED:
                        counters["files_auto_approved"] += 1
                    elif status == FileStatus.PENDING_REVIEW:
                        counters["files_pending_review"] += 1
                    else:
                        counters["files_auto_rejected"] += 1

                    sm.publish(job_id, "progress", {
                        "stage": "validated",
                        "message": f"{md_file.filename} → {status.value} (score: {validation.score:.2f})",
                        "filename": md_file.filename,
                        "status": status.value,
                        "score": validation.score,
                    })

                    # 2f. Upload approved files to S3
                    if status == FileStatus.APPROVED:
                        sm.publish(job_id, "progress", {
                            "stage": "s3_upload",
                            "message": f"Uploading {md_file.filename} to S3",
                        })
                        await self._upload_to_s3(file_id, md_file)
                except Exception as exc:
                    logger.error(
                        "Validation failed for file_id=%s: %s",
                        file_id, exc, exc_info=True,
                    )
                    sm.publish(job_id, "progress", {
                        "stage": "validation_error",
                        "message": f"Validation failed for {md_file.filename}: {exc}",
                        "filename": md_file.filename,
                    })
                    counters["files_pending_review"] += 1
                    continue

            return child_urls, counters

        except Exception as exc:
            logger.error(
                "Error processing URL %s for job_id=%s: %s",
                url, job_id, exc, exc_info=True,
            )
            sm.publish(job_id, "crawl_page_error", {
                "url": url,
                "error": str(exc),
            })
            return [], {"_failed": True}

    async def _insert_file(self, md_file: MarkdownFile,
                           source_id: UUID | None = None,
                           job_id: UUID | None = None) -> UUID:
        """Insert a MarkdownFile into the DB with pending_review status."""
        file_dict = {
            "filename": md_file.filename,
            "title": md_file.title,
            "content_type": md_file.content_type,
            "content_hash": md_file.content_hash,
            "source_url": md_file.source_url,
            "component_type": md_file.component_type,
            "md_content": md_file.md_content,
            "parent_context": md_file.parent_context,
            "region": md_file.region,
            "brand": md_file.brand,
            "key": md_file.key,
            "namespace": md_file.namespace,
            "validation_score": None,
            "validation_breakdown": None,
            "validation_issues": None,
            "status": FileStatus.PENDING_REVIEW.value,
            "source_id": source_id,
            "job_id": job_id,
        }
        return await insert_kb_file(self.db_pool, file_dict)

    def _route_by_score(self, score: float) -> FileStatus:
        """Determine file status based on validation score thresholds."""
        if score >= self.settings.auto_approve_threshold:
            return FileStatus.APPROVED
        elif score >= self.settings.auto_reject_threshold:
            return FileStatus.PENDING_REVIEW
        else:
            return FileStatus.AUTO_REJECTED

    async def _upload_to_s3(
        self, file_id: UUID, md_file: MarkdownFile,
    ) -> None:
        """Upload an approved file to S3 and update its DB status to in_s3.

        On S3 failure, the file retains its approved status and the error
        is logged for later retry.
        """
        try:
            result = await self.s3_service.upload(md_file, file_id)
            await update_kb_file_status(
                self.db_pool,
                file_id,
                status=FileStatus.IN_S3.value,
                s3_bucket=result.s3_bucket,
                s3_key=result.s3_key,
                s3_uploaded_at=result.s3_uploaded_at,
            )
        except Exception:
            logger.error(
                "S3 upload failed for file_id=%s; retaining approved status",
                file_id,
                exc_info=True,
            )
