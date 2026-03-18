"""Revalidation service for re-running validation on existing KB files.

Provides single-file and batch revalidation with the same score-routing
logic as the ingestion pipeline: approved → S3 upload, rejected, or
pending_review based on configured thresholds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.agents.validator import ValidatorAgent
from src.config import Settings
from src.db.queries import get_kb_file, update_kb_file_status, update_revalidation_job
from src.models.schemas import FileStatus, MarkdownFile, ValidationResult
from src.services.s3_upload import S3UploadService

logger = logging.getLogger(__name__)


class RevalidationService:
    """Re-validates existing KB files and updates their status."""

    def __init__(
        self,
        validator: ValidatorAgent,
        db_pool: asyncpg.Pool,
        s3_service: S3UploadService,
        settings: Settings,
    ) -> None:
        self.validator = validator
        self.db_pool = db_pool
        self.s3_service = s3_service
        self.settings = settings

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reconstruct_markdown_file(record: dict) -> MarkdownFile:
        """Rebuild a MarkdownFile from a DB record.

        ``md_body`` is derived by stripping YAML frontmatter (delimited by
        ``---``) from ``md_content``.  ``extracted_at`` is set to now(UTC).
        """
        md_content: str = record["md_content"]

        # Derive md_body by stripping frontmatter
        if md_content.startswith("---"):
            closing = md_content.find("---", 3)
            if closing != -1:
                md_body = md_content[closing + 3:].lstrip("\n")
            else:
                md_body = md_content
        else:
            md_body = md_content

        return MarkdownFile(
            filename=record["filename"],
            title=record["title"],
            content_type=record["content_type"],
            source_url=record["source_url"],
            component_type=record["component_type"],
            md_content=md_content,
            md_body=md_body,
            content_hash=record["content_hash"],
            extracted_at=datetime.now(timezone.utc),
            parent_context=record["parent_context"],
            region=record["region"],
            brand=record["brand"],
        )

    def _route_by_score(self, score: float) -> FileStatus:
        """Determine file status based on validation score thresholds."""
        if score >= self.settings.auto_approve_threshold:
            return FileStatus.APPROVED
        elif score >= self.settings.auto_reject_threshold:
            return FileStatus.PENDING_REVIEW
        else:
            return FileStatus.AUTO_REJECTED

    async def _route_and_update(
        self, file_id: UUID, result: ValidationResult
    ) -> None:
        """Update DB with validation results and routed status.

        If the file is approved, attempt S3 upload.  On S3 failure the file
        retains its ``approved`` status (consistent with pipeline behaviour).
        """
        status = self._route_by_score(result.score)

        await update_kb_file_status(
            self.db_pool,
            file_id,
            status=status.value,
            validation_score=result.score,
            validation_breakdown=result.breakdown.model_dump(),
            validation_issues=result.issues,
            doc_type=result.doc_type,
        )

        if status == FileStatus.APPROVED:
            record = await get_kb_file(self.db_pool, file_id)
            md_file = self._reconstruct_markdown_file(record)
            try:
                s3_result = await self.s3_service.upload(
                    md_file, file_id
                )
                await update_kb_file_status(
                    self.db_pool,
                    file_id,
                    status=FileStatus.IN_S3.value,
                    s3_bucket=s3_result.s3_bucket,
                    s3_key=s3_result.s3_key,
                    s3_uploaded_at=s3_result.s3_uploaded_at,
                )
            except Exception:
                logger.error(
                    "S3 upload failed for file_id=%s; retaining approved status",
                    file_id,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def revalidate_single(self, file_id: UUID) -> dict:
        """Revalidate one file synchronously. Returns updated file record.

        Raises FileNotFoundError if file_id doesn't exist.
        Raises RuntimeError if ValidatorAgent fails.
        """
        record = await get_kb_file(self.db_pool, file_id)
        if record is None:
            raise FileNotFoundError(f"File {file_id} not found")

        md_file = self._reconstruct_markdown_file(record)

        try:
            result = await self.validator.validate(md_file)
        except Exception as exc:
            raise RuntimeError(f"Validation failed for file {file_id}") from exc

        await self._route_and_update(file_id, result)
        return await get_kb_file(self.db_pool, file_id)

    async def revalidate_batch(self, job_id: UUID, file_ids: list[UUID]) -> None:
        """Background task: revalidate multiple files, updating job progress."""
        try:
            completed = 0
            failed = 0
            not_found = 0

            for file_id in file_ids:
                record = await get_kb_file(self.db_pool, file_id)
                if record is None:
                    not_found += 1
                    await update_revalidation_job(
                        self.db_pool, job_id,
                        not_found=not_found,
                    )
                    continue

                md_file = self._reconstruct_markdown_file(record)

                try:
                    result = await self.validator.validate(md_file)
                    await self._route_and_update(file_id, result)
                    completed += 1
                except Exception as exc:
                    logger.error(
                        "Batch revalidation failed for file_id=%s: %s",
                        file_id, exc, exc_info=True,
                    )
                    failed += 1

                await update_revalidation_job(
                    self.db_pool, job_id,
                    completed=completed,
                    failed=failed,
                    not_found=not_found,
                )

            await update_revalidation_job(
                self.db_pool, job_id,
                status="completed",
                completed=completed,
                failed=failed,
                not_found=not_found,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.error(
                "Batch revalidation job %s failed: %s",
                job_id, exc, exc_info=True,
            )
            await update_revalidation_job(
                self.db_pool, job_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc),
            )
