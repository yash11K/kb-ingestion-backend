# Bugfix Requirements Document

## Introduction

The AEM KB ingestion pipeline crashes with a `MaxTokensReachedException` from the Strands SDK when processing large AEM JSON payloads. The root cause is that the entire AEM JSON response is passed through the Strands agent's LLM context window — first returned by the `fetch_aem_json` tool and then forwarded as an argument to `filter_by_component_type`. For large AEM pages, this payload exceeds the model's token limit, causing the agent to enter an unrecoverable state and the pipeline job to fail. Additionally, there is insufficient logging around agent inputs and tool invocations, making it difficult to diagnose payload size issues.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the AEM JSON payload returned by `fetch_aem_json` is large enough to exceed the Strands agent's context window token limit THEN the system crashes with `MaxTokensReachedException: Agent has reached an unrecoverable state due to max_tokens limit` and the pipeline job is marked as failed.

1.2 WHEN the `filter_by_component_type` tool is invoked by the Strands agent THEN the entire raw AEM JSON payload (potentially megabytes of data) is passed through the LLM context as a tool argument, consuming tokens unnecessarily since the filtering logic is deterministic and does not require LLM reasoning.

1.3 WHEN the pipeline processes a large AEM endpoint THEN there is no logging of the payload size, token estimate, or tool input sizes being passed to the Strands agent, making it difficult to diagnose token limit failures before they occur.

1.4 WHEN the `fetch_aem_json` tool returns a large JSON payload THEN there is no size check or pre-filtering step to reduce the payload before it enters the agent's context window.

### Expected Behavior (Correct)

2.1 WHEN the AEM JSON payload is large THEN the system SHALL pre-filter the JSON outside the Strands agent's context (i.e., call `filter_by_component_type` directly in Python code before or instead of having the LLM invoke it as a tool) so that only the filtered content nodes enter the agent's context window, preventing `MaxTokensReachedException`.

2.2 WHEN the `filter_by_component_type` logic is executed THEN the system SHALL run it as a direct Python function call rather than as an LLM tool invocation, since the filtering is deterministic and does not benefit from LLM reasoning, thereby avoiding unnecessary token consumption.

2.3 WHEN the pipeline processes an AEM endpoint THEN the system SHALL log the size of the fetched JSON payload (in bytes and estimated tokens), the number of content nodes after filtering, and the size of data being passed to the Strands agent, providing visibility into potential token limit issues.

2.4 WHEN the fetched AEM JSON payload exceeds a configurable size threshold THEN the system SHALL apply the allowlist/denylist filtering before passing data to the Strands agent, ensuring only relevant content nodes are included in the agent's context.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the AEM JSON payload is small enough to be processed without token issues THEN the system SHALL CONTINUE TO extract all matching content nodes, convert HTML to markdown, and generate markdown files with correct YAML frontmatter for each node.

3.2 WHEN content nodes are filtered by allowlist and denylist THEN the system SHALL CONTINUE TO apply the same glob-style matching logic (denylist takes precedence over allowlist) and produce identical `ContentNode` results.

3.3 WHEN the pipeline completes successfully THEN the system SHALL CONTINUE TO update the ingestion job with accurate counters (files_created, files_auto_approved, files_pending_review, files_auto_rejected, duplicates_skipped) and set the job status to completed.

3.4 WHEN a pipeline job fails for any reason THEN the system SHALL CONTINUE TO catch the exception, log the error, and update the job status to failed with the error message.

3.5 WHEN duplicate content is detected via content_hash THEN the system SHALL CONTINUE TO skip the duplicate and increment the duplicates_skipped counter.
