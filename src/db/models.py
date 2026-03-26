"""SQLAlchemy ORM models for all 6 database tables.

Maps the complete schema produced by SQL migrations 001-007.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    ARRAY,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import TSVECTOR as TSVector
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    nav_root_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nav_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nav_section: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_ingested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    # Relationships
    ingestion_jobs: Mapped[list[IngestionJob]] = relationship(
        "IngestionJob", back_populates="source"
    )
    kb_files: Mapped[list[KBFile]] = relationship(
        "KBFile", back_populates="source"
    )
    deep_links: Mapped[list[DeepLink]] = relationship(
        "DeepLink", back_populates="source"
    )

    __table_args__ = (
        Index("idx_sources_region", "region"),
        Index("idx_sources_brand", "brand"),
        Index("idx_sources_url", "url"),
    )


# ---------------------------------------------------------------------------
# ingestion_jobs
# ---------------------------------------------------------------------------
class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'in_progress'")
    )
    total_nodes_found: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    files_created: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    files_auto_approved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    files_pending_review: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    files_auto_rejected: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    duplicates_skipped: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    child_urls: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    max_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    pages_crawled: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    current_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    source: Mapped[Optional[Source]] = relationship(
        "Source", back_populates="ingestion_jobs"
    )
    kb_files: Mapped[list[KBFile]] = relationship(
        "KBFile", back_populates="job"
    )

    __table_args__ = (
        Index("idx_ingestion_jobs_status", "status"),
        Index("idx_ingestion_jobs_source_id", "source_id"),
    )


# ---------------------------------------------------------------------------
# kb_files
# ---------------------------------------------------------------------------
class KBFile(Base):
    __tablename__ = "kb_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    component_type: Mapped[str] = mapped_column(Text, nullable=False)
    aem_node_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    md_content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    modify_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parent_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    namespace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    validation_breakdown: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    validation_issues: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending_review'")
    )
    s3_bucket: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_uploaded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=True
    )
    search_vector = mapped_column(TSVector, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    # Relationships
    source: Mapped[Optional[Source]] = relationship(
        "Source", back_populates="kb_files"
    )
    job: Mapped[Optional[IngestionJob]] = relationship(
        "IngestionJob", back_populates="kb_files"
    )

    __table_args__ = (
        Index("idx_kb_files_content_hash", "content_hash"),
        Index("idx_kb_files_status", "status"),
        Index("idx_kb_files_region", "region"),
        Index("idx_kb_files_brand", "brand"),
        Index("idx_kb_files_source_url", "source_url"),
        Index("idx_kb_files_content_type", "content_type"),
        Index("idx_kb_files_doc_type", "doc_type"),
        Index("idx_kb_files_created_at", "created_at"),
        Index("idx_kb_files_source_id", "source_id"),
        Index("idx_kb_files_job_id", "job_id"),
        Index(
            "idx_kb_files_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )


# ---------------------------------------------------------------------------
# revalidation_jobs
# ---------------------------------------------------------------------------
class RevalidationJob(Base):
    __tablename__ = "revalidation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'in_progress'")
    )
    total_files: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    not_found: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_revalidation_jobs_status", "status"),
    )


# ---------------------------------------------------------------------------
# nav_tree_cache
# ---------------------------------------------------------------------------
class NavTreeCache(Base):
    __tablename__ = "nav_tree_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    root_url: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True
    )
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    tree_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# ---------------------------------------------------------------------------
# deep_links
# ---------------------------------------------------------------------------
class DeepLink(Base):
    __tablename__ = "deep_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    model_json_url: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    found_in_node: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    found_in_page: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    # Relationships
    source: Mapped[Optional[Source]] = relationship(
        "Source", back_populates="deep_links"
    )
    job: Mapped[Optional[IngestionJob]] = relationship("IngestionJob")

    __table_args__ = (
        Index("idx_deep_links_source", "source_id"),
        Index("idx_deep_links_status", "status"),
        Index("idx_deep_links_job", "job_id"),
        UniqueConstraint("source_id", "url", name="uq_deep_links_source_url"),
    )
