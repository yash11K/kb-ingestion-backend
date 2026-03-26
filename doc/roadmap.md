# Future Roadmap — Toward Agentic Automation

## Current State

Today, the ingestion pipeline accepts a list of AEM `model.json` URLs. Region and brand are auto-inferred from URL patterns. A three-agent architecture handles the downstream processing:

1. **Discovery Agent** (Haiku) — fast content discovery + deep link detection
2. **Extractor Agent** (Sonnet) — Markdown generation with frontmatter
3. **Validator Agent** (Sonnet) — quality scoring + document classification

```json
POST /ingest
{
  "urls": ["https://www.avis.com/en/customer-service/faqs.model.json"],
  "nav_root_url": "https://www.avis.com/en.model.json"
}
// Region and brand auto-inferred from URL
```

## Vision

The system will progressively become more agentic, reducing human input at each phase until the pipeline can autonomously discover, ingest, and maintain AEM content with minimal oversight.

## Phase 1: Explicit Input (Implemented ✓)

- Human provides list of `model.json` URLs
- Optional nav context for source enrichment
- Pipeline handles discovery → extraction → validation → routing → upload
- Human review queue for mid-score content

## Phase 2: Smart Metadata Detection (Implemented ✓)

Region and brand are now auto-detected from URL patterns:
- `/en-us/` → region `nam`, brand inferred from domain
- `/en-gb/` → region `emea`
- Locale-to-region mapping is configurable via `LOCALE_REGION_MAP`
- Namespace inference from URL path segments (e.g. `/customer-service/` → namespace `customer-service`)

Input simplified to just URLs — no manual region/brand required.

## Phase 3: URL Discovery via Deep Links (Partially Implemented)

The Discovery Agent (Haiku) already identifies embedded deep links within AEM content. These are stored in the `deep_links` table and surfaced via the API for user confirmation.

Current capabilities:
- Deep links discovered automatically during ingestion
- User can confirm/dismiss discovered links via API
- Confirmed links trigger individual ingestion jobs
- URL denylist patterns filter out non-content URLs (login, checkout, etc.)

**Remaining work:**
- Sitemap crawling for broader URL discovery
- Automatic relevance scoring for discovered URLs
- Scheduled discovery passes for new content detection

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

