# Platform DeerFlow Runtime Backend Integration Test Checklist

- Audience: `test`
- Status: `partially verified`
- Last aligned with spec: `2026-04-01`
- Goal: verify that the new Gateway runtime adapter is stable, tenant-safe, and platform-consumable

## Test Scope

### In Scope

1. runtime thread creation contract
2. thread binding metadata persistence
3. `allowed_agents` validation and propagation
4. runtime ownership enforcement
5. SSE event contract normalization
6. regression coverage for existing upload/artifact/intervention/governance compatibility

### Out Of Scope

1. DeerFlow frontend visual testing
2. external platform frontend testing
3. Keycloak audience tightening
4. real multi-tenant claim rollout

## Required Test Files

### New Tests

- [x] `backend/tests/test_runtime_router.py`

Recommended sections:

1. `POST /api/runtime/threads`
2. `GET /api/runtime/threads/{thread_id}`
3. `POST /api/runtime/threads/{thread_id}/messages:stream`
4. payload validation failures
5. OIDC/identity usage
6. upstream LangGraph failure handling
7. normalized SSE contract

### Existing Tests To Extend

- [x] `backend/tests/test_multi_tenant.py`
  - extend thread registry assertions to cover metadata object storage and backward-compatible string entries
- [ ] `backend/tests/test_uploads_router.py`
  - verify runtime-created threads still work with upload APIs
- [ ] `backend/tests/test_interventions_router.py`
  - verify runtime-created threads still work with intervention resolution flow
- [ ] `backend/tests/test_governance_api.py`
  - verify governance APIs still work on runtime-created threads

## Functional Test Matrix

### 1. Thread Creation

- [x] valid token + valid `portal_session_id` returns `200/201`
- [x] response contains `thread_id`
- [x] response contains `portal_session_id`
- [x] response contains `tenant_id`
- [x] response contains `user_id`
- [x] thread is persisted in registry

### 2. Thread Read / Snapshot

- [x] owner can fetch thread binding
- [x] foreign tenant gets `403`
- [x] cross-owner access gets `403`
- [x] unknown thread gets `404`
- [ ] state summary fields are present even when values are partially missing

### 3. Message Stream Validation

- [x] blank `message` -> `422`
- [x] blank `group_key` -> `422`
- [x] empty `allowed_agents` -> `422`
- [x] duplicate `allowed_agents` are normalized correctly
- [x] unknown agent name -> `422`
- [ ] blank agent name -> `422`
- [x] `entry_agent` not in `allowed_agents` -> `422`
- [x] invalid `requested_orchestration_mode` -> `422`
- [ ] non-object `metadata` -> `422`
- [x] nested non-primitive `metadata` values -> `422`

### 4. Message Stream Success Path

- [x] valid request triggers upstream runtime submission
- [ ] upstream context contains `thread_id`
- [ ] upstream context contains `tenant_id`
- [ ] upstream context contains `user_id`
- [ ] upstream context contains `allowed_agents`
- [ ] upstream context contains `group_key`
- [ ] upstream context contains `requested_orchestration_mode`
- [ ] upstream context contains `agent_name` only when `entry_agent` is supplied

### 5. Thread Metadata Persistence

- [x] successful message submission updates `group_key`
- [x] successful message submission updates `allowed_agents`
- [x] successful message submission updates `entry_agent`
- [x] successful message submission updates `requested_orchestration_mode`
- [x] `updated_at` changes on successful submission
- [x] failed message submission does not update binding metadata

### 6. SSE Event Contract

- [x] adapter emits only normalized external event names
- [x] `ack` includes `thread_id`
- [ ] `message_delta` includes `thread_id`
- [ ] `message_completed` includes `thread_id`
- [x] `artifact_created` includes `artifact_url` when available
- [x] `intervention_requested` includes `request_id` and `fingerprint` when available
- [ ] `governance_created` includes `governance_id` when available
- [ ] `run_completed` includes `thread_id`
- [x] `run_failed` includes stable error text
- [x] raw upstream event names are not leaked as external contract

### 7. Auth / Access Control

- [ ] no token -> `401`
- [ ] invalid token -> `401`
- [x] cross-tenant thread access -> `403`
- [x] cross-owner thread access -> `403`
- [ ] valid token from `moss` realm can access runtime endpoints

### 8. Compatibility Regression

- [ ] runtime-created thread can upload files via existing uploads API
- [ ] uploaded files remain readable via existing artifacts API
- [ ] intervention resolution still works on runtime-created thread
- [ ] governance queue/detail/resolve still works on runtime-created thread
- [ ] existing DeerFlow frontend `/api/langgraph` regression suite remains green

## Recommended Command Set

After implementation, at minimum run:

- [x] `backend/tests/test_runtime_router.py`
- [x] `backend/tests/test_multi_tenant.py`
- [x] `backend/tests/test_uploads_router.py`
- [x] `backend/tests/test_interventions_router.py`
- [x] `backend/tests/test_governance_api.py`
- [x] `backend/tests/test_oidc_middleware.py`

Additional verification run:

- [x] `backend/tests/test_client.py`

## Verification Notes

Verified on `2026-04-01`.

Automated suites run:

- `backend/tests/test_runtime_router.py`
- `backend/tests/test_multi_tenant.py`
- `backend/tests/test_uploads_router.py`
- `backend/tests/test_interventions_router.py`
- `backend/tests/test_governance_api.py`
- `backend/tests/test_oidc_middleware.py`
- `backend/tests/test_client.py`

Additional targeted retest confirmed:

- unknown `allowed_agents` returns `422`
- cross-owner runtime access returns `403`
- upstream thread state failures map to `404` and `503`
- failed runtime submissions do not mutate thread binding metadata
- normalized SSE emits top-level `artifact_url`, intervention `fingerprint`, and stable `run_failed` error text

Still recommended:

- add explicit compatibility tests for runtime-created threads across uploads / interventions / governance
- add dedicated OIDC middleware integration tests for runtime endpoints

## Test Acceptance Criteria

1. A tester can validate the complete runtime adapter contract from tests without reading implementation internals.
2. Ownership and tenant enforcement remain correct after extending `ThreadRegistry`.
3. `allowed_agents` semantics are executable, not only documented.
4. Existing DeerFlow operational APIs remain compatible with runtime-created threads.
5. No regression appears in DeerFlow's own `/api/langgraph` frontend path.

## Related Docs

- [platform-deerflow-runtime-backend-integration.md](E:/work/deer-flow/collaboration/features/platform-deerflow-runtime-backend-integration.md)
- [platform-deerflow-runtime-backend-integration-backend-checklist.md](E:/work/deer-flow/collaboration/features/platform-deerflow-runtime-backend-integration-backend-checklist.md)
