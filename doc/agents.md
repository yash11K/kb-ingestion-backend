# AI Agents

The system uses three AI agents built on the AWS Strands Agents SDK with Amazon Bedrock. Each agent has a focused responsibility, dedicated tools, and a tailored system prompt.

## Discovery Agent

**Purpose**: Fast, cheap content discovery from raw AEM JSON. Identifies content items and embedded deep links without full extraction.

**Model**: Amazon Bedrock Claude Haiku (`us.anthropic.claude-3-5-haiku-20241022-v1:0`)

### Output

Returns a `DiscoveryResult` containing:
- `content_items` — list of `DiscoveredContent` (path, component_type, title, cleaned text, modify_date)
- `deep_links` — list of `DeepLink` (url, model_json_url, anchor_text, found_in_node, found_in_page)

### Design Rationale

Haiku is significantly cheaper and faster than Sonnet. By running discovery as a separate pass, the system can quickly identify what content exists on a page and what links it contains, before committing to the more expensive Sonnet extraction. The `HAIKU_MAX_INPUT_TOKENS` setting (default 150K) controls the maximum payload size sent to Haiku.

---

## Extractor Agent

**Purpose**: Transforms discovered content items into structured Markdown files with YAML frontmatter.

**Model**: Amazon Bedrock Claude Sonnet (`us.anthropic.claude-sonnet-4-20250514-v1:0`)

### Tools

| Tool | Type | Description |
|------|------|-------------|
| `generate_md_file` | Strands `@tool` | Creates Markdown file with YAML frontmatter, computes SHA-256 content hash, generates slug filename |

### Flow

1. Receives `DiscoveredContent` items from the Discovery Agent
2. Content is batched if the node count exceeds `BATCH_THRESHOLD` (default 8)
3. The Strands agent processes each item, generating Markdown with frontmatter
4. Returns an `ExtractionOutput` containing `MarkdownFile` objects and any discovered `child_urls`

### System Prompt Summary

The Extractor Agent is instructed to:
- Process ALL content nodes provided in the prompt
- For each node: convert HTML → Markdown, then generate a Markdown file with frontmatter
- Use the `region` and `brand` values from the prompt for every file
- Return results as a JSON array of generated file objects

### Result Parsing

The agent response is parsed using multiple strategies:
1. Check for `tool_results` with file data directly
2. Parse the text response as a JSON array
3. Extract JSON array from within the text (find `[` ... `]`)

## Validator Agent

**Purpose**: Scores Markdown files on metadata completeness, semantic quality, and uniqueness. Also classifies the document type.

**Model**: Amazon Bedrock Claude Sonnet (`us.anthropic.claude-sonnet-4-20250514-v1:0`)

### Tools

| Tool | Type | Description |
|------|------|-------------|
| `parse_frontmatter` | Strands `@tool` (sync) | Parses YAML frontmatter, validates 10 required fields, returns missing fields list |
| `check_duplicate` | Strands `@tool` (async) | Queries `kb_files` table by content_hash, returns `is_duplicate` flag |

### Scoring Breakdown

| Dimension | Range | Criteria |
|-----------|-------|----------|
| `metadata_completeness` | 0.0 – 0.3 | Presence of 10 required frontmatter fields. Each field worth 0.03. |
| `semantic_quality` | 0.0 – 0.5 | LLM evaluates coherence, readability, completeness of the Markdown body |
| `uniqueness` | 0.0 – 0.2 | 0.2 if content hash is unique; 0.0 if duplicate exists |

**Total score** = sum of three sub-scores (0.0 – 1.0)

### Required Frontmatter Fields

1. `title`
2. `content_type`
3. `source_url`
4. `component_type`
5. `aem_node_id`
6. `modify_date`
7. `extracted_at`
8. `parent_context`
9. `region`
10. `brand`

### Document Type Classification

The Validator Agent classifies each file into one of these categories based on content semantics (not AEM component type):

| Category | Description |
|----------|-------------|
| `TnC` | Terms and conditions, legal agreements, policies |
| `FAQ` | Frequently asked questions, Q&A content |
| `ProductGuide` | Product descriptions, feature guides, how-to guides |
| `Support` | Troubleshooting, help articles, support documentation |
| `Marketing` | Promotional content, campaigns, offers |
| `General` | Content that doesn't fit the above categories |

The classified `doc_type` is stored in the database and used in the S3 key path structure.

### System Prompt Summary

The Validator Agent follows these steps:
1. Parse frontmatter using the `parse_frontmatter` tool
2. Score metadata completeness (0.0 – 0.3)
3. Score semantic quality (0.0 – 0.5)
4. Check for duplicates using `check_duplicate` tool
5. Compute total score
6. Collect issues into a list
7. Classify document type from content semantics
8. Return a single JSON object with `score`, `breakdown`, `issues`, and `doc_type`

### Safety Measures

- Sub-scores are clamped to their valid ranges after parsing
- Total score is recomputed as the sum of clamped sub-scores (not trusted from agent output)
- Final score is clamped to [0.0, 1.0]
- If the agent response can't be parsed, a default low score (0.0) is returned with an issue noting the parse failure
- Invalid `doc_type` values fall back to `"General"`

## Agent Lifecycle

All three agents create a fresh `Agent` instance per invocation to avoid shared conversation history across requests. This ensures each discovery/extraction/validation is independent.

```python
# Each call creates a new agent instance
agent = Agent(
    model=BedrockModel(**self._model_kwargs),
    tools=self._tools,
    system_prompt=SYSTEM_PROMPT,
    callback_handler=callback,
)
result = await agent.invoke_async(prompt)
```

## SSE Callback Integration

All three agents use callback handlers that emit SSE events during execution:

- **`tool_call`** events when the agent invokes a tool
- **`agent_log`** events for streaming LLM text chunks
- **`agent_log`** status messages when the agent completes

These events are published through the `StreamManager` and delivered to connected SSE clients in real-time.
