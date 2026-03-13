# AI Agents

The system uses two AI agents built on the AWS Strands Agents SDK with Amazon Bedrock (Claude Sonnet). Each agent has a focused responsibility, dedicated tools, and a tailored system prompt.

## Extractor Agent

**Purpose**: Fetches AEM content, converts HTML to Markdown, and generates structured Markdown files with YAML frontmatter.

**Model**: Amazon Bedrock Claude Sonnet (`us.anthropic.claude-sonnet-4-20250514-v1:0`)

### Tools

| Tool | Type | Description |
|------|------|-------------|
| `html_to_markdown` | Strands `@tool` | Converts HTML to clean Markdown via `markdownify`, strips residual HTML tags |
| `generate_md_file` | Strands `@tool` | Creates Markdown file with YAML frontmatter, computes SHA-256 content hash, generates slug filename |

Note: `fetch_aem_json` and `filter_by_component_type` exist as Strands tools but are called directly in Python (not through the agent) to avoid sending large payloads through the LLM context window.

### Flow

1. The `ExtractorAgent.extract()` method fetches AEM JSON via `httpx` directly
2. Pre-filters content nodes using `filter_by_component_type_direct()` in Python
3. Serializes filtered `ContentNode` objects into the agent prompt
4. The Strands agent processes each node using `html_to_markdown` → `generate_md_file`
5. Returns a list of `MarkdownFile` objects

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

Both agents create a fresh `Agent` instance per invocation to avoid shared conversation history across requests. This ensures each validation/extraction is independent.

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

Both agents use callback handlers that emit SSE events during execution:

- **`tool_call`** events when the agent invokes a tool
- **`agent_log`** events for streaming LLM text chunks
- **`agent_log`** status messages when the agent completes

These events are published through the `StreamManager` and delivered to connected SSE clients in real-time.
