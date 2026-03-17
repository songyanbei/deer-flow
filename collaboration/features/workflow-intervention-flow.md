# Feature: Workflow Intervention Flow

- Status: `draft`
- Owner suggestion: `backend` for intervention protocol and runtime lifecycle, `frontend` for intervention surface and action UX, `test` for cross-run and resume validation
- Related area: workflow mode, human intervention, task interruption and resume

## Goal

Build a general-purpose intervention flow for `workflow` mode so that any
execution step that requires explicit user involvement can pause in a uniform
way and resume through a structured resolution protocol.

This is a framework capability, not a single business flow. Risk approval is
one intervention type, but not the only one.

Primary objectives:

1. workflow can pause on any user-owned gate using one unified protocol
2. intervention state survives refresh/reconnect through authoritative
   `task_pool` state
3. user actions are submitted as structured resolutions, not inferred from chat
   text
4. resume behavior is deterministic and can route to:
   - resume current task
   - fail current task
   - re-plan with updated input

## Why This Needs Frontend/Backend Collaboration

Backend owns:

- intervention trigger points
- interruption and resume lifecycle
- event/API contract
- authoritative persistence in `task_pool` and thread state

Frontend owns:

- intervention summary priority in workflow footer
- intervention card rendering
- generic action rendering from schema
- optimistic update and final authoritative reconciliation

Without a shared contract:

- backend will leak scenario-specific semantics into framework code
- frontend will hard-code one-off UI paths for approval, confirmation, and user
  overrides

## Related Source Documents

1. `多智能体兼容改造分阶段实施方案.md`
2. `多智能体架构技术路线说明书.md`
3. `collaboration/使用说明.md`

This feature doc is the framework-oriented collaboration summary. The earlier
implementation notes remain useful reference material, but this document
elevates the design from “approval flow” to a reusable intervention capability.

## Design Principle

Framework code should not encode business-specific semantics such as “meeting
room confirmation”, “send approval”, or “change assignee”.

Framework code should only answer:

1. why the workflow must pause
2. what actions the user may take
3. what payload each action requires
4. how the workflow continues after resolution

Business meaning belongs in:

- tool metadata
- agent config / policy config
- router or executor decision mapping
- agent prompt / skill / registry layers

## Current Behavior

### Backend

- workflow already supports:
  - `request_help`
  - `ask_clarification`
  - `INTERRUPTED` resume path
  - authoritative `task_pool` persistence
- domain-agent middleware chain already exists in:
  - `backend/src/agents/lead_agent/agent.py`
- current system does **not** yet have:
  - a formal `WAITING_INTERVENTION` task status
  - structured `intervention_request`
  - structured `intervention_resolution`
  - a generic action schema that can cover approval, choice, and custom input

### Frontend

- workflow footer, task panel, and subtask cards already render:
  - `waiting_clarification`
  - `waiting_dependency`
  - `in_progress`
  - `pending`
- current system does **not** yet have:
  - `waiting_intervention` summary priority
  - a reusable `InterventionCard`
  - schema-driven actions and payload inputs
  - intervention event ingestion

## In Scope

1. `workflow` mode only in the first iteration
2. unified intervention protocol across risky actions and user-owned decisions
3. `before_tool` intervention interception for risky side-effect tools
4. router/executor-originated intervention protocol reservation only
5. task state persistence using `WAITING_INTERVENTION`
6. structured intervention resolve action
7. backend and frontend regression coverage

## Out Of Scope

1. full `leader` mode redesign
2. model-based risk classification
3. exact graph-frame continuation from the paused tool call
4. parallel scheduler changes
5. agent-specific business rules baked into framework core

## Contract To Confirm First

- Event/API:
  - backend emits `task_waiting_intervention`
  - frontend submits explicit resolution to a dedicated intervention endpoint
- Payload shape:
  - `intervention_request` contains reason, context, action schema, request id,
    and fingerprint
- Persistence:
  - authoritative intervention state lives in `task_pool`
  - intervention resolutions live in thread state
- Error behavior:
  - invalid or stale resolutions are rejected deterministically
  - configured action mapping decides whether resolution resumes, fails, or
    replans
- Dedup/replacement:
  - same `intervention_fingerprint` in the same `run_id` must not create a
    duplicate intervention prompt

## Frozen Decisions For Phase 1

The following three items are treated as frozen for the first vertical slice.
Frontend and backend should build against them directly instead of reopening the
same design discussion during implementation.

### 1. Resolve Endpoint Is Fixed

Phase 1 uses a dedicated intervention resolve endpoint.

Frozen decision:

```http
POST /api/threads/{thread_id}/interventions/{request_id}:resolve
```

Why this is fixed:

1. intervention resolution is a state transition, not a normal chat message
2. the endpoint must support idempotency and stale-request rejection
3. the protocol should remain reusable for approval, override input, and choice
   actions

Phase 1 explicitly does **not** use the generic message submit path for
intervention resolution.

### 2. Protocol Field Set Is Fixed

Phase 1 freezes the minimum protocol shape so frontend and backend can develop
in parallel without inventing private fields.

Frozen request fields:

1. `InterventionRequest`
   - `request_id`
   - `fingerprint`
   - `intervention_type`
   - `title`
   - `reason`
   - `description?`
   - `source_agent`
   - `source_task_id`
   - `tool_name?`
   - `risk_level?`
   - `category?`
   - `context?`
   - `action_summary?`
   - `action_schema`
   - `created_at`
2. `InterventionActionSchema.actions[]`
   - `key`
   - `label`
   - `kind`
   - `resolution_behavior`
   - `payload_schema?`
   - `placeholder?`
3. `InterventionResolution`
   - `request_id`
   - `fingerprint`
   - `action_key`
   - `payload`

Any extra fields added during implementation must be treated as non-blocking
extensions and must not replace these frozen fields.

### 3. Phase 1 Scope Is Fixed

To keep the first slice small enough for stable landing, Phase 1 scope is
explicitly constrained.

Phase 1 includes:

1. `workflow` mode only
2. tool-originated intervention only
3. `before_tool` interception only
4. action kinds:
   - `button`
   - `input`
5. resolution outcomes implemented in the first slice:
   - `resume_current_task`
   - `fail_current_task`
6. one generic `InterventionCard` on the frontend

Phase 1 does **not** require:

1. `leader` mode integration
2. router-originated intervention execution path
3. executor-originated intervention execution path
4. `select` or `composite` frontend rendering
5. `replan_from_resolution` runtime execution
   - protocol stays reserved
   - execution can follow in Phase 2
6. timeout automation

If a requirement depends on any excluded item above, it should be written back
as a follow-up and not silently expanded into the Phase 1 implementation.

## Proposed Minimal Contract

### Thread Task Extension

```ts
type InterventionActionSchema = {
  actions: Array<{
    key: string;
    label: string;
    kind: "button" | "input" | "select" | "composite";
    resolution_behavior:
      | "resume_current_task"
      | "fail_current_task"
      | "replan_from_resolution";
    payload_schema?: Record<string, unknown>;
    placeholder?: string;
  }>;
};

type InterventionRequest = {
  request_id: string;
  fingerprint: string;
  intervention_type: string;
  title: string;
  reason: string;
  description?: string;
  source_agent: string;
  source_task_id: string;
  tool_name?: string;
  risk_level?: "medium" | "high" | "critical";
  category?: string;
  context?: Record<string, unknown>;
  action_summary?: string;
  action_schema: InterventionActionSchema;
  created_at: string;
};
```

```ts
type ThreadTaskState = {
  status:
    | "PENDING"
    | "RUNNING"
    | "WAITING_DEPENDENCY"
    | "WAITING_INTERVENTION"
    | "DONE"
    | "FAILED";
  intervention_request?: InterventionRequest | null;
  intervention_status?:
    | "pending"
    | "resolved"
    | "consumed"
    | "rejected"
    | null;
  intervention_fingerprint?: string | null;
};
```

### Task Event

```ts
type TaskWaitingInterventionEvent = {
  type: "task_waiting_intervention";
  source: "multi_agent";
  run_id: string;
  task_id: string;
  agent_name: string;
  status: "waiting_intervention";
  status_detail: string;
  intervention_request: InterventionRequest;
};
```

### Intervention Resolve Action

Recommended first choice:

```http
POST /api/threads/{thread_id}/interventions/{request_id}:resolve
```

Body:

```json
{
  "fingerprint": "...",
  "action_key": "approve",
  "payload": {
    "comment": "可以执行"
  }
}
```

Another example:

```json
{
  "fingerprint": "...",
  "action_key": "change_room",
  "payload": {
    "room_name": "3号会议室",
    "comment": "时间不变"
  }
}
```

Frozen Phase 1 request body:

```json
{
  "fingerprint": "...",
  "action_key": "approve",
  "payload": {
    "comment": "可以执行"
  }
}
```

Phase 1 request-body constraints:

1. `fingerprint` is required
2. `action_key` is required
3. `payload` is required and must be an object
4. resolution requests without these fields should be rejected by backend

Recommended Phase 1 success response:

```json
{
  "ok": true,
  "thread_id": "...",
  "request_id": "...",
  "fingerprint": "...",
  "accepted": true
}
```

Recommended rejection cases:

1. `404` when `thread_id` or `request_id` does not match a live/persisted
   intervention
2. `409` when `fingerprint` does not match the authoritative intervention
3. `422` when `payload` does not satisfy the selected action requirements

### Resolution Outcome Mapping

Framework should support at least three resolution outcomes:

1. `resume_current_task`
2. `fail_current_task`
3. `replan_from_resolution`

The framework does not hard-code which action means which outcome. That mapping
comes from the intervention producer or policy layer.

For Phase 1, that mapping should be carried explicitly by
`InterventionActionSchema.actions[].resolution_behavior` so backend runtime does
not need to infer behavior from action names.

Phase 1 runtime behavior is frozen as:

1. implemented:
   - `resume_current_task`
   - `fail_current_task`
2. protocol reserved only:
   - `replan_from_resolution`

### Intervention Status Semantics

To avoid state drift between frontend and backend, Phase 1 uses these meanings:

1. `pending`
   - intervention is waiting for a user action
2. `resolved`
   - backend has accepted a valid resolution and persisted it
3. `consumed`
   - workflow runtime has already applied that resolution and moved on
4. `rejected`
   - backend rejected the submitted resolution request itself
   - this is for invalid/stale submissions, not for a user choosing a
     reject-style action

## Generic Examples

This protocol should support all of the following without changing framework
core:

1. Risk approval
   - actions:
     - `approve`
     - `reject`
2. Meeting room confirmation
   - actions:
     - `keep_current_room`
     - `change_room`
3. User override input
   - actions:
     - `provide_input`
4. Strategy choice
   - actions:
     - `choose_fast_path`
     - `choose_safe_path`

For Phase 1 implementation, examples 3 and 4 remain protocol examples only.
Only tool-originated intervention with `button` and `input` actions must be
shipped in the first slice.

## Proposed Trigger Points

### `before_tool`

Use when:

1. the tool is risky or side-effectful
2. metadata or policy requires user intervention before execution

### `router`

Use when:

1. multiple valid paths exist and user must choose one
2. the top-level workflow needs user arbitration

### `executor`

Use when:

1. the running task has already reached a user-owned checkpoint
2. the agent produced a structured intervention signal

The framework should not assume middleware is the only intervention source.

## Backend Changes

1. add intervention state to `thread_state.py`
2. add `InterventionMiddleware` for tool-level interception
3. register middleware in the domain-agent middleware chain
4. extend `executor.py` to handle `intervention_required`
5. extend `workflow_resume.py` to detect structured intervention resolutions
6. extend config/API with generic `intervention_policies`
7. emit intervention events and persist intervention resolutions

See:

- `workflow-intervention-flow-backend-checklist.md`

## Frontend Changes

1. extend workflow task/thread types with intervention fields
2. consume `task_waiting_intervention`
3. render `waiting_intervention` as the highest-priority blocking state
4. add a schema-driven `InterventionCard`
5. submit generic intervention resolutions through one endpoint

See:

- `workflow-intervention-flow-frontend-checklist.md`

## Test Changes

1. backend middleware and executor regression coverage
2. frontend adapters, hooks, summary, and card rendering coverage
3. manual/end-to-end validation for refresh, resume, replan, reject, and dedup

See:

- `workflow-intervention-flow-test-checklist.md`

## Risks

1. if intervention is modeled as plain chat text, resume logic will be fragile
2. if framework code hard-codes business action names, extensibility will
   collapse quickly
3. if intervention state is not persisted in `task_pool`, refresh/reconnect
   will break the UX
4. if fingerprinting is missing, the same intervention may reopen repeatedly
5. if outcome mapping is implicit, resume behavior will become inconsistent

## Acceptance Criteria

1. workflow can enter `WAITING_INTERVENTION` from tool, router, or executor
   paths
2. footer/task panel clearly surfaces intervention state as the primary blocker
3. frontend can render actions from schema without hard-coding each scenario
4. one resolution endpoint can handle approval, rejection, override input, and
   selection actions
5. refresh/reconnect restores intervention state from authoritative thread data
6. the same intervention does not reopen repeatedly within the same `run_id`
7. framework core remains business-agnostic

## Open Questions

1. whether first iteration should implement only tool-originated intervention
   while keeping router/executor-originated intervention protocol-ready
2. whether resolution outcome mapping should be stored directly in
   `intervention_request` or derived from action config plus executor/router
   policy
