"""Pipeline orchestration service.

Coordinates the full ingestion pipeline: fetch → extract → insert to DB →
validate → route → upload approved → complete job.

Processes a flat list of URLs (no BFS crawling). Deep links are discovered
and stored for user confirmation, not auto-followed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import frontmatter as fm_lib

from src.agents.extractor import ExtractorAgent, PostProcessor
from src.agents.validator import ValidatorAgent
from src.config import Settings
from src.db.queries import (
    find_by_content_hash,
    insert_deep_links,
    insert_kb_file,
    update_ingestion_job,
    update_kb_file_status,
)
from src.models.schemas import ExtractionOutput, FileStatus, MarkdownFile
from src.services.deep_link_extractor import extract_deep_links
from src.services.s3_upload import S3UploadService
from src.services.stream_manager import StreamManager
from src.utils.url_inference import (
    infer_brand,
    infer_namespace,
    infer_region,
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
        self._concurrency_sem = asyncio.Semaphore(settings.max_concurrent_jobs)

    async def run(
        self,
        job_id: UUID,
        urls: list[str],
        source_id: UUID | None = None,
    ) -> None:
        """Execute the ingestion pipeline for a list of URLs.

        Processes each URL sequentially: fetch → extract → validate → route → upload.
        Discovers deep links in content and stores them for user confirmation.
        """
        # Register stream immediately so SSE clients can connect
        self.stream_manager.register(job_id)
        self.stream_manager.publish(job_id, "queued", {
            "stage": "queued",
            "message": f"Job queued — waiting for available slot ({len(urls)} URL(s))",
        })

        try:
            # Acquire semaphore — blocks if max_concurrent_jobs reached
            async with self._concurrency_sem:
                self.stream_manager.publish(job_id, "progress", {
                    "stage": "started",
                    "message": f"Pipeline started for {len(urls)} URL(s)",
                })
                try:
                    await self._run_pipeline(job_id, urls, source_id)
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
        urls: list[str],
        source_id: UUID | None,
    ) -> None:
        """Process each URL in the flat list sequentially."""
        sm = self.stream_manager

        # Job-level counters
        total_files_created = 0
        total_files_auto_approved = 0
        total_files_pending_review = 0
        total_files_auto_rejected = 0
        total_duplicates_skipped = 0
        pages_processed = 0
        failed_count = 0
        total_deep_links = 0

        for page_index, url in enumerate(urls, 1):
            sm.publish(job_id, "crawl_page_start", {
                "url": url,
                "depth": 0,
                "page_index": page_index,
            })

            # Infer metadata from URL
            brand = infer_brand(url)
            region = infer_region(url, self.settings.locale_region_map)
            namespace = infer_namespace(url, self.settings.namespace_list)

            counters, deep_links_found = await self._process_single_url(
                url, brand, region, namespace,
                job_id, source_id,
            )

            if counters.get("_failed"):
                failed_count += 1
                continue

            # Accumulate counters
            total_files_created += counters.get("files_created", 0)
            total_files_auto_approved += counters.get("files_auto_approved", 0)
            total_files_pending_review += counters.get("files_pending_review", 0)
            total_files_auto_rejected += counters.get("files_auto_rejected", 0)
            total_duplicates_skipped += counters.get("duplicates_skipped", 0)
            total_deep_links += deep_links_found
            pages_processed += 1

            sm.publish(job_id, "crawl_page_complete", {
                "url": url,
                "depth": 0,
                "files_extracted": counters.get("files_created", 0),
                "deep_links_found": deep_links_found,
            })

        # Emit summary
        sm.publish(job_id, "crawl_summary", {
            "total_pages": pages_processed,
            "total_files": total_files_created,
            "failed_count": failed_count,
            "deep_links_discovered": total_deep_links,
        })

        if total_deep_links > 0:
            sm.publish(job_id, "deep_links_discovered", {
                "count": total_deep_links,
                "message": (
                    f"Discovered {total_deep_links} embedded link(s) in content. "
                    "Review them in the source detail page."
                ),
            })

        # Update job with final counters
        await update_ingestion_job(
            self.db_pool,
            job_id,
            status="completed",
            files_created=total_files_created,
            files_auto_approved=total_files_auto_approved,
            files_pending_review=total_files_pending_review,
            files_auto_rejected=total_files_auto_rejected,
            duplicates_skipped=total_duplicates_skipped,
            pages_crawled=pages_processed,
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Pipeline completed for job_id=%s: pages=%d, created=%d, approved=%d, "
            "review=%d, rejected=%d, duplicates=%d, failed=%d, deep_links=%d",
            job_id, pages_processed, total_files_created, total_files_auto_approved,
            total_files_pending_review, total_files_auto_rejected,
            total_duplicates_skipped, failed_count, total_deep_links,
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
    ) -> tuple[dict, int]:
        """Extract → validate → route → upload for one URL.

        Returns (counters_dict, deep_links_count).
        On error: returns ({"_failed": True}, 0).
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

            all_results = extraction.files
            child_urls = extraction.child_urls

            # Patch namespace and rebuild frontmatter
            md_files: list[MarkdownFile] = []
            for f in all_results:
                fm_metadata = {
                    "key": f.key,
                    "namespace": namespace,
                    "brand": brand,
                    "region": region,
                    "source_url": f.source_url,
                    "parent_context": "",
                    "title": f.title,
                }
                post = fm_lib.Post(f.md_body, **fm_metadata)
                md_content = fm_lib.dumps(post)

                updated = f.model_copy(update={
                    "namespace": namespace,
                    "parent_context": "",
                    "brand": brand,
                    "region": region,
                    "md_content": md_content,
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

            # 2. Discover deep links in content
            deep_links_count = 0
            try:
                parsed_url = urlparse(url)
                base_host = f"{parsed_url.scheme}://{parsed_url.netloc}"

                # Get content nodes from the extraction for deep link scanning
                # We use the extraction's child_urls discovery as a baseline
                # but also scan HTML content for embedded links
                from src.models.schemas import ContentNode
                # Re-extract content nodes for deep link scanning
                # (the extractor already has them, but we need the raw nodes)
                import httpx
                import json
                try:
                    resp = httpx.get(url, timeout=self.settings.aem_request_timeout)
                    if resp.status_code == 200:
                        aem_json = resp.json()
                        if self.settings.enable_haiku_prefilter:
                            from src.services.haiku_prefilter import HaikuPrefilter
                            prefilter = HaikuPrefilter(self.settings)
                            content_nodes = prefilter.identify_content_paths(aem_json)
                        else:
                            from src.tools.filter_components import filter_by_component_type_direct
                            content_nodes = filter_by_component_type_direct(
                                aem_json,
                                self.settings.allowlist,
                                self.settings.denylist,
                            )

                        deep_links = extract_deep_links(
                            content_nodes,
                            source_page_url=url,
                            base_host=base_host,
                            url_denylist_patterns=self.settings.url_denylist_patterns,
                        )

                        if deep_links and source_id:
                            link_dicts = [
                                {
                                    "source_id": source_id,
                                    "job_id": job_id,
                                    "url": dl.url,
                                    "model_json_url": dl.model_json_url,
                                    "anchor_text": dl.anchor_text,
                                    "found_in_node": dl.found_in_node,
                                    "found_in_page": dl.found_in_page,
                                }
                                for dl in deep_links
                            ]
                            await insert_deep_links(self.db_pool, link_dicts)
                            deep_links_count = len(deep_links)
                            logger.info(
                                "Stored %d deep links from %s for user confirmation",
                                deep_links_count, url,
                            )
                except Exception as exc:
                    logger.warning(
                        "Deep link extraction failed for %s: %s", url, exc,
                    )
            except Exception as exc:
                logger.warning("Deep link discovery failed: %s", exc)

            # 3. Process each file
            for idx, md_file in enumerate(md_files, 1):
                sm.publish(job_id, "progress", {
                    "stage": "processing",
                    "message": f"Processing file {idx}/{total_nodes}: {md_file.filename}",
                    "current": idx,
                    "total": total_nodes,
                })

                file_id = await self._insert_file(md_file, source_id, job_id)
                counters["files_created"] += 1

                try:
                    sm.publish(job_id, "progress", {
                        "stage": "validation",
                        "message": f"Validating file {idx}/{total_nodes}: {md_file.filename}",
                        "current": idx,
                        "total": total_nodes,
                    })
                    validation = await self.validator.validate(
                        md_file, job_id, sm
                    )

                    status = self._route_by_score(validation.score)

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

            return counters, deep_links_count

        except Exception as exc:
            logger.error(
                "Error processing URL %s for job_id=%s: %s",
                url, job_id, exc, exc_info=True,
            )
            sm.publish(job_id, "crawl_page_error", {
                "url": url,
                "error": str(exc),
            })
            return {"_failed": True}, 0

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
        """Upload an approved file to S3 and update its DB status to in_s3."""
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
