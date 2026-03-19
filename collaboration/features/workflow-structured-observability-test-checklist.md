# Workflow Structured Observability Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature:
  - [workflow-structured-observability.md](/E:/work/deer-flow/collaboration/features/workflow-structured-observability.md)
- Frontend impact expectation: `none required`

## 0. Test Scope Guardrails

- [ ] Validate observability instrumentation without requiring frontend code changes
- [ ] Validate that existing workflow behavior is unaffected by instrumentation
- [ ] Validate that system works identically with and without OpenTelemetry packages installed
- [ ] Validate that no sensitive data leaks into decision logs
- Done when:
  - all observability features are proven non-invasive to existing functionality

---

## 1. Tracer Facade Unit Tests

- [ ] Add test file:
  - `backend/tests/test_observability_tracer.py`
- [ ] Test: `span()` context manager works without OTel installed
  - enter span, do work, exit
  - assert no exception raised
  - assert `SpanHandle.elapsed_ms` returns positive number
- [ ] Test: `span()` records error on exception
  - enter span, raise exception
  - assert exception propagates (not swallowed)
  - assert `SpanHandle.elapsed_ms` is still accessible in finally block
- [ ] Test: `span()` with attributes
  - pass `attributes={"run_id": "test_run", "task_id": "task-001"}`
  - assert no exception (noop mode should silently accept any attributes)
- [ ] Test: `SpanHandle.set_attribute()` accepts various types
  - string, int, float, bool values
  - assert no exception in noop mode
- [ ] Test: `_sanitize_attributes()` truncates long strings
  - pass string > 500 chars
  - assert output is truncated with `...(+N)` suffix
- [ ] Test: `_sanitize_attributes()` converts non-primitive values to string
  - pass dict, list values
  - assert they become string representations
- Done when:
  - tracer facade has full noop-mode coverage
  - no test depends on OTel packages being installed

## 2. Metrics Facade Unit Tests

- [ ] Add test file:
  - `backend/tests/test_observability_metrics.py`
- [ ] Test: `WorkflowMetrics.get()` returns singleton
  - call `.get()` twice
  - assert same instance returned
- [ ] Test: `record_workflow_start()` increments counter in fallback mode
  - record one event
  - check `snapshot()` shows count 1
- [ ] Test: `record_workflow_end()` records histogram in fallback mode
  - record with duration_ms=1500
  - check `snapshot()` histograms contains the value
- [ ] Test: `record_task()` with various statuses
  - record DONE, FAILED tasks
  - verify counters increment correctly
  - verify FAILED also increments `task.failure.total`
- [ ] Test: `record_llm_call()` records duration and token count
  - record with input_tokens=100, output_tokens=200
  - verify both duration histogram and token counter update
- [ ] Test: `record_mcp_call()` with success=true and success=false
  - verify success label is set correctly in counter key
- [ ] Test: `record_intervention()` and `record_helper_retry()`
  - verify counters increment
- [ ] Test: `snapshot()` returns correct statistical aggregations
  - record multiple values: [100, 200, 300, 400, 500]
  - verify count=5, avg=300, max=500
  - verify p95 is approximately correct
- [ ] Test: `snapshot()` returns empty result when no data recorded
  - assert `{"counters": {}, "histograms": {}}` on fresh instance
- Done when:
  - all metric types and recording methods have coverage in fallback mode
  - snapshot output format is validated

## 3. Decision Log Unit Tests

- [ ] Add test file:
  - `backend/tests/test_observability_decision_log.py`
- [ ] Test: `record_decision()` produces valid JSON
  - call with all parameters filled
  - capture log output
  - parse as JSON
  - assert all fields present
- [ ] Test: `record_decision()` strips None values
  - call with only `decision_type` and `run_id` (other fields None)
  - parse JSON output
  - assert no key with None value exists
- [ ] Test: `record_decision()` includes timestamp
  - call `record_decision()`
  - parse output
  - assert `ts` field exists and is a valid Unix timestamp
- [ ] Test: all `DecisionType` values are valid
  - iterate over DecisionType literal values
  - call `record_decision(decision_type=dt)` for each
  - assert no exception
- [ ] Test: `_truncate_dict()` truncates long strings
  - input dict with string value > 500 chars
  - assert output string is 500 chars + suffix
- [ ] Test: `_truncate_dict()` handles nested dicts
  - input `{"outer": {"inner": "long_string..."}}`
  - assert inner string is also truncated
- [ ] Test: `record_decision()` does NOT include sensitive patterns
  - call with `inputs={"password": "secret123", "api_key": "sk-xxx"}`
  - verify values appear in output (truncation, not filtering — filtering is caller's responsibility)
  - NOTE: this test documents behavior; actual sensitive data exclusion is done at call sites
- [ ] Test: `record_decision()` handles duration_ms rounding
  - call with `duration_ms=12.3456`
  - assert output has `"duration_ms": 12.35` (2 decimal places)
- Done when:
  - decision log output format is fully validated
  - JSON parsing of output never fails

## 4. Setup Module Tests

- [ ] Add test file:
  - `backend/tests/test_observability_setup.py`
- [ ] Test: `init_observability()` succeeds without OTel packages
  - call `init_observability()`
  - assert no exception
  - assert log message contains "Initialization complete"
- [ ] Test: `_setup_decision_logger()` configures independent logger
  - call setup
  - verify `deer-flow.decisions` logger has `propagate = False`
  - verify logger has at least one handler
  - verify handler formatter is `"%(message)s"`
- [ ] Test: `_setup_decision_logger()` with file output
  - set `DEER_FLOW_DECISION_LOG_FILE` to a temp file path
  - call setup
  - record a decision
  - verify file contains JSON line
  - clean up temp file
- [ ] Test: `_setup_otel_if_enabled()` with OTEL_ENABLED=false
  - set `OTEL_ENABLED=false`
  - call setup
  - verify log message contains "disabled"
  - verify no TracerProvider set
- [ ] Test: `_setup_otel_if_enabled()` with OTEL_ENABLED=true but no packages
  - set `OTEL_ENABLED=true`
  - mock importlib to raise ImportError
  - call setup
  - assert no exception (graceful degradation)
  - assert warning log message
- Done when:
  - setup module handles all combinations of config and dependency availability

## 5. Node Wrapper Tests

- [ ] Add test file:
  - `backend/tests/test_observability_node_wrapper.py`
- [ ] Test: `traced_node()` preserves original function behavior
  - create mock async node function that returns `{"execution_state": "DONE"}`
  - wrap with `traced_node("test_node")`
  - call wrapped function with mock state
  - assert return value is identical to original
- [ ] Test: `traced_node()` does not swallow exceptions
  - create mock async node function that raises `ValueError`
  - wrap with `traced_node("test_node")`
  - call wrapped function
  - assert `ValueError` propagates
- [ ] Test: `traced_node()` extracts correct attributes from state
  - provide state with `run_id`, `task_pool` (with one RUNNING task), `route_count`
  - call wrapped function
  - verify span was created with correct attributes (in noop mode, just verify no crash)
- [ ] Test: `traced_node()` handles empty state gracefully
  - provide state with no `run_id`, empty `task_pool`
  - assert no exception
- Done when:
  - node wrapper is proven transparent — it only observes, never modifies

## 6. LLM Callback Handler Tests

- [ ] Add test file:
  - `backend/tests/test_observability_llm_callback.py`
- [ ] Test: `on_llm_start` + `on_llm_end` records duration
  - call `on_llm_start` with mock serialized and run_id
  - simulate time passage
  - call `on_llm_end` with mock response
  - verify `WorkflowMetrics.get().snapshot()` shows llm.call.duration_ms entry
- [ ] Test: `on_llm_end` extracts tokens from OpenAI format
  - mock response with `llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 200}}`
  - call handler
  - verify token counter recorded 300
- [ ] Test: `on_llm_end` extracts tokens from alternative format
  - mock response with `generations[0][0].generation_info={"usage": {"input_tokens": 50, "output_tokens": 75}}`
  - call handler
  - verify token counter recorded 125
- [ ] Test: `on_llm_end` handles missing token data gracefully
  - mock response with no usage data
  - call handler
  - assert no exception
  - verify duration is still recorded (tokens = 0)
- [ ] Test: `on_llm_error` cleans up start time
  - call `on_llm_start`
  - call `on_llm_error`
  - verify no memory leak in `_call_starts` dict
- [ ] Test: handler does not raise on unexpected response structure
  - pass various malformed response objects
  - assert no exception from any handler method
- Done when:
  - LLM callback handles all known response formats
  - handler never causes LLM invocation failures

## 7. Integration Test — Decision Log End-to-End

- [ ] Add test file or extend:
  - `backend/tests/test_observability_integration.py`
- [ ] Test: full workflow produces decision log entries
  - run a minimal workflow (mock LLM, mock agents)
  - capture all `deer-flow.decisions` logger output
  - parse each line as JSON
  - assert at least the following decision types appear:
    - `orchestration_mode` (1 entry)
    - `task_decomposition` (at least 1 entry)
    - `agent_route` (at least 1 entry per task)
    - `outcome_classification` (at least 1 entry per task)
  - assert all entries have valid `run_id`
- [ ] Test: decision log entries for same run_id form coherent sequence
  - filter entries by `run_id`
  - verify chronological order (ts is monotonically increasing)
  - verify task_ids referenced in `agent_route` match those from `task_decomposition`
- [ ] Test: decision log does not contain entries from module loggers
  - capture `deer-flow.decisions` logger output
  - assert no line starts with `YYYY-MM-DD` (module log format)
  - assert every line is valid JSON
- Done when:
  - a complete workflow produces a queryable decision audit trail

## 8. Integration Test — Metrics End-to-End

- [ ] Test: full workflow produces expected metrics
  - run a minimal workflow (mock LLM, mock agents)
  - call `WorkflowMetrics.get().snapshot()`
  - assert `workflow.total` counter > 0
  - assert `task.total` counter > 0
  - assert `llm.call.total` counter > 0
  - assert `llm.call.duration_ms` histogram has entries
- [ ] Test: metrics accumulate across multiple workflows
  - run two workflows
  - verify counters show cumulative counts
- [ ] Test: `/debug/metrics` endpoint returns valid JSON
  - start test server
  - GET `/debug/metrics`
  - assert HTTP 200
  - parse response as JSON
  - assert `counters` and `histograms` keys exist
- Done when:
  - metrics correctness is validated in realistic workflow scenarios

## 9. Non-Regression Tests

### 9.1 Existing Workflow Behavior

- [ ] Re-run all existing tests in:
  - `backend/tests/test_multi_agent_core.py`
  - `backend/tests/test_multi_agent_graph.py`
  - `backend/tests/test_executor_intervention_normalization.py`
  - `backend/tests/test_help_request_builder.py`
- [ ] Assert ALL existing tests pass without modification
- Done when:
  - observability instrumentation has zero impact on existing test suite

### 9.2 SSE Event Compatibility

- [ ] Verify SSE event types are unchanged:
  - `task_started`, `task_running`, `task_resumed`
  - `task_completed`, `task_failed`
  - `task_waiting_dependency`, `task_help_requested`, `task_waiting_intervention`
  - `workflow_stage_changed`
  - `orchestration_mode_resolved`
- [ ] Verify event payloads contain no new required fields
- [ ] Verify event payloads contain no renamed fields
- Done when:
  - frontend can consume events without code changes

### 9.3 API Compatibility

- [ ] Verify intervention resolve endpoint unchanged:
  - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
  - request body: `fingerprint`, `action_key`, `payload`
  - response body: `ok`, `thread_id`, `request_id`, `fingerprint`, `accepted`, `resume_action`, `resume_payload`, `checkpoint`
- [ ] Verify no new required request fields
- [ ] Verify no removed response fields
- Done when:
  - existing frontend intervention flow works without changes

### 9.4 LangSmith Compatibility

- [ ] If LangSmith is configured (`LANGSMITH_TRACING=true`):
  - verify LangSmith tracer callback still registers
  - verify both LangSmith and ObservabilityCallbackHandler coexist in callbacks list
  - verify LLM calls produce traces in both systems
- [ ] If LangSmith is NOT configured:
  - verify only ObservabilityCallbackHandler is active
  - verify no LangSmith-related errors
- Done when:
  - dual-tracing mode works correctly

## 10. Performance Non-Regression

- [ ] Measure workflow execution time with and without observability instrumentation:
  - run same workflow 5 times without instrumentation (baseline)
  - run same workflow 5 times with instrumentation
  - assert overhead < 5% of total execution time
- [ ] Measure memory footprint of in-memory metrics:
  - simulate 100 workflows worth of metrics
  - verify memory usage is bounded (histograms don't grow unbounded)
- [ ] Note: this is a qualitative check, not a strict benchmark
- Done when:
  - instrumentation overhead is negligible

## 11. Security Validation

- [ ] Verify decision log does NOT contain:
  - full MCP tool arguments (only tool name and arg keys)
  - user passwords or credentials
  - API keys or tokens
  - PII beyond what's in task descriptions
- [ ] Verify `/debug/metrics` endpoint does NOT expose:
  - individual user data
  - full request/response content
- [ ] Recommend: `/debug/metrics` should be restricted in production deployments
- Done when:
  - decision log and metrics are safe for centralized log aggregation

## 12. Graceful Degradation Tests

- [ ] Test: uninstall `opentelemetry-*` packages, run full test suite
  - all tests must pass
  - no ImportError at module load time
  - decision log still works
  - in-memory metrics still work
  - `/debug/metrics` endpoint still works
- [ ] Test: set `OTEL_ENABLED=true` without OTel packages
  - verify warning log, not error
  - verify system continues running
- [ ] Test: set `DEER_FLOW_DECISION_LOG_FILE` to unwritable path
  - verify error is logged
  - verify system continues running (fallback to stdout or skip)
- Done when:
  - system is resilient to all configuration and dependency combinations

## 13. Manual Validation Scenarios

### Scenario A: Decision Log Inspection

- [ ] Run a real workflow (e.g., "帮我查一下张三的联系方式")
- [ ] Capture decision log output
- [ ] Verify you can answer these questions from the log alone:
  - Which orchestration mode was chosen? Why?
  - How many tasks were created? With what descriptions?
  - Which agent was assigned to each task? Was it fast-path or LLM-routed?
  - Did any task fail? Was there a retry?
  - What was the final outcome classification for each task?
- Done when:
  - decision log is sufficient for post-mortem analysis without reading code

### Scenario B: Metrics Dashboard Check

- [ ] Run 3 different workflows
- [ ] Call `GET /debug/metrics`
- [ ] Verify the response shows:
  - workflow count = 3
  - task count matches actual task count
  - LLM call durations are reasonable (not 0, not astronomical)
  - MCP call durations are present if MCP tools were used
- Done when:
  - metrics reflect actual system behavior

### Scenario C: No-OTel Smoke Test

- [ ] Remove `opentelemetry-*` from installed packages
- [ ] Start the backend
- [ ] Run a workflow
- [ ] Verify:
  - no error in startup logs
  - workflow completes successfully
  - decision log entries appear
  - `/debug/metrics` returns data
  - SSE events stream to frontend normally
- Done when:
  - production deployment without OTel is safe

## 14. Test File Summary

| Test File | Coverage Area | Dependencies |
|-----------|---------------|-------------|
| `test_observability_tracer.py` | Tracer facade noop mode | None (no OTel) |
| `test_observability_metrics.py` | Metrics facade fallback mode | None (no OTel) |
| `test_observability_decision_log.py` | Decision log format and content | None |
| `test_observability_setup.py` | Init and configuration | None |
| `test_observability_node_wrapper.py` | Node decorator transparency | None |
| `test_observability_llm_callback.py` | LLM callback token/duration extraction | None |
| `test_observability_integration.py` | End-to-end decision log + metrics | Existing test infra |

## 15. Release Readiness

- [ ] All new unit tests pass
- [ ] All existing workflow tests pass unchanged
- [ ] Manual scenario A (decision log inspection) verified
- [ ] Manual scenario B (metrics dashboard check) verified
- [ ] Manual scenario C (no-OTel smoke test) verified
- [ ] No mandatory frontend changes required
- [ ] No API contract changes
- [ ] Decision log contains no sensitive data
- [ ] Remaining gaps are written back to the feature doc if deferred
