# Runtime LG1.x Trusted Context Submit Development Checklist

- Audience: `development`
- Status: `ready`
- Parent spec: `collaboration/features/runtime-lg1x-trusted-context-submit.md`
- Bug report: `collaboration/bugs/runtime-dual-context-configurable-lg1x-regression.md`
- Goal: implement trusted Gateway submit paths without weakening tenant/user/thread isolation

## Development Scope

### In Scope

1. Gateway remote LangGraph submit channel fix.
2. Subagent parent context propagation.
3. Gateway-backed thread binding lifecycle for frontend main chat.
4. Frontend main chat submit migration to Gateway.
5. Resume/governance Gateway endpoints and frontend migration in a later phase.
6. Observability and LangGraph channel behavior documentation.

### Out Of Scope

1. Letting frontend create or pass `thread_context` / `auth_user`.
2. Migrating local pregel (`DeerFlowClient` / `SubagentExecutor`) to context-only.
3. Replacing `ThreadDataMiddleware` with a bare `tenant_id/user_id/thread_id` fallback.
4. Downgrading LangGraph as a long-term fix.
5. Moving intervention resume / governance resume through `messages:stream`.

## Hard Rules

- Browser-provided identity is never trusted.
- `thread_context` comes from Gateway `resolve_thread_context(...)` or parent run trusted config.
- `auth_user` comes from OIDC/SSO request state or parent run trusted config.
- Remote LangGraph API submit must not send non-empty `config.configurable` and non-empty `context` together.
- Local pregel paths may keep dual channel and must keep `configurable["thread_context"]`.

## Phase 0: Backend Runtime Unblock

### D0.1 Gateway `start_stream` context-only

Owner: Backend

Files:

- `backend/src/gateway/runtime_service.py`
- `backend/src/gateway/routers/runtime.py`

Tasks:

- [ ] Remove `configurable` construction/injection from `runtime_service.start_stream`.
- [ ] Keep `run_config = {"recursion_limit": 1000}` only.
- [ ] Call `client.runs.stream(..., config=run_config, context=dict(context), ...)`.
- [ ] Do not mutate caller-provided `context`.
- [ ] Keep router-side `resolve_thread_context()` and `auth_user` construction as the only identity source.
- [ ] Keep `ThreadDataMiddleware` behavior unchanged.

Functional Boundary:

- This task only changes Gateway `/api/runtime/threads/{id}/messages:stream` upstream submit kwargs.
- It does not change frontend `/api/langgraph` direct calls.
- It does not change local pregel submit behavior.

Developer Acceptance:

- [ ] Local inspection shows `client.runs.stream` in `start_stream` cannot receive `config["configurable"]`.
- [ ] Gateway context still includes `thread_context`, `auth_user`, `tenant_id`, `user_id`, `thread_id`.
- [ ] `sso_e2e_smoke.py` Phase 3 can reach upstream without LG1.x dual-channel 400.

### D0.2 Subagent trusted context propagation

Owner: Backend

Files:

- `backend/src/tools/builtins/task_tool.py`
- `backend/src/subagents/executor.py`

Tasks:

- [ ] In `task_tool.py`, read `thread_context` from `runtime.config["configurable"]`.
- [ ] In `task_tool.py`, keep existing `auth_user` extraction and pass both `thread_context` and `auth_user` to `SubagentExecutor`.
- [ ] Add optional `thread_context: dict | None = None` and `auth_user: dict | None = None` to `SubagentExecutor.__init__`.
- [ ] Store these values as `self.thread_context` and `self.auth_user`.
- [ ] When building child `run_config.configurable`, include `thread_context` and `auth_user` when present.
- [ ] Keep `thread_id`, `tenant_id`, and `user_id` in both configurable/context as today.
- [ ] Keep `agent.stream(state, config=run_config, context=context, ...)` dual channel for local pregel.

Functional Boundary:

- This is a field propagation fix, not a channel migration.
- Do not synthesize `thread_context` from loose ids if parent trusted context is missing.
- If parent trusted context is missing, fail closed or leave existing failure visible.

Developer Acceptance:

- [ ] Child `ThreadDataMiddleware` receives the same serialized `ThreadContext` as parent.
- [ ] Child tools remain wrapped with `identity_guard` using parent `auth_user`.
- [ ] No change is made to `DeerFlowClient` channel model.

### D0.3 Backend regression tests

Owner: Backend / Test-supporting developer

Files:

- `backend/tests/test_runtime_router.py` or new `backend/tests/test_runtime_service_channels.py`
- Subagent-focused backend test file, existing or new

Tasks:

- [ ] Add a test asserting Gateway remote submit does not pass `configurable`.
- [ ] Add a test asserting Gateway remote submit passes trusted `thread_context` inside `context`.
- [ ] Add a subagent propagation test around `task_tool` / `SubagentExecutor`.

Developer Acceptance:

- [ ] Tests fail on the old implementation and pass after D0.1/D0.2.

## Phase 1: Frontend Main Chat Through Gateway

Phase 1 migrates only normal main chat / agent chat submit. Intervention resume and governance resume remain on `/api/langgraph` until Phase 2.

### D1.1 Thread binding lifecycle

Owner: Frontend + Backend

Decision Required:

- [ ] Choose Option A or Option B before coding.

Option A, recommended:

- Frontend calls `POST /api/runtime/threads` before first message.
- Gateway creates LangGraph thread and registers `ThreadRegistry` binding.
- Frontend navigates using Gateway-returned `thread_id`.
- `use-thread-chat.ts` no longer treats local `uuid()` as authoritative for Gateway-backed chats.

Option B:

- Gateway adds `POST /api/runtime/threads/{id}:adopt` or equivalent.
- Frontend keeps current generated id flow, but calls adopt before first Gateway stream.
- Gateway validates ownership and writes `ThreadRegistry` binding.

Files likely touched:

- `frontend/src/components/workspace/chats/use-thread-chat.ts`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- `backend/src/gateway/routers/runtime.py` if Option B is chosen

Functional Boundary:

- This task only ensures thread ids submitted to Gateway are registered.
- It does not migrate resume/governance.

Developer Acceptance:

- [ ] New normal chat first submit does not 403 due to missing registry binding.
- [ ] New agent chat first submit does not 403 due to missing registry binding.
- [ ] Cross-tenant or cross-user thread adoption remains impossible.

### D1.2 Main chat Gateway submit adapter

Owner: Frontend

Files:

- `frontend/src/core/threads/hooks.ts`
- Any small helper/client module introduced for Gateway runtime SSE

Tasks:

- [ ] Replace main chat `thread.submit(...)` path with `POST /api/runtime/threads/{id}/messages:stream`.
- [ ] Map the prompt text into `MessageStreamRequest.message`.
- [ ] Map settings into `group_key`, `allowed_agents`, `entry_agent`, `requested_orchestration_mode`, and primitive `metadata`.
- [ ] Preserve upload flow before submit.
- [ ] Parse Gateway SSE events and update the same UI-facing state used by message list, workflow footer, tasks, todos, title/run state.
- [ ] Do not send `thread_context`, `auth_user`, `tenant_id`, or `user_id` from frontend.

Functional Boundary:

- Scope includes normal chat, agent chat, and bootstrap only through `hooks.ts`.
- Scope excludes `intervention-card.tsx`.
- Scope excludes `governance/utils.ts`.

Developer Acceptance:

- [ ] Normal chat submit uses `/api/runtime/threads/{id}/messages:stream`.
- [ ] Agent chat submit uses `/api/runtime/threads/{id}/messages:stream`.
- [ ] Browser Network has no `/api/langgraph/*` run submit for main chat.
- [ ] Intervention/governance resume may still use `/api/langgraph/*` and this is expected in Phase 1.
- [ ] Fake browser identity fields are not sent and cannot affect backend identity.

### D1.3 Gateway event parity fixes

Owner: Backend or Frontend depending on missing field

Trigger:

- Run this only if D1.2 finds Gateway SSE cannot drive existing UI.

Tasks:

- [ ] Frontend records missing field/event in `collaboration/handoffs/frontend-to-backend.md`.
- [ ] Backend extends Gateway event projection or explains the supported alternative.
- [ ] Final event contract is copied back to parent spec.

Developer Acceptance:

- [ ] Main chat UI can render messages, task events, workflow state, completion, and errors from Gateway SSE.

## Phase 2: Resume And Governance Through Gateway

Phase 2 starts with intervention resume as the P0 item. Phase 1 intentionally
left `intervention-card.tsx` on `/api/langgraph`, but the Gateway-first live
state model now means that path can leave stale `liveValuesPatch.task_pool` /
`workflow_stage` in the UI after a decision is submitted. Backend should build
the Gateway resume stream first; frontend should then switch
`InterventionCard` to that stream before governance migration.

### D2.1 Backend resume endpoint

Owner: Backend

Files:

- `backend/src/gateway/routers/runtime.py`
- `backend/src/gateway/runtime_service.py`

Tasks:

- [ ] Add `POST /api/runtime/threads/{id}/resume`.
- [ ] Add a request model supporting `checkpoint`, resume command fields, workflow resume flags, run id, task id, and resume message.
- [ ] Support the existing `InterventionCard` resume response contract:
  `checkpoint`, `resume_payload.message`, `workflow_clarification_resume`,
  `workflow_resume_run_id`, and `workflow_resume_task_id`.
- [ ] Resolve thread ownership before upstream submit.
- [ ] Inject trusted `thread_context` and `auth_user`.
- [ ] Submit to LangGraph API with context-only remote channel.
- [ ] Stream normalized Gateway SSE events with the same `state_snapshot` /
  task projection used by `messages:stream`.
- [ ] Reject or ignore client identity fields.

Functional Boundary:

- This endpoint is for intervention/interrupt resume, not normal new messages.

Developer Acceptance:

- [ ] Existing intervention resume can continue interrupted work instead of starting a new message flow.
- [ ] Submitting a waiting intervention decision clears the waiting card
  without a browser refresh.
- [ ] Workflow task execution continues and emits updated `task_pool` /
  `workflow_stage` through Gateway SSE.
- [ ] Cross-tenant/user resume returns 403.

### D2.2 Backend governance resume endpoint

Owner: Backend

Files:

- `backend/src/gateway/routers/runtime.py`
- governance-related backend modules/tests as needed

Tasks:

- [ ] Add `POST /api/runtime/threads/{id}/governance:resume` or agreed equivalent.
- [ ] Preserve governance queue/history/audit semantics.
- [ ] Use the same trusted context injection and context-only upstream submit.

Developer Acceptance:

- [ ] Governance resume keeps correct `run_id` and `task_id`.
- [ ] Governance audit/history remains correct.
- [ ] Cross-tenant/user governance resume returns 403.

### D2.3 Frontend resume migration

Owner: Frontend

Priority note: migrate `InterventionCard` first. It must stop calling
`thread.submit(...)` for `resume_action="submit_resume"` and instead consume
the Gateway resume stream so the same `liveValuesPatch` path as main chat is
updated. Acceptance includes the waiting card disappearing and workflow
progress continuing without a page refresh.

Files:

- `frontend/src/components/workspace/messages/intervention-card.tsx`
- `frontend/src/core/governance/utils.ts`

Tasks:

- [ ] Switch intervention resume from `thread.submit(...)` to Gateway resume endpoint.
- [ ] Switch governance resume from `client.runs.create(...)` to Gateway governance resume endpoint.
- [ ] Preserve current UI loading/error behavior.
- [ ] Remove browser direct `/api/langgraph` dependency for these two flows.

Functional Boundary:

- This task runs only after D2.1/D2.2 endpoints exist.

Developer Acceptance:

- [ ] Browser Network shows no `/api/langgraph/*` for intervention resume.
- [ ] Browser Network shows no `/api/langgraph/*` for governance resume.
- [ ] Both resume flows still complete end to end.

### D2.4 Nginx exposure hardening

Owner: Backend / DevOps

Files:

- `templates/nginx.offline-runtime.conf.template`
- `docker/nginx/nginx.offline.conf`
- `docker/nginx/nginx.conf`
- `docker/nginx/nginx.local.conf`

Tasks:

- [x] Restrict `/api/langgraph` from browser access after frontend no longer depends on it.
- [x] Keep Gateway-to-LangGraph server access working.

Implementation:

- Added `map $host $deer_block_langgraph_browser { default <0|1>; }` at `http`
  level in each config, plus an `if ($deer_block_langgraph_browser = 1) { return 404; }`
  guard at the top of the `/api/langgraph/` location.
- Protected/offline deployments (`nginx.offline-runtime.conf.template`,
  `nginx.offline.conf`) default to `1` (blocked).
- Dev/default deployments (`nginx.conf`, `nginx.local.conf`) default to `0`
  (open) so the frontend's remaining `client.threads.getState()` calls still
  work during migration; a single-line edit flips them to blocked.
- Gateway → LangGraph traffic uses `LANGGRAPH_URL` (direct to `127.0.0.1:2024`
  or `langgraph:2024`), bypassing nginx entirely, so the gate never affects
  Gateway's `/api/runtime/*` streaming.

Developer Acceptance:

- [x] Browser cannot call `/api/langgraph/*` in protected deployment.
- [x] Gateway runtime still streams successfully.
- [x] Regression test `backend/tests/test_nginx_langgraph_browser_gate.py`
  statically verifies (a) the `map` directive is present with the expected
  default per deployment type, (b) the `if ... return 404;` guard is present
  inside `/api/langgraph/`, and (c) `/api/runtime` is **not** behind the gate.

## Phase 3: Hardening

### D3.1 Debuggability

Owner: Backend

Tasks:

- [ ] Add `GATEWAY_DEBUG_LG_ERRORS=true` or equivalent around sanitized LangGraph errors.
- [ ] Keep production default sanitized.

Acceptance:

- [ ] Debug mode exposes raw LG channel errors.
- [ ] Default mode does not leak internals.

### D3.2 Dependency and probe docs

Owner: Backend

Tasks:

- [ ] Add LangGraph dependency comment warning not to send both `configurable` and `context`.
- [ ] Document `backend/scripts/_probe_channels.py`.
- [ ] Document `backend/scripts/probe_lg_channels.py`.
- [ ] Document `backend/scripts/probe_local_pregel.py`.

Acceptance:

- [ ] A future upgrader can rerun probes without reopening the original investigation.

## Development Completion Criteria

- [ ] Phase 0 complete and backend tests pass.
- [ ] Phase 1 complete and main chat no longer browser-submits to `/api/langgraph`.
- [ ] Phase 2 complete before claiming browser `/api/langgraph` is fully retired.
- [ ] Phase 3 complete before closing the feature as `done`.
- [ ] Any unresolved cross-side contract issue is recorded in `collaboration/handoffs/`.
