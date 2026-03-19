# Workflow Structured Observability Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature:
  - [workflow-structured-observability.md](/E:/work/deer-flow/collaboration/features/workflow-structured-observability.md)
- Frontend impact target: `none required`

## 0. Implementation Guardrails

- [ ] Do not change existing SSE event types or payload structures
- [ ] Do not change `task_pool` field definitions in `thread_state.py`
- [ ] Do not change intervention resolve API request/response format
- [ ] Do not require any frontend code changes
- [ ] Do not change existing module logger behavior — only add new independent loggers
- [ ] OpenTelemetry must be optional dependency — system must run normally without it
- Done when:
  - backend can be deployed without coordinating a frontend release
  - system runs identically when `opentelemetry` packages are not installed

---

## Phase 1: Observability Infrastructure + Decision Log

### 1. Create Observability Package Skeleton

- [ ] Create directory:
  - `backend/src/observability/`
- [ ] Create file:
  - `backend/src/observability/__init__.py`
- [ ] Export public API:
  - `span`, `get_tracer`, `SpanHandle`
  - `WorkflowMetrics`
  - `record_decision`, `DecisionType`
  - `init_observability`
- Done when:
  - `from src.observability import record_decision` works from any backend module

### 2. Implement Tracer Facade

- [ ] Create file:
  - `backend/src/observability/tracer.py`
- [ ] Implement `get_tracer()`:
  - when `opentelemetry` is installed: return `trace.get_tracer("deer-flow")`
  - when not installed: return `_NoopTracer()` instance
- [ ] Implement `span()` context manager:
  - accepts: `name: str`, `attributes: dict | None`, `parent_span: Any | None`
  - yields: `SpanHandle` instance
  - on exception: records error on span, then re-raises
- [ ] Implement `SpanHandle` class:
  - `set_attribute(key, value)` — writes to OTel span or noop
  - `add_event(name, attributes)` — writes to OTel span or noop
  - `record_error(exc)` — sets span status ERROR + records exception, or noop
  - `elapsed_ms` property — returns `(perf_counter() - t0) * 1000`
- [ ] Implement `_NoopTracer` class:
  - `start_as_current_span()` returns `contextlib.nullcontext()`
- [ ] Implement `_sanitize_attributes()` helper:
  - converts non-primitive values to strings
  - truncates long strings (max 500 chars)
- Done when:
  - `with span("test", attributes={"run_id": "x"}) as s:` works regardless of whether OTel is installed
  - `s.elapsed_ms` returns correct duration

### 3. Implement Metrics Facade

- [ ] Create file:
  - `backend/src/observability/metrics.py`
- [ ] Implement `WorkflowMetrics` singleton class:
  - thread-safe via `threading.Lock`
  - `WorkflowMetrics.get()` returns singleton instance
- [ ] When `opentelemetry.metrics` is available, create OTel instruments:
  - `workflow.total` (Counter)
  - `workflow.duration_ms` (Histogram, unit=ms)
  - `task.total` (Counter)
  - `task.duration_ms` (Histogram, unit=ms)
  - `task.failure.total` (Counter)
  - `llm.call.total` (Counter)
  - `llm.call.duration_ms` (Histogram, unit=ms)
  - `llm.tokens.total` (Counter)
  - `mcp.call.total` (Counter)
  - `mcp.call.duration_ms` (Histogram, unit=ms)
  - `intervention.total` (Counter)
  - `helper.retry.total` (Counter)
- [ ] When `opentelemetry.metrics` is NOT available, use in-memory dicts:
  - `_counters: dict[str, float]`
  - `_histograms: dict[str, list[float]]`
- [ ] Implement recording methods (each method writes to OTel instruments or in-memory fallback):
  - `record_workflow_start(run_id, mode)`
  - `record_workflow_end(run_id, mode, duration_ms, status)`
  - `record_task(task_id, agent, status, duration_ms)`
  - `record_llm_call(model, node, duration_ms, input_tokens, output_tokens)`
  - `record_mcp_call(tool_name, agent, duration_ms, success)`
  - `record_intervention(agent, tool, risk_level)`
  - `record_helper_retry(parent_task_id, agent)`
- [ ] Implement `snapshot()` method for in-memory fallback:
  - returns `{"counters": {...}, "histograms": {...}}` with count/avg/max/p95 per histogram
- Done when:
  - `WorkflowMetrics.get().record_llm_call(...)` works regardless of OTel installation
  - `WorkflowMetrics.get().snapshot()` returns readable stats in fallback mode

### 4. Implement Decision Log

- [ ] Create file:
  - `backend/src/observability/decision_log.py`
- [ ] Create dedicated logger:
  - name: `deer-flow.decisions`
  - NOT using `__name__` — this is a cross-cutting logger
- [ ] Define `DecisionType` literal type with all decision types:
  - `orchestration_mode`
  - `task_decomposition`
  - `workflow_completion`
  - `agent_route`
  - `agent_route_fallback`
  - `helper_dispatch`
  - `helper_retry`
  - `budget_escalation`
  - `dependency_resolution`
  - `outcome_classification`
  - `intervention_trigger`
  - `intervention_resolution`
- [ ] Implement `record_decision()` function:
  - parameters: `decision_type`, `run_id`, `task_id`, `agent_name`, `inputs`, `output`, `reason`, `alternatives`, `confidence`, `duration_ms`
  - all parameters except `decision_type` are optional
  - builds dict, strips None values, serializes to JSON
  - calls `logger.info(json_string)` on the `deer-flow.decisions` logger
- [ ] Implement `_truncate_dict()` helper:
  - truncates string values > 500 chars
  - recursively handles nested dicts
- Done when:
  - `record_decision(decision_type="agent_route", run_id="r1", ...)` outputs one JSON line to the decisions logger
  - output contains no None fields

### 5. Implement Setup Module

- [ ] Create file:
  - `backend/src/observability/setup.py`
- [ ] Implement `init_observability()` function:
  - calls `_setup_decision_logger()`
  - calls `_setup_otel_if_enabled()`
  - logs initialization status
- [ ] Implement `_setup_decision_logger()`:
  - gets logger `deer-flow.decisions`
  - sets level to INFO
  - sets `propagate = False` — CRITICAL: must not mix with module logs
  - reads `DEER_FLOW_DECISION_LOG_FILE` env var
  - if set: adds `FileHandler` with utf-8 encoding
  - if not set: adds `StreamHandler` (stdout)
  - formatter: `"%(message)s"` only (JSON content is the message itself)
- [ ] Implement `_setup_otel_if_enabled()`:
  - reads `OTEL_ENABLED` env var
  - if not `"true"`: logs disabled message and returns
  - if `"true"`: imports OTel SDK, creates TracerProvider + MeterProvider, sets exporters
  - on ImportError: logs warning and returns (graceful degradation)
  - on other Exception: logs error and returns
- Done when:
  - `init_observability()` succeeds with no OTel packages installed
  - decision log entries go to stdout or configured file
  - setting `OTEL_ENABLED=true` with OTel packages enables trace export

### 6. Integrate Startup Initialization

- [ ] Modify file:
  - `backend/src/gateway/app.py`
- [ ] Add to application startup (before request handling):
  - `from src.observability import init_observability`
  - `init_observability()`
- [ ] Register debug metrics endpoint:
  - `GET /debug/metrics`
  - returns `WorkflowMetrics.get().snapshot()`
  - this endpoint is for development/debugging only
- Done when:
  - server startup log shows `[Observability] Initialization complete.`
  - `GET /debug/metrics` returns `{"counters": {}, "histograms": {}}` (initially empty)

### 7. Add Decision Log Instrumentation — Orchestration Selector

- [ ] Modify file:
  - `backend/src/agents/orchestration/selector.py`
- [ ] Insert `record_decision()` call after mode resolution (near existing logger.info at line ~321-332):
  - `decision_type = "orchestration_mode"`
  - `inputs`: `requested_mode`, `agent_default_mode`, `heuristic_scores` (if computed)
  - `output`: `resolved_mode`
  - `reason`: `orchestration_reason` field value
  - `alternatives`: the mode not chosen
- [ ] Do NOT remove existing `logger.info(...)` call — keep both during migration
- Done when:
  - every orchestration mode decision produces one JSON line in decision log
  - existing module log entry still appears

### 8. Add Decision Log Instrumentation — Planner

- [ ] Modify file:
  - `backend/src/agents/planner/node.py`
- [ ] Insert `record_decision()` at DECOMPOSE completion (after new_tasks generated, near line ~609):
  - `decision_type = "task_decomposition"`
  - `inputs`: `goal` (truncated), `available_agents`, `mode` ("decompose")
  - `output`: `task_count`, list of `{task_id, assigned_agent, description}` (truncated)
  - `duration_ms`: time from planner node entry to task generation
- [ ] Insert `record_decision()` at VALIDATE done=true (near line ~563):
  - `decision_type = "workflow_completion"`
  - `inputs`: `task_summary` (truncated), `facts_summary` (truncated)
  - `output`: `done=true`, `summary_length`
  - `reason`: "planner_validate_done"
- [ ] Insert `record_decision()` at VALIDATE with follow-up tasks (near line ~595-607):
  - `decision_type = "task_decomposition"`
  - `inputs`: `failed_tasks`, `remaining_goal`
  - `output`: `follow_up_task_ids`
  - `reason`: "validate_follow_up"
- [ ] Do NOT remove existing `logger.info(...)` calls
- Done when:
  - every task decomposition and completion decision produces one JSON line
  - decision log contains enough information to understand WHY tasks were created

### 9. Add Decision Log Instrumentation — Router

- [ ] Modify file:
  - `backend/src/agents/router/semantic_router.py`
- [ ] Insert `record_decision()` at fast path route (near line ~826):
  - `decision_type = "agent_route"`
  - `inputs`: `task_id`, `task_description` (truncated)
  - `output`: `selected_agent`
  - `reason`: "fast_path_pre_assigned"
- [ ] Insert `record_decision()` at LLM route (near line ~830):
  - `decision_type = "agent_route"`
  - `inputs`: `task_id`, `task_description` (truncated), `candidates`
  - `output`: `selected_agent`
  - `reason`: "llm_route"
  - `alternatives`: other candidate agents
- [ ] Insert `record_decision()` at SYSTEM_FALLBACK (near line ~95-108):
  - `decision_type = "agent_route_fallback"`
  - `inputs`: `task_id`, `task_description` (truncated)
  - `reason`: LLM error message or "no_suitable_agent"
- [ ] Insert `record_decision()` at helper dispatch (near line ~262-268):
  - `decision_type = "helper_dispatch"`
  - `inputs`: `parent_task_id`, `help_request` (truncated), `requester`
  - `output`: `helper_task_id`, `assigned_agent`
  - `alternatives`: candidate names
- [ ] Insert `record_decision()` at helper retry (near line ~504-510):
  - `decision_type = "helper_retry"`
  - `inputs`: `parent_task_id`, `failed_helper_task_id`
  - `output`: `new_helper_task_id`, `retry_count`
  - `reason`: budget_reason string
- [ ] Insert `record_decision()` at budget escalation (near line ~739-746):
  - `decision_type = "budget_escalation"`
  - `inputs`: `route_count`, `help_depth`, `resume_count`
  - `reason`: budget exhaustion detail
- [ ] Insert `record_decision()` at dependency resolution (near line ~603-612):
  - `decision_type = "dependency_resolution"`
  - `inputs`: `parent_task_id`, `resolved_input_keys`
  - `output`: `resume_status`, `assigned_agent`
- [ ] Insert `record_decision()` at intervention resolution (near line ~689-696):
  - `decision_type = "intervention_resolution"`
  - `inputs`: `task_id`, `request_id`, `action_key`
  - `output`: `behavior`, `new_status`
- [ ] Do NOT remove existing `logger.info(...)` calls
- Done when:
  - every routing decision, helper dispatch, retry, budget exhaustion, and resolution produces one JSON line
  - decision log entries include run_id and task_id for cross-referencing

### 10. Add Decision Log Instrumentation — Executor

- [ ] Modify file:
  - `backend/src/agents/executor/executor.py`
- [ ] Insert `record_decision()` after outcome normalization (near `_log_executor_decision` calls):
  - `decision_type = "outcome_classification"`
  - `inputs`: `message_count`, `last_message_type`, `last_message_name`
  - `output`: `outcome_kind` (or current classification), `used_fallback`
  - `task_id` and `agent_name` from context
- [ ] Insert `record_decision()` at intervention trigger (when `intervention_required` is detected):
  - `decision_type = "intervention_trigger"`
  - `inputs`: `tool_name`, `tool_args_keys` (NOT full args — avoid logging sensitive data)
  - `output`: `request_id`, `risk_level`
  - `agent_name` from context
- [ ] Evaluate whether `_log_executor_decision()` can be replaced by `record_decision()`:
  - if both produce equivalent information, mark old function as deprecated
  - if old function has unique fields, keep both during migration
- [ ] Do NOT remove `_log_executor_decision()` in this phase
- Done when:
  - every executor outcome classification and intervention trigger produces one JSON line
  - sensitive tool arguments (passwords, tokens, etc.) are NOT included in decision log

### 11. Add Observability Configuration

- [ ] Create file:
  - `backend/src/config/observability_config.py`
- [ ] Define configuration classes:
  - `OtelConfig`: `enabled`, `endpoint`, `service_name`
  - `DecisionLogConfig`: `enabled`, `output` ("stdout"|"file"), `file_path`
  - `ObservabilityConfig`: `otel`, `decision_log`, `metrics_enabled`, `metrics_expose_endpoint`
- [ ] Set sensible defaults:
  - OTel disabled by default
  - Decision log enabled, output to stdout
  - Metrics enabled
- [ ] Integrate with existing config loading if applicable
- Done when:
  - observability behavior is configurable without code changes
  - defaults are safe for development and production

### Phase 1 Sign-Off

- [ ] Decision log outputs structured JSONL for all 12 decision types
- [ ] `deer-flow.decisions` logger is independent from module loggers
- [ ] `/debug/metrics` endpoint returns valid JSON
- [ ] `init_observability()` succeeds without OTel packages installed
- [ ] Existing module logs, SSE events, and API behavior are unchanged
- [ ] No frontend changes required

---

## Phase 2: Span Tracing + LLM/MCP Metrics

### 12. Implement Node Wrapper

- [ ] Create file:
  - `backend/src/observability/node_wrapper.py`
- [ ] Implement `traced_node(node_name: str)` decorator:
  - wraps async LangGraph node functions
  - creates span named `node.{node_name}`
  - span attributes: `run_id`, `task_id` (from running tasks), `node`, `route_count`, `task_pool_size`
  - on completion: records `execution_state` transition as span attribute
  - passes through original return value unchanged
- [ ] Decorator must NOT change function signature or behavior
- [ ] Decorator must NOT catch or swallow exceptions — only record them on span
- Done when:
  - `@traced_node("planner")` wraps a node function with span creation
  - original function behavior is completely unchanged

### 13. Integrate Node Wrapper Into Graph

- [ ] Modify file:
  - `backend/src/agents/graph.py`
- [ ] Wrap graph node registrations:
  - `graph.add_node("planner", traced_node("planner")(planner_node))`
  - `graph.add_node("router", traced_node("router")(router_node))`
  - `graph.add_node("executor", traced_node("executor")(executor_node))`
- [ ] If entry_graph.py also registers nodes, apply same pattern there
- [ ] Verify: graph execution behavior is identical with and without OTel
- Done when:
  - each graph node execution creates a span (or noop) with correct attributes
  - graph transitions and state updates are unchanged

### 14. Implement LLM Callback Handler

- [ ] Create file:
  - `backend/src/observability/llm_callback.py`
- [ ] Implement `ObservabilityCallbackHandler(AsyncCallbackHandler)`:
  - `on_llm_start`: records start time keyed by `run_id`
  - `on_llm_end`: calculates duration, extracts token usage from response, calls `WorkflowMetrics.get().record_llm_call()`
  - `on_llm_error`: cleans up start time record
- [ ] Token extraction logic must handle multiple response formats:
  - `response.llm_output.token_usage` (OpenAI format)
  - `response.generations[0][0].generation_info.usage` (alternative format)
  - `prompt_tokens` / `input_tokens` (naming variants)
  - `completion_tokens` / `output_tokens` (naming variants)
  - if no usage data found: record 0 tokens (not an error)
- [ ] Constructor accepts `node_hint: str` for labeling which graph node initiated the call
- Done when:
  - every LLM call records duration and token usage to WorkflowMetrics
  - handler does not raise exceptions on unexpected response formats

### 15. Integrate LLM Callback Into Model Factory

- [ ] Modify file:
  - `backend/src/models/factory.py`
- [ ] In `create_chat_model()`, after existing LangSmith callback registration:
  - import `ObservabilityCallbackHandler`
  - append `ObservabilityCallbackHandler(node_hint="default")` to callbacks list
- [ ] Ensure both LangSmith tracer and ObservabilityCallbackHandler coexist:
  - callbacks list should contain both when both are enabled
  - each operates independently
- Done when:
  - LLM calls produce both LangSmith traces (if enabled) AND internal metrics
  - `GET /debug/metrics` shows `llm.call.total` and `llm.call.duration_ms` after LLM calls

### 16. Add MCP Tool Call Metrics

- [ ] Identify the MCP tool invocation layer:
  - likely in `_ScopedMCPClient` or the tool wrapper used by Agent executor
  - find the function that actually dispatches MCP tool calls
- [ ] Add timing instrumentation around MCP tool dispatch:
  - record `time.perf_counter()` before and after call
  - call `WorkflowMetrics.get().record_mcp_call(tool_name, agent, duration_ms, success)`
  - `success = True` if no exception; `False` if exception raised
- [ ] Ensure instrumentation does NOT catch or modify exceptions
- [ ] Ensure instrumentation does NOT change tool call arguments or return values
- [ ] Implementation approach options (choose one):
  - Option A: wrap in existing middleware chain (e.g., alongside InterventionMiddleware)
  - Option B: add timing directly in the MCP tool dispatch function
  - Option A is preferred if middleware chain already exists
- Done when:
  - `GET /debug/metrics` shows `mcp.call.total` and `mcp.call.duration_ms` per tool/agent
  - tool call behavior is completely unchanged

### 17. Add Workflow-Level Timing

- [ ] In the graph root node or entry point, record workflow start:
  - call `WorkflowMetrics.get().record_workflow_start(run_id, mode)`
- [ ] At workflow completion (planner declares done, or ERROR/INTERRUPTED):
  - calculate total duration
  - call `WorkflowMetrics.get().record_workflow_end(run_id, mode, duration_ms, status)`
- [ ] At task completion (executor marks task DONE/FAILED):
  - calculate task duration (from task_started to completion)
  - call `WorkflowMetrics.get().record_task(task_id, agent, status, duration_ms)`
- Done when:
  - `GET /debug/metrics` shows workflow and task duration histograms after execution

### Phase 2 Sign-Off

- [ ] Graph node execution creates spans with correct attributes
- [ ] LLM calls record duration and token usage to metrics
- [ ] MCP tool calls record duration and success/failure to metrics
- [ ] Workflow and task durations are tracked
- [ ] All metrics visible via `GET /debug/metrics`
- [ ] No behavior changes in Planner/Router/Executor/Agent
- [ ] No frontend changes required

---

## Phase 3: OTEL Export + Debug Tooling

### 18. Complete OTEL Setup Logic

- [ ] Verify `_setup_otel_if_enabled()` in `setup.py` handles:
  - TracerProvider with OTLP gRPC exporter
  - MeterProvider with periodic OTLP metric reader (15s interval)
  - Resource with `service.name` attribute
  - Graceful fallback on ImportError
- [ ] Add `pyproject.toml` optional dependency group:
  - `[project.optional-dependencies]`
  - `observability = ["opentelemetry-api>=1.20", "opentelemetry-sdk>=1.20", "opentelemetry-exporter-otlp-proto-grpc>=1.20"]`
- Done when:
  - `pip install -e ".[observability]"` installs OTel packages
  - `OTEL_ENABLED=true` with OTLP collector receives traces and metrics

### 19. Add Decision Log Query Endpoint (Optional)

- [ ] Consider adding:
  - `GET /debug/decisions?run_id={run_id}`
  - reads from decision log file (if file mode) or returns empty (if stdout mode)
- [ ] This is a convenience endpoint for debugging, not required for production
- Done when:
  - developers can query decision history for a specific run via HTTP

### 20. Add docker-compose Observability Stack (Optional)

- [ ] Create file:
  - `docker-compose.observability.yml`
- [ ] Include services:
  - Jaeger (all-in-one) for trace visualization
  - Prometheus for metric scraping (or rely on OTLP)
  - Grafana for dashboards
- [ ] Add README section explaining how to start the observability stack
- Done when:
  - `docker-compose -f docker-compose.observability.yml up` starts the full stack
  - traces appear in Jaeger UI
  - metrics appear in Grafana

### Phase 3 Sign-Off

- [ ] OTEL export works end-to-end with external collector
- [ ] Optional dependency installation is documented
- [ ] System runs correctly without observability optional dependencies

---

## Suggested PR Breakdown

- [ ] PR 1: `src/observability/` package + init + decision_log + setup + gateway integration
  - contains: steps 1-6, 11
  - can be reviewed and deployed independently
- [ ] PR 2: Decision log instrumentation for all nodes
  - contains: steps 7-10
  - depends on PR 1
- [ ] PR 3: Span tracing + LLM/MCP metrics
  - contains: steps 12-17
  - depends on PR 1
- [ ] PR 4: OTEL export + optional tooling
  - contains: steps 18-20
  - depends on PR 1

PRs 2 and 3 can be developed in parallel after PR 1 merges.

## Final Backend Sign-Off

- [ ] All 12 decision types produce structured JSONL entries
- [ ] Decision log is independent from module logs (separate logger, no propagation)
- [ ] Span tree covers: graph nodes, LLM calls, MCP tool calls
- [ ] Metrics cover: workflow duration, task duration, LLM latency/tokens, MCP latency/success
- [ ] `/debug/metrics` endpoint works in fallback mode (no OTel)
- [ ] OTEL export works when enabled with optional dependencies
- [ ] Existing frontend SSE events, API contracts, and task state are unchanged
- [ ] Existing LangSmith integration still functions when enabled
- [ ] No sensitive data (passwords, tokens, full tool args) appears in decision logs
