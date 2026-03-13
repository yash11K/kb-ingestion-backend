# MaxTokensReachedException Pipeline Fix — Bugfix Design

## Overview

The AEM KB ingestion pipeline crashes with `MaxTokensReachedException` when processing large AEM JSON payloads. The entire JSON response flows through the Strands agent's LLM context window: first returned by `fetch_aem_json`, then forwarded as an argument to `filter_by_component_type`. For large AEM pages this exceeds the model's token limit. The fix moves `filter_by_component_type` from an LLM tool invocation to a direct Python function call executed before the agent, adds payload size logging, and introduces a configurable size threshold check.

## Glossary

- **Bug_Condition (C)**: The AEM JSON payload returned by `fetch_aem_json` is large enough that passing it through the Strands agent's context window (as a tool argument to `filter_by_component_type`) causes `MaxTokensReachedException`.
- **Property (P)**: The system pre-filters the JSON via a direct Python call to `filter_by_component_type`, passes only filtered `ContentNode` data to the agent, and logs payload sizes throughout.
- **Preservation**: Existing extraction behavior for small payloads, allowlist/denylist filtering logic, pipeline job counters, error handling, and duplicate detection must remain unchanged.
- **ExtractorAgent**: The class in `src/agents/extractor.py` that wraps a Strands `Agent` with extraction tools and orchestrates the fetch → filter → convert → generate flow.
- **filter_by_component_type**: The `@tool`-decorated function in `src/tools/filter_components.py` that recursively traverses AEM JSON and filters nodes by component type using allowlist/denylist glob patterns.
- **fetch_aem_json**: The `@tool`-decorated function in `src/tools/fetch_aem.py` that fetches and parses JSON from an AEM model.json endpoint.
- **PipelineService**: The orchestrator in `src/services/pipeline.py` that coordinates the full ingestion pipeline.

## Bug Details

### Fault Condition

The bug manifests when the AEM JSON payload returned by `fetch_aem_json` is large enough to exceed the Strands agent's context window token limit. The `ExtractorAgent` currently registers `filter_by_component_type` as an LLM tool, so the agent passes the entire raw JSON (potentially megabytes) as a tool argument through its context window. This consumes tokens for a deterministic operation that requires no LLM reasoning.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { url: string, aem_json: dict, allowlist: list[str], denylist: list[str] }
  OUTPUT: boolean

  payload_bytes := len(json.dumps(aem_json))
  estimated_tokens := payload_bytes / 4  -- rough byte-to-token ratio

  RETURN estimated_tokens > AGENT_CONTEXT_TOKEN_LIMIT
         AND filter_by_component_type is invoked as LLM tool (not direct call)
         AND agent receives full unfiltered aem_json in context window
END FUNCTION
```

### Examples

- **Large corporate page**: AEM endpoint returns 2.5 MB JSON with 400+ nodes. Agent attempts to pass full JSON to `filter_by_component_type` tool → `MaxTokensReachedException` after ~625K estimated tokens exceed the model limit. Expected: pre-filter to ~15 matching nodes before agent sees data.
- **Medium page near threshold**: AEM endpoint returns 800 KB JSON. Depending on model limits, this may intermittently fail. Expected: pre-filter reduces payload to only matching content nodes.
- **Small page (no bug)**: AEM endpoint returns 50 KB JSON with 20 nodes. Agent processes successfully. Expected: continues to work identically after fix.
- **Edge case — empty JSON**: AEM endpoint returns `{}` with no `:items`. Expected: no content nodes extracted, no crash, pipeline completes with zero files.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Small AEM payloads that fit within the agent's context window must continue to produce identical `MarkdownFile` results with correct YAML frontmatter.
- The `filter_by_component_type` function's allowlist/denylist glob-style matching logic (denylist takes precedence) must produce identical `ContentNode` results.
- Pipeline job counters (`files_created`, `files_auto_approved`, `files_pending_review`, `files_auto_rejected`, `duplicates_skipped`) must remain accurate.
- Pipeline error handling must continue to catch exceptions, log errors, and update job status to `failed`.
- Duplicate detection via `content_hash` must continue to skip duplicates and increment the counter.

**Scope:**
All inputs that do NOT involve large AEM JSON payloads exceeding the token limit should be completely unaffected by this fix. This includes:
- Small AEM endpoints that process successfully today
- All validation, routing, S3 upload, and duplicate detection logic
- API request/response handling
- Database operations

## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is architectural:

1. **filter_by_component_type registered as LLM tool**: In `ExtractorAgent.__init__`, `filter_by_component_type` is passed in the `tools=` list to the Strands `Agent`. This means the LLM must receive the full AEM JSON in its context to formulate the tool call arguments, and the tool response (filtered nodes) also flows back through the context. For large payloads, this double-pass of data through the context window exceeds token limits.

2. **No pre-filtering step**: The `ExtractorAgent.extract()` method delegates everything to the agent prompt. There is no Python-level step to fetch the JSON and filter it before the agent sees it. The fetch and filter are both LLM-mediated, which is unnecessary since filtering is deterministic.

3. **No payload size awareness**: Neither `ExtractorAgent` nor `PipelineService` checks the size of the fetched JSON before passing it to the agent. There are no logs for payload size, token estimates, or tool input sizes.

4. **System prompt instructs LLM to use filter tool**: The `EXTRACTOR_SYSTEM_PROMPT` explicitly tells the agent to "Use the filter_by_component_type tool" in step 2, guaranteeing the full JSON flows through the context.

## Correctness Properties

Property 1: Fault Condition — Pre-filtered data prevents MaxTokensReachedException

_For any_ AEM JSON payload where `isBugCondition` returns true (payload large enough to exceed token limits when passed unfiltered through the agent context), the fixed `ExtractorAgent` SHALL call `filter_by_component_type` as a direct Python function before invoking the Strands agent, passing only the filtered `ContentNode` data into the agent's context, thereby preventing `MaxTokensReachedException`.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 2: Preservation — Filtering produces identical ContentNode results

_For any_ AEM JSON input with any combination of allowlist and denylist patterns, the direct Python call to `filter_by_component_type` (bypassing the `@tool` decorator) SHALL produce the same `ContentNode` list as the original tool invocation, preserving the glob-style matching logic where denylist takes precedence over allowlist.

**Validates: Requirements 3.1, 3.2**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `src/tools/filter_components.py`

**Function**: `filter_by_component_type`

**Specific Changes**:
1. **Extract core logic into a plain function**: Create a new function `filter_by_component_type_direct(model_json, allowlist, denylist) -> list[ContentNode]` that contains the traversal and filtering logic without the `@tool` decorator. The existing `@tool`-decorated function can delegate to this for backward compatibility.

**File**: `src/agents/extractor.py`

**Class**: `ExtractorAgent`

**Specific Changes**:
2. **Remove filter_by_component_type from agent tools**: Remove `filter_by_component_type` from the `tools=` list in `Agent()` constructor. The agent no longer needs this tool since filtering happens before it.

3. **Pre-filter in extract() method**: In `ExtractorAgent.extract()`, call `fetch_aem_json` directly (as a plain Python HTTP call, not via the agent) to get the raw JSON, then call `filter_by_component_type_direct()` to get filtered `ContentNode` objects. Pass only the filtered nodes to the agent prompt.

4. **Update system prompt**: Modify `EXTRACTOR_SYSTEM_PROMPT` to remove step 1 (fetch) and step 2 (filter). The agent now receives pre-filtered content nodes and only needs to convert HTML to markdown and generate markdown files.

5. **Add payload size logging**: Log the raw JSON size (bytes), estimated token count, number of nodes after filtering, and filtered payload size before passing data to the agent.

6. **Add configurable size threshold**: Read a `max_payload_bytes` setting from `Settings`. If the raw JSON exceeds this threshold, log a warning. The pre-filtering always runs regardless of size (it's cheap), but the threshold provides observability.

**File**: `src/config.py`

**Class**: `Settings`

**Specific Changes**:
7. **Add max_payload_bytes setting**: Add `max_payload_bytes: int = 500_000` (default 500 KB) to the `Settings` class for the configurable size threshold.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that construct large AEM JSON payloads and pass them through the `ExtractorAgent` to observe `MaxTokensReachedException`. Mock the Strands `Agent` to simulate token limit behavior.

**Test Cases**:
1. **Large Payload Test**: Construct a 2 MB AEM JSON with 400+ nodes and invoke `ExtractorAgent.extract()` — observe `MaxTokensReachedException` on unfixed code.
2. **Threshold Boundary Test**: Construct a payload just above the token limit — observe intermittent failures on unfixed code.
3. **Filter Tool Argument Size Test**: Instrument the `filter_by_component_type` tool to log its `model_json` argument size — observe that the full unfiltered JSON is passed through the agent context.

**Expected Counterexamples**:
- `MaxTokensReachedException` raised when agent attempts to pass large JSON as tool argument
- Possible causes: full JSON in context window, no pre-filtering, deterministic operation mediated by LLM

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := ExtractorAgent_fixed.extract(input.url, input.region, input.brand)
  ASSERT no MaxTokensReachedException raised
  ASSERT filter_by_component_type called as direct Python function
  ASSERT agent context contains only filtered ContentNode data
  ASSERT result contains expected MarkdownFile objects
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT filter_by_component_type_direct(input.json, input.allowlist, input.denylist)
         == filter_by_component_type_original(input.json, input.allowlist, input.denylist)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many random AEM JSON structures with varying node types, depths, and allowlist/denylist patterns
- It catches edge cases in glob matching that manual unit tests might miss
- It provides strong guarantees that filtering behavior is unchanged across the input domain

**Test Plan**: Observe behavior on UNFIXED code first for the `filter_by_component_type` function with various inputs, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Filter Equivalence Preservation**: Verify that `filter_by_component_type_direct()` produces identical `ContentNode` lists as the original `@tool`-decorated function for any valid AEM JSON, allowlist, and denylist combination.
2. **Small Payload Preservation**: Verify that small AEM payloads produce identical `MarkdownFile` results through the fixed pipeline as through the original pipeline.
3. **Denylist Precedence Preservation**: Verify that denylist continues to take precedence over allowlist for any node type and pattern combination.

### Unit Tests

- Test `filter_by_component_type_direct()` with various AEM JSON structures (nested, flat, empty)
- Test payload size logging emits correct values for known input sizes
- Test configurable threshold check triggers warning log at correct boundary
- Test updated `ExtractorAgent` calls filter directly instead of via agent tool

### Property-Based Tests

- Generate random AEM JSON trees with random `:type` values and verify `filter_by_component_type_direct()` matches original tool output
- Generate random allowlist/denylist pattern combinations and verify denylist precedence is preserved
- Generate random payload sizes and verify logging outputs correct byte counts and token estimates

### Integration Tests

- Test full pipeline flow with a large mocked AEM endpoint to verify no `MaxTokensReachedException`
- Test full pipeline flow with a small mocked AEM endpoint to verify identical output to original
- Test pipeline error handling still catches and logs failures correctly after the refactor
