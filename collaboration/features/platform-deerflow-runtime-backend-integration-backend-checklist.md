# Platform DeerFlow Runtime Backend Integration Backend Checklist

- Audience: `backend`
- Status: `completed`
- Last aligned with spec: `2026-04-01`
- Goal: add a stable Gateway runtime adapter so the external platform can use DeerFlow as a backend-only multi-agent runtime

## Backend Scope

### In Scope

1. add Gateway runtime adapter endpoints
2. extend `ThreadRegistry` to persist platform binding metadata
3. validate and propagate `allowed_agents`
4. normalize upstream LangGraph stream events into a stable external contract
5. preserve existing OIDC protection and existing upload/artifact/intervention/governance APIs

### Out Of Scope

1. modifying DeerFlow frontend
2. modifying `/api/langgraph` protocol
3. changing Keycloak audience / tenant claim strategy
4. agent/MCP/skill synchronization from the external platform
5. frontend/UI work in the external platform

## Implementation Checklist

### 1. Runtime Router

- [x] Add `backend/src/gateway/routers/runtime.py`
- [x] Mount router in `backend/src/gateway/app.py`
- [x] Implement `POST /api/runtime/threads`
- [x] Implement `GET /api/runtime/threads/{thread_id}`
- [x] Implement `POST /api/runtime/threads/{thread_id}/messages:stream`

### 2. Runtime Service

- [x] Add `backend/src/gateway/runtime_service.py`
- [x] Implement LangGraph thread creation helper
- [x] Implement thread state summary helper
- [x] Implement message submit helper using fixed assistant id `entry_graph`
- [x] Implement SSE normalization from upstream runtime events
- [x] Map upstream failures to stable Gateway errors

### 3. Thread Registry Metadata

- [x] Extend `backend/src/gateway/thread_registry.py` from `thread_id -> tenant_id` to metadata object storage
- [x] Preserve backward compatibility with existing string entries
- [x] Add `get_binding(thread_id)`
- [x] Add `register_binding(...)`
- [x] Add `update_binding(...)`
- [x] Keep `check_access(thread_id, tenant_id)` backward compatible

### 4. Payload Validation

- [x] Validate `portal_session_id`
- [x] Validate `message`
- [x] Validate `group_key`
- [x] Validate `allowed_agents`
- [x] Deduplicate `allowed_agents` preserving order
- [x] Validate each agent exists in tenant-scoped agent storage
- [x] Validate `entry_agent in allowed_agents`
- [x] Validate `requested_orchestration_mode in {auto, leader, workflow}`
- [x] Validate `metadata` contains only primitive JSON values

### 5. Runtime Context Injection

- [x] Always inject `thread_id` from path
- [x] Always inject `tenant_id` from `request.state`
- [x] Always inject `user_id` from `request.state`
- [x] Always inject `username` from `request.state`
- [x] Inject `allowed_agents`
- [x] Inject `group_key`
- [x] Inject `requested_orchestration_mode`
- [x] Inject `agent_name` only when `entry_agent` is present

### 6. Thread Binding Persistence

- [x] Persist `portal_session_id`
- [x] Persist `tenant_id`
- [x] Persist `user_id`
- [x] Persist `group_key`
- [x] Persist latest `allowed_agents`
- [x] Persist latest `entry_agent`
- [x] Persist latest `requested_orchestration_mode`
- [x] Persist `created_at`
- [x] Update `updated_at` on each successful message submission

### 7. Existing APIs Must Remain Compatible

- [x] Existing `/api/threads/{thread_id}/uploads...` remains unchanged
- [x] Existing `/api/threads/{thread_id}/artifacts...` remains unchanged
- [x] Existing `/api/threads/{thread_id}/interventions...` remains unchanged
- [x] Existing `/api/governance...` remains unchanged
- [x] Existing DeerFlow frontend `/api/langgraph` flow remains unchanged

## Verification Notes

Verified on `2026-04-01` with:

- `backend/tests/test_runtime_router.py`
- `backend/tests/test_multi_tenant.py`
- `backend/tests/test_uploads_router.py`
- `backend/tests/test_interventions_router.py`
- `backend/tests/test_governance_api.py`
- `backend/tests/test_oidc_middleware.py`
- `backend/tests/test_client.py`

Additional targeted retest confirmed:

- cross-owner access control is enforced alongside tenant checks
- unknown `allowed_agents` returns `422`
- upstream snapshot failures map to `404` and `503`
- failed submissions do not persist binding metadata
- normalized SSE emits top-level `artifact_url`, intervention `fingerprint`, and stable `run_failed` text

## Recommended File Change Surface

### New Files

- `backend/src/gateway/routers/runtime.py`
- `backend/src/gateway/runtime_service.py`
- `backend/tests/test_runtime_router.py`

### Modified Files

- `backend/src/gateway/app.py`
- `backend/src/gateway/thread_registry.py`
- `backend/tests/test_multi_tenant.py`

## Backend Acceptance Criteria

1. Runtime adapter endpoints exist exactly as specified in the feature doc.
2. `ThreadRegistry` stores platform binding metadata without breaking existing ownership checks.
3. `allowed_agents` is validated against tenant-scoped agent storage.
4. `entry_agent` cannot escape the allowlist.
5. External platform calls do not need to understand raw `/api/langgraph` protocol details.
6. Existing DeerFlow frontend chat path still works unchanged.

## Follow-up: allowed_agents Runtime Filtering + Batch Sync

Completed on `2026-04-02`. These items were identified as gaps during progress review and are logically part of the same feature surface.

### 8. allowed_agents Planner/Router Filtering

- [x] Add `allowed_agents` parameter to `list_domain_agents()` in `src/config/agents_config.py`
- [x] Case-insensitive set-based filtering
- [x] Wire `configurable["allowed_agents"]` into `planner_node` in `src/agents/planner/node.py`
- [x] Wire `configurable["allowed_agents"]` into `router_node` in `src/agents/router/semantic_router.py`
- [x] Wire `allowed_agents` into `_get_helper_candidates()` (signature + 2 call sites)
- [x] Executor unchanged — routes only to `task.assigned_agent` which is already in allowlist

### 9. Batch Agent Sync Endpoint

- [x] Add `POST /api/agents/sync` in `src/gateway/routers/agents.py`
- [x] `AgentSyncItem` / `AgentSyncRequest` / `AgentSyncResponse` Pydantic models
- [x] `upsert` mode: create new, update existing, leave others untouched
- [x] `replace` mode: additionally delete agents not in list
- [x] Up-front name validation and duplicate detection (422)
- [x] Per-agent error isolation: single failure does not abort batch
- [x] All `AgentConfig` fields supported (domain, engine_type, mcp_binding, etc.)

### 10. Tests

- [x] `TestListDomainAgentsAllowedFilter` (6 tests): filter, case-insensitive, empty list, None, unknown ignored
- [x] `TestAgentSyncAPI` (9 tests): upsert create/update, replace delete, duplicate reject, invalid name, empty list, field preservation, case normalization

### Follow-up Verification Notes

Verified on `2026-04-02` with:

- `backend/tests/test_custom_agent.py` — 70/70 passed (15 new)
- Full suite: 1364 passed, 0 regressions from these changes

## Related Docs

- [platform-deerflow-runtime-backend-integration.md](E:/work/deer-flow/collaboration/features/platform-deerflow-runtime-backend-integration.md)
- [platform-deerflow-runtime-backend-integration-test-checklist.md](E:/work/deer-flow/collaboration/features/platform-deerflow-runtime-backend-integration-test-checklist.md)
