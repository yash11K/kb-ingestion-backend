"""Pipeline orchestration service.

Simplified flow: fetch JSON once → Haiku discovery (content + links) →
Sonnet extraction → Haiku validation → route → upload.

3 agent calls per URL, no Python filtering, no allowlist/denylist config.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

import httpx
import frontmatter as fm_lib

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents.discovery import DiscoveryAgent
from src.agents.extractor import ExtractorAgent
from src.agents.validator import ValidatorAgent
from src.config import Settings
from src.db.queries import (
    insert_deep_links,
    insert_kb_file,
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
)

logger = logging.getLogger(__name__)


class PipelineService:
    """Orchestrates the AEM content ingestion pipeline."""

    def __init__(
        self,
        discovery: DiscoveryAgent,
        extractor: ExtractorAgent,
        validator: ValidatorAgent,
        session_factory: async_sessionmaker[AsyncSession],
        s3_service: S3UploadService,
        settings: Settings,
        stream_manager: StreamManager,
    ) -> None:
        self.discovery = discovery
        self.extractor = extractor
        self.validator = validator
        self.session_factory = session_factory
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
        """Execute the ingestion pipeline for a list of URLs."""
        self.stream_manager.register(job_id)
        self.stream_manager.publish(job_id, "queued", {
            "stage": "queued",
            "message": f"Job queued — waiting for available slot ({len(urls)} URL(s))",
        })

        try:
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
                    async with self.session_factory() as session:
                        await update_ingestion_job(
                            session, job_id,
                            status="failed",
                            error_message=str(exc),
                            completed_at=datetime.now(timezone.utc),
                        )
                        await session.commit()
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
        """Process each URL sequentially."""
        sm = self.stream_manager

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

            brand = infer_brand(url)
            region = infer_region(url, self.settings.locale_region_map)
            namespace = infer_namespace(url, self.settings.namespace_list)

            counters, deep_links_found = await self._process_single_url(
                url, brand, region, namespace, job_id, source_id,
            )

            if counters.get("_failed"):
                failed_count += 1
                continue

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

        async with self.session_factory() as session:
            await update_ingestion_job(
                session, job_id,
                status="completed",
                files_created=total_files_created,
                files_auto_approved=total_files_auto_approved,
                files_pending_review=total_files_pending_review,
                files_auto_rejected=total_files_auto_rejected,
                duplicates_skipped=total_duplicates_skipped,
                pages_crawled=pages_processed,
                completed_at=datetime.now(timezone.utc),
            )
            await session.commit()

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
        """Fetch → discover → extract → validate → route → upload for one URL.

        Returns (counters_dict, deep_links_count).
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
            # 1. Fetch JSON once
            logger.info("Starting extraction for job_id=%s url=%s", job_id, url)
            sm.publish(job_id, "progress", {
                "stage": "fetch",
                "message": f"Fetching AEM JSON from {url}",
            })

            resp = httpx.get(url, timeout=self.settings.aem_request_timeout)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"AEM endpoint returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            aem_json = resp.json()

            payload_bytes = len(json.dumps(aem_json))
            logger.info(
                "Raw AEM JSON payload: %d bytes, estimated %d tokens",
                payload_bytes, payload_bytes // 4,
            )

            # 2. Haiku discovery (content + deep links in one pass)
            sm.publish(job_id, "progress", {
                "stage": "discovery",
                "message": f"Haiku discovering content and links from {url}",
            })
            discovery = await self.discovery.discover(aem_json, url)

            logger.info(
                "Discovery complete: %d content items, %d deep links from %s",
                len(discovery.content_items), len(discovery.deep_links), url,
            )

            # 3. Store deep links
            deep_links_count = 0
            if discovery.deep_links and source_id:
                try:
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
                        for dl in discovery.deep_links
                    ]
                    async with self.session_factory() as session:
                        await insert_deep_links(session, link_dicts)
                        await session.commit()
                    deep_links_count = len(discovery.deep_links)
                    logger.info(
                        "Stored %d deep links from %s",
                        deep_links_count, url,
                    )
                except Exception as exc:
                    logger.warning("Failed to store deep links: %s", exc)

            if not discovery.content_items:
                logger.info("No content items discovered for %s", url)
                return counters, deep_links_count

            # 4. Sonnet extraction
            sm.publish(job_id, "progress", {
                "stage": "extraction",
                "message": f"Extracting {len(discovery.content_items)} content items from {url}",
            })
            extraction: ExtractionOutput = await self.extractor.extract(
                content_items=discovery.content_items,
                url=url,
                region=region,
                brand=brand,
                namespace=namespace,
                job_id=job_id,
                stream_manager=sm,
            )

            md_files = extraction.files
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

            # 4b. Store embedded links from extractor (complements discovery links)
            if extraction.embedded_links and source_id:
                try:
                    ext_link_dicts = [
                        {
                            "source_id": source_id,
                            "job_id": job_id,
                            "url": dl.url,
                            "model_json_url": dl.model_json_url,
                            "anchor_text": dl.anchor_text,
                            "found_in_node": dl.found_in_node,
                            "found_in_page": dl.found_in_page,
                        }
                        for dl in extraction.embedded_links
                    ]
                    async with self.session_factory() as session:
                        await insert_deep_links(session, ext_link_dicts)
                        await session.commit()
                    deep_links_count += len(extraction.embedded_links)
                    logger.info(
                        "Stored %d embedded links from extractor for %s",
                        len(extraction.embedded_links), url,
                    )
                except Exception as exc:
                    logger.warning("Failed to store extractor embedded links: %s", exc)

            # 5. Per file: insert → validate → route → upload
            for idx, md_file in enumerate(md_files, 1):
                sm.publish(job_id, "progress", {
                    "stage": "processing",
                    "message": f"Processing file {idx}/{total_nodes}: {md_file.filename}",
                    "current": idx,
                    "total": total_nodes,
                })

                async with self.session_factory() as session:
                    file_id = await self._insert_file(session, md_file, source_id, job_id)
                    await session.commit()
                counters["files_created"] += 1

                try:
                    sm.publish(job_id, "progress", {
                        "stage": "validation",
                        "message": f"Validating file {idx}/{total_nodes}: {md_file.filename}",
                        "current": idx,
                        "total": total_nodes,
                    })
                    validation = await self.validator.validate(md_file, job_id, sm)

                    status = self._route_by_score(
                        validation.score,
                        validation.breakdown.semantic_quality,
                    )

                    async with self.session_factory() as session:
                        await update_kb_file_status(
                            session, file_id,
                            status=status.value,
                            validation_score=validation.score,
                            validation_breakdown=validation.breakdown.model_dump(),
                            validation_issues=validation.issues,
                            doc_type=validation.doc_type,
                        )
                        await session.commit()

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
                    counters["files_pending_review"] += 1

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

    async def _insert_file(
        self,
        session: AsyncSession,
        md_file: MarkdownFile,
        source_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> UUID:
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
        return await insert_kb_file(session, file_dict)

    def _route_by_score(self, score: float, semantic_quality: float = 1.0) -> FileStatus:
        """Determine file status based on validation score and semantic quality.

        Auto-approval requires both the total score threshold AND semantic
        quality >= 90% of its max (0.45 out of 0.5).
        """
        min_semantic = 0.4  # 80% of 0.5 max
        if score >= self.settings.auto_approve_threshold and semantic_quality >= min_semantic:
            return FileStatus.APPROVED
        elif score >= self.settings.auto_reject_threshold:
            return FileStatus.PENDING_REVIEW
        else:
            return FileStatus.AUTO_REJECTED

    async def _upload_to_s3(
        self, file_id: UUID, md_file: MarkdownFile,
    ) -> None:
        """Upload an approved file to S3 and update its DB status."""
        try:
            result = await self.s3_service.upload(md_file, file_id)
            async with self.session_factory() as session:
                await update_kb_file_status(
                    session, file_id,
                    status=FileStatus.IN_S3.value,
                    s3_bucket=result.s3_bucket,
                    s3_key=result.s3_key,
                    s3_uploaded_at=result.s3_uploaded_at,
                )
                await session.commit()
        except Exception:
            logger.error(
                "S3 upload failed for file_id=%s; retaining approved status",
                file_id, exc_info=True,
            )
