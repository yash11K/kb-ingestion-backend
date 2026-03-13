# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Large AEM JSON Payload Causes MaxTokensReachedException
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete failing cases: construct AEM JSON payloads large enough to exceed the agent's context window token limit (e.g., 2 MB+ with 400+ nodes) and invoke `ExtractorAgent.extract()`
  - Test that when `isBugCondition(input)` is true (payload exceeds token limit AND `filter_by_component_type` is invoked as LLM tool AND agent receives full unfiltered JSON), the system raises `MaxTokensReachedException`
  - Mock the Strands `Agent` to simulate token limit behavior when receiving large tool arguments
  - Assertions should match Expected Behavior: no `MaxTokensReachedException` raised, `filter_by_component_type` called as direct Python function, agent context contains only filtered `ContentNode` data
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists because the current code passes full JSON through the agent context)
  - Document counterexamples found (e.g., "ExtractorAgent.extract() with 2 MB JSON raises MaxTokensReachedException because filter_by_component_type is an LLM tool receiving full payload")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - filter_by_component_type Produces Identical ContentNode Results
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: Run `filter_by_component_type` on UNFIXED code with various AEM JSON structures, allowlist, and denylist combinations and record actual `ContentNode` outputs
  - Observe: Verify denylist takes precedence over allowlist for overlapping patterns on unfixed code
  - Observe: Verify small payloads produce correct `ContentNode` results with proper glob-style matching
  - Write property-based tests: for all valid AEM JSON inputs with any allowlist/denylist combination where `NOT isBugCondition(input)`, the direct call to `filter_by_component_type` core logic produces the same `ContentNode` list as the original `@tool`-decorated function
  - Generate random AEM JSON trees with random `:type` values and verify filtering output matches
  - Generate random allowlist/denylist pattern combinations and verify denylist precedence is preserved
  - Verify tests PASS on UNFIXED code (confirms baseline behavior to preserve)
  - _Requirements: 3.1, 3.2_

- [x] 3. Fix MaxTokensReachedException by pre-filtering AEM JSON outside agent context

  - [x] 3.1 Extract core filtering logic into a plain function
    - In `src/tools/filter_components.py`, create `filter_by_component_type_direct(model_json, allowlist, denylist) -> list[ContentNode]` containing the traversal and filtering logic without the `@tool` decorator
    - The existing `@tool`-decorated `filter_by_component_type` should delegate to `filter_by_component_type_direct` for backward compatibility
    - _Bug_Condition: isBugCondition(input) where estimated_tokens > AGENT_CONTEXT_TOKEN_LIMIT AND filter_by_component_type is invoked as LLM tool_
    - _Expected_Behavior: filter_by_component_type runs as direct Python call, not LLM tool invocation_
    - _Preservation: filter_by_component_type_direct produces identical ContentNode results as original tool_
    - _Requirements: 2.2, 3.2_

  - [x] 3.2 Add configurable max_payload_bytes setting
    - In `src/config.py`, add `max_payload_bytes: int = 500_000` to the `Settings` class
    - _Requirements: 2.4_

  - [x] 3.3 Pre-filter in ExtractorAgent.extract() and update agent setup
    - In `src/agents/extractor.py`, remove `filter_by_component_type` from the `tools=` list in the `Agent()` constructor
    - In `ExtractorAgent.extract()`, call `fetch_aem_json` directly as a plain Python HTTP call to get the raw JSON
    - Call `filter_by_component_type_direct()` to get filtered `ContentNode` objects before passing to the agent
    - Pass only the filtered content nodes to the agent prompt
    - _Bug_Condition: isBugCondition(input) where agent receives full unfiltered aem_json in context window_
    - _Expected_Behavior: agent context contains only filtered ContentNode data, preventing MaxTokensReachedException_
    - _Preservation: small payloads continue to produce identical MarkdownFile results_
    - _Requirements: 2.1, 2.2, 2.4, 3.1_

  - [x] 3.4 Update EXTRACTOR_SYSTEM_PROMPT
    - Remove step 1 (fetch) and step 2 (filter) from the system prompt
    - Agent now receives pre-filtered content nodes and only needs to convert HTML to markdown and generate markdown files
    - _Requirements: 2.1, 2.2_

  - [x] 3.5 Add payload size logging
    - Log raw JSON size in bytes and estimated token count (`payload_bytes / 4`)
    - Log number of content nodes after filtering and filtered payload size
    - Log warning when raw JSON exceeds `max_payload_bytes` threshold from Settings
    - _Requirements: 2.3, 2.4_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Large AEM JSON Payload Causes MaxTokensReachedException
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (no crash, direct Python call, filtered data in agent context)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** - filter_by_component_type Produces Identical ContentNode Results
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
