"""Pydantic data models for the AEM Knowledge Base Ingestion System."""

from pydantic import BaseModel, Field, HttpUrl, field_validator
from uuid import UUID
from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")

# --- Enums ---


class FileStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    AUTO_REJECTED = "auto_rejected"
    IN_S3 = "in_s3"
    REJECTED = "rejected"


class JobStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Internal Models ---


class ContentNode(BaseModel):
    node_type: str                       # :type value
    aem_node_id: str                     # path/key in the JSON tree
    html_content: str                    # raw HTML from the node
    parent_context: str                  # parent node path
    metadata: dict                       # additional node metadata


class MarkdownFile(BaseModel):
    filename: str
    title: str
    content_type: str
    source_url: str
    component_type: str
    key: str = ""                        # AEM component key (e.g. "contentcardelement_821372053")
    namespace: str = ""                  # inferred from URL path
    md_content: str                      # full markdown with frontmatter
    md_body: str                         # markdown body only (no frontmatter)
    content_hash: str                    # SHA-256 of md_body
    extracted_at: datetime
    parent_context: str
    region: str
    brand: str


class ExtractionOutput(BaseModel):
    """Return value from ExtractorAgent.extract().

    Bundles the produced MarkdownFiles together with any internal AEM page
    URLs discovered during extraction (e.g. from contentcardelement.ctaLink).
    Callers can use ``child_urls`` to schedule deeper ingestion passes.
    """
    files: list["MarkdownFile"]
    child_urls: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    title: str
    content_type: str
    markdown_body: str  # must be non-empty (validator)
    source_nodes: list[str]  # aem_node_ids that contributed
    component_type: str
    source_url: str
    parent_context: str
    grouping_rationale: str

    @field_validator("markdown_body")
    @classmethod
    def markdown_body_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("markdown_body must not be empty")
        return v


class ValidationBreakdown(BaseModel):
    metadata_completeness: float = Field(ge=0.0, le=0.3)
    semantic_quality: float = Field(ge=0.0, le=0.5)
    uniqueness: float = Field(ge=0.0, le=0.2)


class ValidationResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    breakdown: ValidationBreakdown
    issues: list[str]
    doc_type: str = "unknown"            # AI-classified document type (e.g. TnC, FAQ, ProductGuide)


class S3UploadResult(BaseModel):
    s3_bucket: str
    s3_key: str
    s3_uploaded_at: datetime


class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    existing_file_id: UUID | None = None


class FrontmatterResult(BaseModel):
    metadata: dict
    body: str
    missing_fields: list[str]
    valid: bool


# --- API Request Models ---


class IngestRequest(BaseModel):
    urls: list[HttpUrl] = Field(..., min_length=1)
    nav_root_url: str | None = None       # the home page this nav tree came from
    nav_metadata: dict | None = None      # {url -> {label, section}} for source enrichment



class ReIngestRequest(BaseModel):
    """Request body for re-ingesting an existing source (no fields needed,
    region/brand come from the source)."""
    pass


class AcceptRequest(BaseModel):
    reviewed_by: str


class RejectRequest(BaseModel):
    reviewed_by: str
    review_notes: str


class UpdateRequest(BaseModel):
    md_content: str


class RevalidateRequest(BaseModel):
    file_ids: list[UUID] = Field(..., min_length=1)


# --- API Response Models ---


class IngestResponse(BaseModel):
    source_id: UUID
    job_id: UUID
    status: JobStatus


class BatchIngestItem(BaseModel):
    source_id: UUID
    job_id: UUID
    url: str


class BatchIngestResponse(BaseModel):
    jobs: list[BatchIngestItem]
    status: JobStatus


class RevalidateResponse(BaseModel):
    job_id: UUID
    status: JobStatus


class RevalidationJobResponse(BaseModel):
    id: UUID
    status: JobStatus
    total_files: int
    completed: int
    failed: int
    not_found: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class QueueActionResponse(BaseModel):
    file_id: UUID
    status: FileStatus
    message: str


class QueueItemSummary(BaseModel):
    id: UUID
    filename: str
    title: str
    content_type: str
    component_type: str
    region: str
    brand: str
    validation_score: float | None
    created_at: datetime


class QueueItemDetail(BaseModel):
    id: UUID
    filename: str
    title: str
    content_type: str
    component_type: str
    source_url: str
    aem_node_id: str | None = None
    md_content: str
    region: str
    brand: str
    validation_score: float | None
    validation_breakdown: ValidationBreakdown | None
    validation_issues: list[str] | None
    created_at: datetime
    updated_at: datetime


class FileSummary(BaseModel):
    id: UUID
    filename: str
    title: str
    content_type: str
    status: FileStatus
    region: str
    brand: str
    validation_score: float | None
    created_at: datetime


class FileDetail(BaseModel):
    id: UUID
    filename: str
    title: str
    content_type: str
    content_hash: str
    source_url: str
    component_type: str
    aem_node_id: str | None = None
    md_content: str
    modify_date: datetime | None = None
    parent_context: str
    region: str
    brand: str
    doc_type: str | None                 # AI-classified document type (TnC, FAQ, etc.)
    validation_score: float | None
    validation_breakdown: ValidationBreakdown | None
    validation_issues: list[str] | None
    status: FileStatus
    s3_bucket: str | None
    s3_key: str | None
    s3_uploaded_at: datetime | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_at: datetime
    updated_at: datetime


class IngestionJobResponse(BaseModel):
    id: UUID
    source_id: UUID | None
    source_url: str
    status: JobStatus
    total_nodes_found: int | None
    files_created: int
    files_auto_approved: int
    files_pending_review: int
    files_auto_rejected: int
    duplicates_skipped: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    child_urls: list[str] = Field(default_factory=list)
    max_depth: int = 0
    pages_crawled: int = 1
    current_depth: int = 0


class SourceSummary(BaseModel):
    id: UUID
    url: str
    region: str
    brand: str
    nav_label: str | None = None
    nav_section: str | None = None
    last_ingested_at: datetime | None
    created_at: datetime


class SourceDetail(BaseModel):
    id: UUID
    url: str
    region: str
    brand: str
    last_ingested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Aggregate stats
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    active_jobs: int
    total_files: int
    pending_review: int
    approved: int
    rejected: int


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


class NavPreviewItem(BaseModel):
    url: str
    depth: int
    parent_url: str | None = None


class NavPreviewResponse(BaseModel):
    root_url: str
    total_urls: int
    urls_by_depth: dict[int, list[NavPreviewItem]]
    summary: dict[int, int]              # depth level → count of URLs at that depth


# --- Navigation Tree Models ---


class NavTreeNode(BaseModel):
    label: str
    url: str | None = None               # None for category headers
    model_json_url: str | None = None
    is_external: bool = False
    children: list["NavTreeNode"] = Field(default_factory=list)


class NavTreeSection(BaseModel):
    section_name: str                     # "Hamburger Menu", "Footer Links", etc.
    nodes: list[NavTreeNode]


class NavTree(BaseModel):
    brand: str
    region: str
    base_url: str
    sections: list[NavTreeSection]


# --- Deep Link Models ---


class DeepLinkStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    INGESTED = "ingested"


class DeepLink(BaseModel):
    url: str
    model_json_url: str
    anchor_text: str = ""
    found_in_node: str = ""
    found_in_page: str = ""


class DeepLinkResponse(BaseModel):
    id: UUID
    url: str
    model_json_url: str
    anchor_text: str | None
    found_in_node: str | None
    found_in_page: str
    status: DeepLinkStatus
    created_at: datetime


class DeepLinkConfirmRequest(BaseModel):
    link_ids: list[UUID] = Field(..., min_length=1)


class DeepLinkDismissRequest(BaseModel):
    link_ids: list[UUID] = Field(..., min_length=1)


class StatsResponse(BaseModel):
    total_files: int
    pending_review: int
    approved: int
    rejected: int
    avg_score: float
