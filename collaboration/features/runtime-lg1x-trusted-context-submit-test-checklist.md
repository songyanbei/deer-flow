# Runtime LG1.x Trusted Context Submit Test Checklist

- Audience: `test`
- Status: `ready`
- Parent spec: `collaboration/features/runtime-lg1x-trusted-context-submit.md`
- Development checklist: `collaboration/features/runtime-lg1x-trusted-context-submit-development-checklist.md`
- Goal: verify runtime submit channel compatibility and tenant/user/thread isolation across Gateway, frontend, and subagent paths

## Test Scope

### In Scope

1. Gateway context-only remote submit.
2. Trusted `thread_context` / `auth_user` propagation.
3. Subagent child run isolation.
4. Gateway thread binding lifecycle.
5. Main chat migration from browser `/api/langgraph` to Gateway.
6. Resume/governance migration after dedicated endpoints exist.
7. Cross-tenant/user/thread security regressions.

### Out Of Scope

1. Pixel-perfect frontend visual QA.
2. LangGraph internals beyond observable channel behavior.
3. Resume/governance Gateway migration before Phase 2 endpoints exist.
4. Testing direct browser `/api/langgraph` retirement before Phase 2.

## Phase Gates

### Phase 0 Gate: Backend Runtime Unblock

Phase 0 can be accepted when:

- [ ] Gateway `/api/runtime/threads/{id}/messages:stream` no longer throws LG1.x dual-channel 400.
- [ ] Gateway remote submit does not pass non-empty `config.configurable`.
- [ ] Gateway remote submit passes `context.thread_context`.
- [ ] Subagent child run receives parent `thread_context`.
- [ ] Existing SSO/OIDC auth behavior is unchanged.

### Phase 1 Gate: Main Chat Through Gateway

Phase 1 can be accepted when:

- [ ] Frontend main chat thread is created or registered in Gateway before first message.
- [ ] Normal chat first submit succeeds through Gateway.
- [ ] Agent chat first submit succeeds through Gateway.
- [ ] Browser Network shows no `/api/langgraph/*` run submit for main chat.
- [ ] Intervention resume and governance resume are explicitly allowed to remain on `/api/langgraph` during Phase 1.
- [ ] Phase 1 exit is recorded as conditional if intervention decisions are
  still on `/api/langgraph`: this is not a Phase 1 scope failure, but it is
  the first Phase 2 blocker because stale `liveValuesPatch` can keep the
  waiting card visible after resolve.
- [ ] Cross-tenant/user thread submit still returns 403.

### Phase 2 Gate: Resume And Governance Through Gateway

Phase 2 can be accepted when:

- [ ] Intervention resume uses Gateway resume endpoint.
- [ ] Governance resume uses Gateway governance endpoint.
- [ ] Browser Network shows no `/api/langgraph/*` for migrated resume/governance flows.
- [ ] Resume continues interrupted work instead of starting unrelated new message flow.
- [ ] Submitting an intervention decision clears the inline waiting card
  without a page refresh.
- [ ] Workflow `task_pool` / `workflow_stage` continue updating through
  Gateway SSE after intervention resume.
- [ ] Governance audit/history remains correct.
- [ ] `/api/langgraph` browser exposure is removed or restricted after frontend no longer depends on it.

### Phase 3 Gate: Hardening

Phase 3 can be accepted when:

- [ ] Debug LG error mode works only when explicitly enabled.
- [ ] Production default remains sanitized.
- [ ] LangGraph dependency notes exist.
- [ ] Probe scripts are documented.

## Required Automated Tests

### Backend Unit / Integration

- [ ] `backend/tests/test_runtime_router.py` or new `test_runtime_service_channels.py`
  - [ ] asserts `start_stream` passes `config={"recursion_limit": ...}` without `configurable`.
  - [ ] asserts `context` contains `thread_context`, `auth_user`, `tenant_id`, `user_id`, `thread_id`.
  - [ ] asserts `context["thread_context"]` came from `resolve_thread_context(...)`.
  - [ ] asserts client-supplied identity fields are ignored or rejected.
- [ ] Subagent propagation test
  - [ ] parent `task_tool` reads `thread_context` from parent runtime config.
  - [ ] child `SubagentExecutor` writes it into child `run_config.configurable`.
  - [ ] child `ThreadDataMiddleware` resolves the same `ThreadContext`.
  - [ ] child `identity_guard` receives parent `auth_user`.
- [ ] Access control tests
  - [ ] unknown thread returns 403 or expected fail-closed response.
  - [ ] cross-tenant thread access returns 403.
  - [ ] cross-user thread access returns 403.
  - [ ] missing auth user under OIDC/SSO returns 401/403.

### Frontend / Contract Tests

- [ ] Main chat submit adapter tests
  - [ ] normal chat calls Gateway `messages:stream`.
  - [ ] agent chat calls Gateway `messages:stream`.
  - [ ] request body does not include `thread_context`.
  - [ ] request body does not include `auth_user`.
  - [ ] request body does not include bare `tenant_id` or `user_id`.
- [ ] Thread lifecycle tests
  - [ ] new chat obtains or registers a Gateway-bound thread id before first stream.
  - [ ] failed thread binding surfaces a usable UI error.
- [ ] Phase 1 non-regression tests
  - [ ] intervention resume still uses existing path until Phase 2.
  - [ ] governance resume still uses existing path until Phase 2.

### Phase 2 Tests

- [ ] Resume endpoint backend tests
  - [ ] accepts required checkpoint/command/workflow resume fields.
  - [ ] rejects or ignores identity fields from client.
  - [ ] resolves thread ownership before upstream submit.
  - [ ] submits context-only to LangGraph API.
- [ ] Governance endpoint backend tests
  - [ ] preserves `run_id`.
  - [ ] preserves `task_id`.
  - [ ] preserves governance audit/history.
  - [ ] rejects cross-tenant/user resume.
- [ ] Frontend resume tests
  - [ ] `intervention-card.tsx` calls Gateway resume endpoint.
  - [ ] `governance/utils.ts` calls Gateway governance endpoint.
  - [ ] browser no longer calls `/api/langgraph/*` for these flows after Phase 2.

## Required Manual / E2E Checks

### Backend Smoke

- [ ] Run `backend/scripts/sso_e2e_smoke.py`.
- [ ] Confirm Phase 3 stream reaches `run_completed` or expected successful terminal event.
- [ ] Confirm no `BadRequestError: Cannot specify both configurable and context`.
- [ ] Confirm no `ThreadDataMiddleware: configurable['thread_context'] is required but missing` on migrated paths.

### Browser Phase 1 Smoke

- [ ] Start a new normal chat.
- [ ] Send "你好".
- [ ] Confirm response streams and completes.
- [ ] Confirm browser Network main chat submit uses `/api/runtime/threads/{id}/messages:stream`.
- [ ] Confirm browser Network main chat submit does not use `/api/langgraph/*`.
- [ ] Start a new agent chat.
- [ ] Confirm `agent_name` behavior is preserved.
- [ ] Trigger a workflow/task event if possible and confirm UI still renders expected state.
- [ ] Confirm intervention resume still works on its old path or is explicitly deferred.
- [ ] Confirm governance resume still works on its old path or is explicitly deferred.

### Browser Phase 2 Smoke

- [ ] Trigger an intervention requiring resume.
- [ ] Submit resume from intervention card.
- [ ] Confirm it resumes the interrupted run.
- [ ] Confirm the waiting intervention card disappears without refreshing.
- [ ] Confirm workflow footer/task panel advance from the resumed
  `task_pool` / `workflow_stage` stream.
- [ ] Confirm no `/api/langgraph/*` request is made for intervention resume.
- [ ] Trigger governance resume.
- [ ] Confirm governance resume completes.
- [ ] Confirm no `/api/langgraph/*` request is made for governance resume.

### Isolation Manual Checks

- [ ] Tenant A cannot submit to Tenant B thread.
- [ ] User A cannot submit to User B thread in same tenant.
- [ ] Browser-modified request containing fake `thread_context` is ignored or rejected.
- [ ] Browser-modified request containing fake `auth_user` is ignored or rejected.
- [ ] Upload/artifact paths after migrated submit remain under expected tenant/user/thread scope.
- [ ] Sandbox paths after migrated submit remain under expected tenant/user/thread scope.
- [ ] Memory paths after migrated submit remain under expected tenant/user/thread scope.

## Recommended Command Set

Run at minimum after Phase 0:

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_runtime_router.py
.venv/Scripts/python -m pytest tests/test_sso_*.py tests/test_oidc_*.py
.venv/Scripts/python -m scripts.sso_e2e_smoke
```

Run after subagent changes:

```bash
cd backend
.venv/Scripts/python -m pytest tests -k "subagent or task_tool or thread_context"
```

Run after frontend Phase 1:

```bash
cd frontend
pnpm test
pnpm lint
```

If commands differ in the local environment, record the actual commands and results in the PR or test execution notes.

## Negative Test Cases

- [ ] Remote Gateway submit with both `config.configurable` and `context` should be caught by unit test.
- [ ] Frontend request with fake `tenant_id` should not change backend tenant.
- [ ] Frontend request with fake `user_id` should not change backend user.
- [ ] Frontend request with fake `thread_context` should not be trusted.
- [ ] Frontend request with fake `auth_user` should not be trusted.
- [ ] Subagent run without parent `thread_context` should fail closed rather than silently using default tenant/user.
- [ ] Resume payload sent to `messages:stream` should not be considered a valid Phase 2 implementation.

## Regression Areas

- [ ] Uploads middleware still sees correct `ThreadContext`.
- [ ] Artifacts URLs still resolve for migrated runtime-created threads.
- [ ] Sandbox middleware still uses correct tenant/user/thread path.
- [ ] Memory middleware still scopes by correct tenant/user/thread.
- [ ] `identity_guard` still blocks identity field rewrite.
- [ ] Planner/router build-time config still sees `mode`, `agent_name`, `allowed_agents`, `requested_orchestration_mode`.
- [ ] Existing direct LangGraph tests remain intentionally scoped or are updated to Gateway path according to phase.

## Test Completion Criteria

- [ ] Every completed development phase has matching automated or manual evidence.
- [ ] Any deferred Phase 2 behavior is explicitly marked as deferred, not failed.
- [ ] No migrated browser path trusts client-provided identity.
- [ ] No migrated remote submit path triggers LG1.x dual-channel 400.
- [ ] No migrated path loses `thread_context`.
- [ ] Final Phase 2 evidence shows browser `/api/langgraph` direct run submit is gone.
