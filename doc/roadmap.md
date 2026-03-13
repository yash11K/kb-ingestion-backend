# Future Roadmap — Toward Agentic Automation

## Current State

Today, the ingestion pipeline requires explicit human input for each job:

```json
POST /ingest
{
  "url": "https://aem-instance/content/page.model.json",
  "region": "US",
  "brand": "BrandName"
}
```

The caller must know the exact AEM URL, the region, and the brand. The system then handles everything downstream (extraction, validation, routing, upload) autonomously. This is a solid foundation, but the input side is still manual.

## Vision

The system will progressively become more agentic, reducing human input at each phase until the pipeline can autonomously discover, ingest, and maintain AEM content with minimal oversight.

## Phase 1: Current — Explicit Input (Implemented)

- Human provides `url`, `region`, `brand`
- Optional `component_types` override for per-job allowlist customization
- Pipeline handles extraction → validation → routing → upload
- Human review queue for mid-score content

## Phase 2: Smart Metadata Detection

**Goal**: Reduce required input to just the URL.

The agent would:
- Auto-detect `region` from URL patterns (e.g., `/en-us/` → US, `/en-gb/` → UK) or from AEM page metadata
- Auto-detect `brand` from the domain or AEM content structure (e.g., `avis.com` → Avis, `budget.com` → Budget)
- Infer `content_type` from the page structure and component types found
- Fall back to asking the user only when detection confidence is low

**Input simplification:**
```json
POST /ingest
{
  "url": "https://www.avis.com/en/customer-service/faqs/usa/car-assignment.model.json"
}
// Agent infers: region=US, brand=Avis
```

## Phase 3: URL Discovery via Sitemap Crawling

**Goal**: The agent discovers ingestible URLs automatically.

The agent would:
- Accept a root domain or sitemap URL instead of individual page URLs
- Crawl AEM sitemaps to discover all `model.json` endpoints
- Filter URLs by relevance (FAQ pages, support pages, product pages)
- Queue discovered URLs for batch ingestion
- Track which URLs have been ingested and detect new/changed pages

**Input simplification:**
```json
POST /ingest
{
  "domain": "www.avis.com",
  "scope": "customer-service"
}
// Agent discovers and ingests all relevant pages
```

## Phase 4: Change Detection and Proactive Re-Ingestion

**Goal**: The system monitors AEM content and reacts to changes.

The agent would:
- Periodically poll known AEM endpoints for content changes (using `modify_date` or ETags)
- Detect new pages added to sitemaps
- Automatically trigger re-ingestion for changed content
- Use content hash comparison to determine if re-processing is needed
- Notify operators of significant content changes via webhooks or alerts

**No manual input needed** — the system maintains the knowledge base proactively.

## Phase 5: Multi-Source Ingestion

**Goal**: Extend beyond AEM to other content sources.

The agent would:
- Support additional CMS platforms (WordPress, Contentful, etc.)
- Ingest from static documentation sites
- Process PDF documents and convert to Markdown
- Handle API documentation (OpenAPI specs)
- Normalize all content into the same Markdown + frontmatter format

## Supporting Enhancements

These improvements support the agentic evolution:

### Smarter Validation
- Learn from human review decisions to improve auto-approve/reject accuracy
- Detect content drift (same URL, significantly different content)
- Cross-reference related content for consistency checking

### Improved Component Filtering
- Agent learns which component types produce valuable content vs. noise
- Dynamic allowlist/denylist adjustment based on extraction success rates
- Per-brand and per-region filtering profiles

### Workflow Automation
- Auto-retry failed validations with exponential backoff
- Scheduled batch revalidation for aging content
- Webhook notifications for pipeline events (completion, failures, review queue growth)

### Observability
- Pipeline execution metrics (duration, token usage, success rates)
- Content quality trends over time
- Agent performance dashboards
