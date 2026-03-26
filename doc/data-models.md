# Data Models

For the full database DDL, table definitions, indexes, migrations, and ORM layer, see [Database & DDL](./database.md).

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

**DiscoveredContent** — A content item identified by the Haiku discovery agent from raw AEM JSON:
```python
class DiscoveredContent(BaseModel):
    path: str                    # JSON tree path (e.g. "/root/container/text")
    component_type: str          # AEM :type value
    title: str                   # inferred title
    content: str                 # cleaned text content (HTML stripped)
    modify_date: str | None      # from dataLayer repo:modifyDate if present
```

**DiscoveryResult** — Output of the Haiku discovery agent:
```python
class DiscoveryResult(BaseModel):
    content_items: list[DiscoveredContent]
    deep_links: list[DeepLink]
```

**ContentNode** — (Deprecated, kept for backward compatibility with tests):
```python
class ContentNode(BaseModel):
    node_type: str          # :type value
    aem_node_id: str        # Path in the JSON tree
    html_content: str       # Raw HTML from the node
    parent_context: str     # Parent node path
    metadata: dict
```

**MarkdownFile** — A generated Markdown file with all metadata:
```python
class MarkdownFile(BaseModel):
    filename: str           # Slug-based filename (e.g. "how-to-reset.md")
    title: str
    content_type: str
    source_url: str
    component_type: str
    key: str                # AEM component key
    namespace: str          # inferred from URL path
    md_content: str         # Full markdown with YAML frontmatter
    md_body: str            # Markdown body only (no frontmatter)
    content_hash: str       # SHA-256 of md_body
    extracted_at: datetime
    parent_context: str
    region: str
    brand: str
```

**ExtractionOutput** — Return value from ExtractorAgent.extract():
```python
class ExtractionOutput(BaseModel):
    files: list[MarkdownFile]
    child_urls: list[str]   # internal AEM URLs discovered during extraction
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

class DeepLinkStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    INGESTED = "ingested"
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
key: "contentcardelement_821372053"
namespace: "customer-service"
extracted_at: "2026-03-05T11:18:45.719041+00:00"
parent_context: "/root/container/accordionmodule"
region: "US"
brand: "Avis"
---

# How to Reset Your Password

Content body here...
```
