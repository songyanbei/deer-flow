# Workflow Intervention Flow Backend Checklist

- Status: `implemented`
- Owner: `backend`
- Related feature: `workflow-intervention-flow.md`

## 1. Thread State And Protocol

- [x] Implement only the frozen Phase 1 protocol fields from
  `workflow-intervention-flow.md`
- [x] Implement frozen action field:
  - `resolution_behavior`
- [x] Add `InterventionRequest` and `InterventionResolution` protocol types in
  `backend/src/agents/thread_state.py`
- [x] Extend `TaskStatus.status` with `WAITING_INTERVENTION`
- [x] Add task fields:
  - `intervention_request`
  - `intervention_status`
  - `intervention_fingerprint`
  - `intervention_resolution`
- [x] Add thread-level intervention resolution storage
- Done when:
  - intervention state can be fully restored from thread state
  - no frontend-only intervention authority is introduced

## 2. Trigger And Fingerprint Rules

- [x] Keep Phase 1 trigger scope limited to tool-originated intervention only
- [x] Define deterministic trigger order for tool-originated intervention:
  - explicit metadata (`intervention_policies` per-tool config)
  - structured parser rules (risky keyword detection)
  - `hitl_keywords` fallback for compatibility
- [x] Introduce intervention fingerprint generation based on:
  - `run_id`
  - `task_id`
  - `agent_name`
  - `tool_name` (source_name)
  - normalized tool args (sensitive payload)
  - SHA-256 truncated to 24 chars
- [x] Ensure read-only tools are excluded by default unless explicitly marked
- Done when:
  - trigger logic does not depend on runtime LLM judgment
  - same fingerprint in the same run is not interrupted twice

## 3. Middleware Insertion

- [x] Create `backend/src/agents/middlewares/intervention_middleware.py`
- [x] Implement `wrap_tool_call / awrap_tool_call`
- [x] On intervention hit:
  - emit `ToolMessage(name="intervention_required")`
  - return `Command(..., goto=END)`
- [x] On prior intervention resolution:
  - bypass (resolved fingerprints collected from task_pool)
- [x] Register middleware in
  `backend/src/agents/lead_agent/agent.py`
- Done when:
  - domain-agent tool execution can pause before side effects happen
  - middleware order: InterventionMiddleware → HelpRequestMiddleware → ClarificationMiddleware

## 4. Executor And Router Integration

- [x] Extend `backend/src/agents/executor/executor.py` to parse
  `intervention_required`
- [x] Write interrupted task state as:
  - `status = WAITING_INTERVENTION`
  - `status_detail = @waiting_intervention`
  - `intervention_request = ...`
  - `intervention_status = pending`
- [x] Emit `task_waiting_intervention`
- [x] Return `execution_state = INTERRUPTED`
- [x] Router blocks on WAITING_INTERVENTION (returns INTERRUPTED)
- [x] Graph routes WAITING_INTERVENTION to router → END
- [x] Planner recognizes WAITING_INTERVENTION as active state
- [x] Keep protocol open for router-originated or executor-originated
  intervention requests without implementing those execution paths in Phase 1
- Done when:
  - intervention is a first-class workflow blocking state
  - framework does not assume tool middleware is the only producer

## 5. Resolution Handling

- [x] Extend `backend/src/agents/workflow_resume.py` with structured
  intervention resolution detection
- [x] Accept a generic resolution envelope:
  - `request_id`
  - `fingerprint`
  - `action_key`
  - `payload`
- [x] Persist resolution before task re-entry
- [x] Drive runtime behavior from
  `action_schema.actions[].resolution_behavior`, not action-name heuristics
- [x] Support outcome mapping:
  - `resume_current_task`
  - `fail_current_task`
- [x] Reserve but do not implement in Phase 1:
  - `replan_from_resolution` (protocol reserved, treated as resume)
- [x] Ensure structured user payload can reach resumed execution or planner
  (stored in `resolved_inputs.intervention_resolution`)
- Done when:
  - resume path is deterministic
  - framework does not hard-code `approve / reject / respond`
  - backend behavior stays inside the frozen Phase 1 scope

## 6. Config And API

- [x] Extend `backend/src/config/agents_config.py` with
  `intervention_policies`
- [ ] Extend `backend/src/gateway/routers/agents.py` create/update/get models
  (deferred - policies work through config.yaml already)
- [x] Keep `hitl_keywords` only as backward-compatible fallback where needed
- [x] Add dedicated resolve endpoint for interventions
- [x] Freeze resolve endpoint contract as:
  - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
  - request body must contain `fingerprint + action_key + payload`
- [x] Freeze basic response/error contract:
  - success response includes `ok + thread_id + request_id + fingerprint + accepted`
  - stale fingerprint returns `409`
  - invalid payload returns `422`
- Done when:
  - intervention rules can be configured without business code changes
  - agents can opt into the generic protocol through config and metadata

## 7. Event Contract And Observability

- [x] Add `task_waiting_intervention` to the multi-agent task event contract
- [x] Include in payload:
  - `run_id`
  - `task_id`
  - `agent_name`
  - `status` (`waiting_intervention`)
  - `status_detail` (`@waiting_intervention`)
  - `intervention_request` (full InterventionRequest object)
- [x] Add logs for:
  - intervention requested (InterventionMiddleware)
  - intervention resolved (resolve endpoint)
  - intervention rejected (resolve endpoint 409/422)
  - duplicate/stale resolution (InterventionMiddleware dedup)
- Done when:
  - frontend can fully render intervention state from state and stream
  - production debugging has enough signal for stuck interventions

## 8. Framework Boundary Guardrail

- [x] Keep framework naming generic:
  - no `meeting_room`
  - no `send_approval`
  - no scenario-specific action keys in core types
- [x] Keep action semantics outside framework core:
  - tool metadata via `intervention_policies`
  - config via `AgentConfig`
  - router/executor mapping via `resolution_behavior`
- Done when:
  - a new agent can reuse intervention flow without requiring framework code
    edits

## 9. Validation

- [x] Run targeted backend tests for executor paths
- [x] Confirm existing clarification/request_help tests still pass (363 passed)
- [x] Validate same-run resume does not regress current workflow behavior
- [ ] Add dedicated `backend/tests/test_intervention_middleware.py` (Phase 2)
- Validation:
  - `backend/tests/test_multi_agent_core.py` - all executor tests pass
  - 2 pre-existing test failures unrelated to intervention (Chinese localization)
