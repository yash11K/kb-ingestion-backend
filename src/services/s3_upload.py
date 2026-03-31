"""S3 upload service for uploading approved markdown files."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from src.models.schemas import MarkdownFile, S3UploadResult

logger = logging.getLogger(__name__)


class S3UploadService:
    """Uploads markdown files to S3 with structured keys and metadata."""

    def __init__(self, s3_client, bucket_name: str) -> None:
        self._s3_client = s3_client
        self._bucket_name = bucket_name

    async def upload(
        self, file: MarkdownFile, file_id: UUID,
    ) -> S3UploadResult:
        """Upload a markdown file to S3.

        Args:
            file: The markdown file to upload.
            file_id: The database ID of the file (assigned by DB, not on MarkdownFile).

        Returns:
            S3UploadResult with bucket, key, and upload timestamp.

        Raises:
            Exception: If the S3 upload fails. The caller should retain
                       the approved status and log the error.
        """
        key = self._build_key(file)

        try:
            await asyncio.to_thread(
                self._s3_client.put_object,
                Bucket=self._bucket_name,
                Key=key,
                Body=file.md_content.encode("utf-8"),
                ContentType="text/markdown",
                Metadata={
                    "file_id": str(file_id),
                    "content_hash": file.content_hash,
                },
            )
        except Exception:
            logger.error(
                "S3 upload failed for file_id=%s key=%s",
                file_id,
                key,
                exc_info=True,
            )
            raise

        return S3UploadResult(
            s3_bucket=self._bucket_name,
            s3_key=key,
            s3_uploaded_at=datetime.now(timezone.utc),
        )


    async def upload_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        brand: str,
        region: str,
        namespace: str,
        file_id: UUID,
        content_hash: str,
    ) -> S3UploadResult:
        """Upload raw PDF bytes to S3.

        Args:
            pdf_bytes: The raw PDF binary content.
            filename: Pre-built filename (e.g. {hash_prefix}_{original_name}.pdf).
            brand: Brand identifier for the S3 key path.
            region: Region identifier for the S3 key path.
            namespace: Namespace identifier for the S3 key path.
            file_id: The database ID of the kb_files record.
            content_hash: SHA-256 hex digest of the PDF bytes.

        Returns:
            S3UploadResult with bucket, key, and upload timestamp.

        Raises:
            Exception: If the S3 upload fails.
        """
        key = f"{brand}/{region}/{namespace}/{filename}"

        try:
            await asyncio.to_thread(
                self._s3_client.put_object,
                Bucket=self._bucket_name,
                Key=key,
                Body=pdf_bytes,
                ContentType="application/pdf",
                Metadata={
                    "file_id": str(file_id),
                    "content_hash": content_hash,
                },
            )
        except Exception:
            logger.error(
                "S3 PDF upload failed for file_id=%s key=%s",
                file_id,
                key,
                exc_info=True,
            )
            raise

        return S3UploadResult(
            s3_bucket=self._bucket_name,
            s3_key=key,
            s3_uploaded_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _build_key(file: MarkdownFile) -> str:
        """Build the S3 object key.

        Structure: {brand}/{region}/{namespace}/{filename}
        """
        return f"{file.brand}/{file.region}/{file.namespace}/{file.filename}"
