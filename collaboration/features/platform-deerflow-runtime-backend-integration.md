# Feature: Platform DeerFlow Runtime Backend Integration

- Status: `implemented`
- Last aligned with code: `2026-04-01`
- Owner suggestion: `backend` + `test`
- Related area: gateway runtime adapter, OIDC resource server, tenant-aware thread registry, platform integration
- Frontend impact: `none required in DeerFlow repo in this phase`

## Goal

Provide a backend-only integration surface so that the external AI developer platform can:

1. authenticate to DeerFlow with the existing Keycloak `moss-market` `access_token`
2. create and own DeerFlow runtime threads
3. send one user message into DeerFlow multi-agent runtime
4. constrain the runtime to a platform-declared `allowed_agents` set
5. reuse existing DeerFlow uploads / artifacts / interventions / governance APIs

This phase does **not** expose DeerFlow frontend pages to the platform. DeerFlow remains a backend runtime only.

## Implementation Status

Implemented in:

- `backend/src/gateway/routers/runtime.py`
- `backend/src/gateway/runtime_service.py`
- `backend/src/gateway/thread_registry.py`
- `backend/src/gateway/app.py`

Verified on `2026-04-01` with:

- `backend/tests/test_runtime_router.py`
- `backend/tests/test_multi_tenant.py`
- `backend/tests/test_uploads_router.py`
- `backend/tests/test_interventions_router.py`
- `backend/tests/test_governance_api.py`
- `backend/tests/test_oidc_middleware.py`
- `backend/tests/test_client.py`

Additional targeted retest confirmed:

- cross-tenant and cross-owner runtime access returns `403`
- unknown `allowed_agents` returns `422`
- upstream thread snapshot failures map to `404` or `503`
- failed runtime submissions do not persist thread binding metadata
- normalized SSE includes top-level `artifact_url`, intervention `fingerprint`, and stable `run_failed` error text

Remaining verification gap:

- dedicated compatibility tests for runtime-created threads in uploads / interventions / governance can still be made more explicit
- runtime endpoints are not yet covered by a dedicated OIDC middleware integration suite

## Why This Needs Frontend/Backend Collaboration

The external platform already owns:

- user login
- chat UI
- agent / MCP / skill management
- platform session/message storage

DeerFlow already owns:

- multi-agent execution
- thread runtime state
- uploads / artifacts
- interventions / governance
- strict OIDC token verification

The missing layer is a backend contract that allows the platform backend to call DeerFlow without coupling directly to DeerFlow's internal LangGraph SDK protocol.

## Current Behavior

### Backend

Current DeerFlow backend already provides:

1. OIDC Resource Server verification in:
   - `backend/src/gateway/middleware/oidc.py`
   - `backend/src/gateway/middleware/oidc_config.py`
2. tenant/user dependency helpers in:
   - `backend/src/gateway/dependencies.py`
3. tenant-aware thread ownership registry in:
   - `backend/src/gateway/thread_registry.py`
4. existing management/operations APIs:
   - `GET /api/models`
   - `GET|POST|PUT|DELETE /api/agents...`
   - `GET|PUT /api/mcp/config`
   - `GET|PUT|POST /api/skills...`
   - `GET|POST /api/memory...`
   - `POST /api/threads/{thread_id}/uploads`
   - `GET /api/threads/{thread_id}/artifacts/{path}`
   - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
   - `GET|POST /api/governance...`

Current DeerFlow chat runtime path is **not** a Gateway business API.
It is the LangGraph runtime exposed through `/api/langgraph`, consumed directly by DeerFlow frontend via:

- `frontend/src/core/api/api-client.ts`
- `frontend/src/core/config/index.ts`
- `frontend/src/core/threads/hooks.ts`

That direct LangGraph protocol is acceptable for DeerFlow's own frontend, but it is **not** the target integration contract for the external platform.

### Frontend

In this phase there is no DeerFlow frontend work required.

The external platform is expected to:

1. keep its own UI and session model
2. call DeerFlow from its backend
3. pass `Authorization: Bearer <access_token>`
4. map `portal_session_id -> deerflow_thread_id`

## Contract To Confirm First

- Event/API:
  - New DeerFlow Gateway runtime adapter API must be added under `/api/runtime/...`
- Payload shape:
  - Thread creation and message streaming payloads are defined below and must be implemented exactly
- Persistence:
  - Thread ownership and platform binding metadata must be persisted in `ThreadRegistry`
- Error behavior:
  - 401 for auth failure
  - 403 for cross-tenant/cross-owner thread access
  - 404 for missing thread/resource
  - 422 for payload validation failure, including unknown agents
  - 503 for LangGraph/JWKS upstream unavailability
- Dedup/replacement:
  - `allowed_agents` must be deduplicated preserving first occurrence order
  - thread creation must be create-only in this phase, not upsert-by-session

## Scope

### In Scope

1. add platform-facing runtime endpoints in DeerFlow Gateway
2. keep OIDC as the only trusted identity source
3. implement `allowed_agents` runtime validation and propagation
4. persist platform/session/runtime metadata per thread
5. reuse existing uploads / artifacts / interventions / governance APIs
6. add backend and integration tests for the new runtime adapter contract

### Out Of Scope

1. changing external platform frontend/UI
2. changing DeerFlow frontend `/api/langgraph` integration
3. changing Keycloak audience / mapper / tenant claim strategy
4. implementing real multi-tenant claim-based isolation beyond current `tenant_id=default` fallback
5. implementing platform-side agent/MCP/skill sync in this phase
6. replacing LangGraph runtime internals

## Functional Requirements

### 1. Authentication Must Reuse Existing OIDC Resource Server

All new runtime endpoints must be protected by existing OIDC middleware.

Trusted identity must come only from:

- `request.state.user_id`
- `request.state.username`
- `request.state.tenant_id`

The request body must not be trusted for `user_id`, `username`, or `tenant_id`.

### 2. Platform Runtime Entry Must Be Gateway-Based

Add a new Gateway router:

- `backend/src/gateway/routers/runtime.py`

Mount it in:

- `backend/src/gateway/app.py`

All platform runtime calls must go through this router, not directly to `/api/langgraph`.

### 3. Thread Creation API

Add:

- `POST /api/runtime/threads`

#### Request

```json
{
  "portal_session_id": "sess_123"
}
```

#### Validation

1. `portal_session_id` is required
2. after trim it must be non-empty
3. maximum length: `128`

#### Behavior

1. create a new LangGraph thread through the local LangGraph server
2. register the thread in `ThreadRegistry`
3. persist owner/binding metadata
4. return the new thread binding payload

#### Response

```json
{
  "thread_id": "thread_xxx",
  "portal_session_id": "sess_123",
  "tenant_id": "default",
  "user_id": "oidc-sub",
  "created_at": "2026-04-01T12:00:00Z"
}
```

#### Error Rules

- `401` when token is missing/invalid
- `503` when LangGraph thread creation fails due to upstream availability

### 4. Thread Binding / Snapshot API

Add:

- `GET /api/runtime/threads/{thread_id}`

#### Behavior

1. validate thread ownership via `ThreadRegistry`
2. return persisted thread binding metadata
3. fetch latest LangGraph state summary when possible

#### Response

```json
{
  "thread_id": "thread_xxx",
  "portal_session_id": "sess_123",
  "tenant_id": "default",
  "user_id": "oidc-sub",
  "group_key": "market-analysis-team",
  "allowed_agents": ["research-agent", "data-analyst"],
  "entry_agent": "research-agent",
  "requested_orchestration_mode": "workflow",
  "created_at": "2026-04-01T12:00:00Z",
  "updated_at": "2026-04-01T12:03:00Z",
  "state": {
    "title": "Untitled",
    "run_id": "run_xxx",
    "workflow_stage": "executing",
    "workflow_stage_detail": "data-analyst is running",
    "artifacts_count": 1,
    "pending_intervention": false
  }
}
```

#### State Summary Rules

- `title`: from thread state values if present, otherwise `null`
- `run_id`: latest run id if present, otherwise `null`
- `workflow_stage`: latest workflow stage if present, otherwise `null`
- `workflow_stage_detail`: latest stage detail if present, otherwise `null`
- `artifacts_count`: count of current `values.artifacts`, default `0`
- `pending_intervention`: `true` iff any task in `task_pool` is pending intervention

#### Error Rules

- `403` when thread exists but belongs to another tenant or owner
- `404` when thread is not found in registry or upstream

### 5. Runtime Message Streaming API

Add:

- `POST /api/runtime/threads/{thread_id}/messages:stream`

Response content type:

- `text/event-stream`

#### Request

```json
{
  "message": "请分析本月销售数据并给出结论",
  "group_key": "market-analysis-team",
  "allowed_agents": ["research-agent", "data-analyst", "report-agent"],
  "entry_agent": "research-agent",
  "requested_orchestration_mode": "workflow",
  "metadata": {
    "source": "portal"
  }
}
```

#### Required Fields

- `message`
- `group_key`
- `allowed_agents`

#### Optional Fields

- `entry_agent`
- `requested_orchestration_mode`
- `metadata`

#### Validation Rules

1. `message` must be a non-empty string after trim
2. `group_key` must be a non-empty string after trim
3. `allowed_agents` must be a non-empty array of strings
4. every agent name must:
   - be non-empty after trim
   - be unique after normalization
   - exist in the current tenant-scoped agents directory
5. `entry_agent`, if provided:
   - must be non-empty after trim
   - must appear in normalized `allowed_agents`
6. `requested_orchestration_mode`, if provided, must be one of:
   - `auto`
   - `leader`
   - `workflow`
7. `metadata`, if provided, must be a JSON object
8. `metadata` may only contain primitive JSON values:
   - string
   - number
   - boolean
   - null

#### Runtime Context Injection Rules

The adapter must submit to LangGraph with context containing at least:

```json
{
  "thread_id": "thread_xxx",
  "tenant_id": "default",
  "user_id": "oidc-sub",
  "username": "gaoming",
  "allowed_agents": ["research-agent", "data-analyst", "report-agent"],
  "group_key": "market-analysis-team",
  "requested_orchestration_mode": "workflow",
  "agent_name": "research-agent"
}
```

Notes:

1. `agent_name` is only injected when `entry_agent` is provided
2. `thinking_enabled` / `is_plan_mode` / `subagent_enabled` are **not** part of this contract in this phase
3. assistant id used for upstream LangGraph execution must be fixed to:
   - `entry_graph`

#### Persistence Rules

On every successful message submission, update thread registry metadata with:

- `group_key`
- `allowed_agents`
- `entry_agent`
- `requested_orchestration_mode`
- `updated_at`

#### Streaming Event Contract

The adapter must normalize outbound SSE to the following stable event names:

1. `ack`
2. `message_delta`
3. `message_completed`
4. `artifact_created`
5. `intervention_requested`
6. `governance_created`
7. `run_completed`
8. `run_failed`

At minimum, each SSE event payload must include:

- `thread_id`
- `run_id` when available
- event-specific fields

The adapter must not forward raw LangGraph event names directly as the external platform contract.

#### Error Rules

- `401` invalid/missing token
- `403` thread ownership mismatch, including cross-tenant and cross-owner access
- `404` thread not found
- `422` payload validation failure
- `503` upstream LangGraph unavailable

### 6. Existing Upload / Artifact / Intervention / Governance APIs Remain Canonical

The following existing APIs remain part of the platform integration contract and do not need new wrappers in this phase:

1. uploads:
   - `POST /api/threads/{thread_id}/uploads`
   - `GET /api/threads/{thread_id}/uploads/list`
   - `DELETE /api/threads/{thread_id}/uploads/{filename}`
2. artifacts:
   - `GET /api/threads/{thread_id}/artifacts/{path}`
3. interventions:
   - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
4. governance:
   - `GET /api/governance/queue`
   - `GET /api/governance/history`
   - `GET /api/governance/{governance_id}`
   - `POST /api/governance/{governance_id}:resolve`

### 7. Thread Registry Must Be Extended to Store Binding Metadata

Current `ThreadRegistry` only stores `thread_id -> tenant_id`.

It must be extended to store:

```json
{
  "thread_xxx": {
    "tenant_id": "default",
    "user_id": "oidc-sub",
    "portal_session_id": "sess_123",
    "group_key": "market-analysis-team",
    "allowed_agents": ["research-agent", "data-analyst"],
    "entry_agent": "research-agent",
    "requested_orchestration_mode": "workflow",
    "created_at": "2026-04-01T12:00:00Z",
    "updated_at": "2026-04-01T12:03:00Z"
  }
}
```

#### Backward Compatibility Rules

1. existing string values must still be readable
2. when old format is encountered, treat it as:

```json
{
  "tenant_id": "<old string value>"
}
```

3. `check_access(thread_id, tenant_id)` behavior must remain backward compatible
4. add helpers:
   - `get_binding(thread_id)`
   - `register_binding(...)`
   - `update_binding(...)`

### 8. Agent Resolution Must Be Tenant-Aware

All `allowed_agents` validation must resolve against the tenant-scoped agents directory exactly as current `/api/agents` does.

The runtime adapter must not read from a separate agent catalog.

## Proposed API Surface

### New Router

- `backend/src/gateway/routers/runtime.py`

### New Endpoints

1. `POST /api/runtime/threads`
2. `GET /api/runtime/threads/{thread_id}`
3. `POST /api/runtime/threads/{thread_id}/messages:stream`

### Existing Endpoints Explicitly Reused

1. `/api/threads/{thread_id}/uploads...`
2. `/api/threads/{thread_id}/artifacts...`
3. `/api/threads/{thread_id}/interventions...`
4. `/api/governance...`

## Backend Changes

### 1. Add Runtime Router

- Add `backend/src/gateway/routers/runtime.py`
- Mount it from `backend/src/gateway/app.py`

### 2. Add LangGraph Runtime Adapter Helper

Add a helper module:

- `backend/src/gateway/runtime_service.py`

Responsibilities:

1. create upstream LangGraph thread
2. fetch thread state summary
3. submit one message to upstream runtime
4. normalize upstream stream events to external platform event contract
5. convert upstream exceptions to Gateway HTTP/SSE errors

### 3. Extend Thread Registry

Modify:

- `backend/src/gateway/thread_registry.py`

Required additions:

1. metadata object storage
2. backward-compatible load behavior
3. owner lookup by `thread_id`
4. binding update helpers

### 4. Add Runtime Payload Validation

Validation should live in `runtime.py` or a dedicated schema/helper module.

Required checks:

1. `portal_session_id`
2. `message`
3. `group_key`
4. `allowed_agents`
5. `entry_agent`
6. `requested_orchestration_mode`
7. primitive-only `metadata`

### 5. Keep Existing DeerFlow Frontend Path Untouched

Do **not** change:

- `frontend/src/core/api/api-client.ts`
- `frontend/src/core/threads/hooks.ts`
- `/api/langgraph` behavior

This feature is additive for platform integration and must not break DeerFlow's own frontend.

## Backend File-Level Change Surface

### Must Change

- `backend/src/gateway/app.py`
- `backend/src/gateway/thread_registry.py`

### Must Add

- `backend/src/gateway/routers/runtime.py`
- `backend/src/gateway/runtime_service.py`

### Existing Modules Reused But Not Semantically Changed

- `backend/src/gateway/dependencies.py`
- `backend/src/gateway/middleware/oidc.py`
- `backend/src/gateway/routers/uploads.py`
- `backend/src/gateway/routers/artifacts.py`
- `backend/src/gateway/routers/interventions.py`
- `backend/src/gateway/routers/governance.py`
- `backend/src/gateway/routers/agents.py`

## Risks

1. Adapter event contract may drift from upstream LangGraph event shapes.
2. Thread registry schema migration may break old entries if backward compatibility is not preserved.
3. Platform may still send `id_token` instead of `access_token`; runtime endpoints must continue relying on OIDC middleware to reject invalid tokens.
4. If `allowed_agents` is validated against a stale or global catalog instead of tenant-scoped agents, platform grouping semantics will be wrong.
5. If the new router leaks raw upstream error text, platform-facing behavior will become unstable.

## Acceptance Criteria

1. A platform backend can create a DeerFlow thread using only `portal_session_id` and a valid Bearer token.
2. A platform backend can send one message into an existing DeerFlow thread using `allowed_agents`, `group_key`, and an optional `entry_agent`.
3. DeerFlow rejects unknown agents, blank agent names, and `entry_agent` values not included in `allowed_agents`.
4. DeerFlow persists `portal_session_id`, `group_key`, latest `allowed_agents`, and latest runtime mode in thread registry metadata.
5. Existing uploads / artifacts / interventions / governance APIs continue to work with the newly created runtime threads.
6. Existing DeerFlow frontend `/api/langgraph` chat flow remains unaffected.
7. All new runtime endpoints are protected by existing OIDC middleware and use `request.state` identity only.

## Open Questions

- None blocking for this phase. The contract in this document should be treated as the implementation source of truth.

## Related Docs

- [OIDC当前阶段可实施方案.md](E:/work/deer-flow/docs/OIDC当前阶段可实施方案.md)
- [平台控制面与DeerFlow运行时交互契约草案.md](E:/work/deer-flow/docs/平台控制面与DeerFlow运行时交互契约草案.md)
