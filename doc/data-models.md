# Data Models

## Database Schema

### kb_files

The primary table tracking all Markdown files through their lifecycle.

```sql
CREATE TABLE kb_files (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename             TEXT NOT NULL,
    title                TEXT NOT NULL,
    content_type         TEXT NOT NULL,
    content_hash         TEXT NOT NULL,          -- SHA-256 of markdown body
    source_url           TEXT NOT NULL,
    component_type       TEXT NOT NULL,
    aem_node_id          TEXT NOT NULL,
    md_content           TEXT NOT NULL,          -- Full markdown with frontmatter
    modify_date          TIMESTAMPTZ,
    parent_context       TEXT,
    region               TEXT NOT NULL,
    brand                TEXT NOT NULL,
    doc_type             TEXT,                   -- AI-classified: TnC, FAQ, ProductGuide, etc.
    validation_score     FLOAT,
    validation_breakdown JSONB,                  -- {metadata_completeness, semantic_quality, uniqueness}
    validation_issues    JSONB,                  -- ["issue1", "issue2", ...]
    status               TEXT NOT NULL DEFAULT 'pending_review',
    s3_bucket            TEXT,
    s3_key               TEXT,
    s3_uploaded_at       TIMESTAMPTZ,
    reviewed_by          TEXT,
    reviewed_at          TIMESTAMPTZ,
    review_notes         TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Indexes:** `content_hash`, `status`, `region`, `brand`, `source_url`, `content_type`, `created_at`, `doc_type`

### ingestion_jobs

Tracks each ingestion request and its progress.

```sql
CREATE TABLE ingestion_jobs (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'in_progress',
    total_nodes_found    INTEGER,
    files_created        INTEGER NOT NULL DEFAULT 0,
    files_auto_approved  INTEGER NOT NULL DEFAULT 0,
    files_pending_review INTEGER NOT NULL DEFAULT 0,
    files_auto_rejected  INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped   INTEGER NOT NULL DEFAULT 0,
    error_message        TEXT,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ
);
```

**Index:** `status`

### revalidation_jobs

Tracks batch revalidation requests.

```sql
CREATE TABLE revalidation_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status          TEXT NOT NULL DEFAULT 'in_progress',
    total_files     INTEGER NOT NULL,
    completed       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    not_found       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
```

**Index:** `status`

---

## File Status Lifecycle

```
                    ┌──────────────────┐
                    │  pending_review   │ ◄── Initial state (after DB insert)
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
        ┌──────────┐  ┌───────────┐  ┌──────────────┐
        │ approved  │  │  pending   │  │ auto_rejected │
        │ (≥ 0.7)  │  │  _review   │  │   (< 0.2)    │
        └─────┬────┘  │ (0.2–0.7) │  └──────────────┘
              │        └─────┬─────┘
              │              │
              ▼         ┌────┴────┐
        ┌──────────┐    │         │
        │  in_s3   │    ▼         ▼
        │ (S3 done)│  approved  rejected
        └──────────┘  (human)   (human)
                        │
                        ▼
                      in_s3
```

**Revalidation** can transition a file from any status back through the score-routing logic.

---

## Pydantic Models

### Internal Models

**ContentNode** — A single content element extracted from AEM JSON:
```python
class ContentNode(BaseModel):
    node_type: str          # :type value (e.g. "avis/components/content/accordionitem")
    aem_node_id: str        # Path in the JSON tree
    html_content: str       # Raw HTML from the node
    parent_context: str     # Parent node path
    metadata: dict          # Additional node metadata
```

**MarkdownFile** — A generated Markdown file with all metadata:
```python
class MarkdownFile(BaseModel):
    filename: str           # Slug-based filename (e.g. "how-to-reset.md")
    title: str
    content_type: str
    source_url: str
    component_type: str
    aem_node_id: str
    md_content: str         # Full markdown with YAML frontmatter
    md_body: str            # Markdown body only (no frontmatter)
    content_hash: str       # SHA-256 of md_body
    modify_date: datetime
    extracted_at: datetime
    parent_context: str
    region: str
    brand: str
```

**ValidationResult** — Output from the Validator Agent:
```python
class ValidationResult(BaseModel):
    score: float            # 0.0 – 1.0 (sum of sub-scores)
    breakdown: ValidationBreakdown
    issues: list[str]       # Human-readable issue descriptions
    doc_type: str           # AI-classified document type
```

**ValidationBreakdown**:
```python
class ValidationBreakdown(BaseModel):
    metadata_completeness: float  # 0.0 – 0.3
    semantic_quality: float       # 0.0 – 0.5
    uniqueness: float             # 0.0 – 0.2
```

### Enums

```python
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
```

### Content Hash Computation

The content hash is a SHA-256 digest of the Markdown body only, excluding YAML frontmatter:

```python
import hashlib

def compute_content_hash(md_body: str) -> str:
    return hashlib.sha256(md_body.encode("utf-8")).hexdigest()
```

Two files with identical bodies but different frontmatter will have the same content hash, enabling deduplication even when metadata changes.

### YAML Frontmatter Structure

Every generated Markdown file includes this frontmatter:

```yaml
---
title: "How to Reset Your Password"
content_type: "faq"
source_url: "https://aem.example.com/content/page.model.json"
component_type: "avis/components/content/accordionitem"
aem_node_id: "/root/container/accordionmodule/accordionitem_123"
modify_date: "2025-08-14T16:57:13+00:00"
extracted_at: "2026-03-05T11:18:45.719041+00:00"
parent_context: "/root/container/accordionmodule"
region: "US"
brand: "Avis"
---

# How to Reset Your Password

Content body here...
```
