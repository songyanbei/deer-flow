# Runtime LG1.x Trusted Context Submit

- Status: `ready-for-development-and-test`
- Owner suggestion: Backend runtime owner + Frontend chat owner + Test owner
- Related area: `backend/src/gateway`, `backend/src/subagents`, `frontend/src/core/threads`, `frontend/src/components/workspace/messages`, `frontend/src/core/governance`
- Source bug report: `collaboration/bugs/runtime-dual-context-configurable-lg1x-regression.md`
- Development checklist: `collaboration/features/runtime-lg1x-trusted-context-submit-development-checklist.md`
- Test checklist: `collaboration/features/runtime-lg1x-trusted-context-submit-test-checklist.md`

## Goal

修复 LangGraph 1.x 下 runtime run submit 的上下文通道问题，同时保持 `tenant_id / user_id / thread_id` 数据隔离不被削弱。

最终目标：

- Gateway `/api/runtime/*` 远程 LangGraph server 路径不再同传 `config.configurable` 与 `context`，避免 LG1.x 400。
- 所有受保护运行提交的 `thread_context` / `auth_user` 都由 Gateway 可信注入，浏览器不能伪造。
- 网页主聊天先迁到 Gateway submit 入口，resume / governance 在专用 endpoint 完成后再迁。
- 本地 pregel 路径不迁通道模型，只补 subagent 缺失的 `thread_context` / `auth_user`。

## Why This Needs Frontend/Backend Collaboration

这是跨前后端信任边界修复，不是单纯后端参数调整：

- Backend 负责认证、thread ownership 校验、`thread_context` 注入、LangGraph submit 通道。
- Frontend 当前直接使用 LangGraph SDK `useStream` / `thread.submit` / `client.runs.create`，需要逐步迁到 Gateway-owned endpoint。
- Resume / governance 的 payload 语义不同于普通 message submit，不能直接复用现有 `messages:stream`。
- Thread lifecycle 当前由前端本地 `uuid()` / LangGraph SDK 创建，迁 Gateway 前必须先解决 Gateway `ThreadRegistry` binding。

## Current Behavior

### Backend

- `backend/src/gateway/runtime_service.py::start_stream()` 当前向 LangGraph SDK 同时传 `config.configurable` 和 `context`，在 `langgraph-api 0.7.65` 下直接 400。
- Gateway router 已经能通过 `resolve_thread_context()` 生成可信 `thread_context`，并把它放进 `context`。
- `ThreadDataMiddleware` 仍以 `config.configurable["thread_context"]` 为权威来源；远程 LangGraph API 在 context-only 时会把 context mirror 到 configurable。
- `SubagentExecutor` 是本地 pregel 路径，不触发 LG API 双通道 400，也不会自动 mirror context/configurable；当前缺 `configurable["thread_context"]`。

### Frontend

- 主聊天、agent chat、bootstrap 通过 `frontend/src/core/threads/hooks.ts` 的 `thread.submit(...)` 直连 `/api/langgraph`。
- Intervention resume 通过 `frontend/src/components/workspace/messages/intervention-card.tsx` 的 `thread.submit(...)` 直连 `/api/langgraph`。
- Governance resume 通过 `frontend/src/core/governance/utils.ts` 的 `client.runs.create(...)` 直连 `/api/langgraph`。
- 新会话当前由前端本地 `uuid()` 和 LangGraph SDK lifecycle 驱动；Gateway `messages:stream` 会先查 `ThreadRegistry`，未注册 thread 会 403。

## Global Invariants

所有阶段都必须保持这些不变量：

- `thread_context` 只能来自 Gateway 的 `resolve_thread_context(thread_id, tenant_id, user_id)` 或父 run 已验证上下文。
- `auth_user` 只能来自 OIDC / SSO request state 或父 run 已验证配置。
- 前端不得提交、拼接或透传 `thread_context` / `auth_user` / 裸 `tenant_id` / 裸 `user_id` 作为身份凭证。
- OIDC/SSO 开启时不能 fallback 到 `default` / `anonymous`。
- 跨 tenant、跨 user、跨 thread 必须 fail-closed。

## Contract To Confirm First

### D1: Thread Lifecycle Route

Phase 1 之前必须在 PR 内明确选一种：

- Option A: 前端新建会话先调用 `POST /api/runtime/threads`，使用 Gateway 返回的 `thread_id`，再进入聊天页。
- Option B: Gateway 新增 `POST /api/runtime/threads/{id}:adopt` 或等价 endpoint，用于把现有 LangGraph thread id 绑定进 Gateway `ThreadRegistry`。

推荐：Option A。它让 thread id 从一开始就由 Gateway 创建并登记，信任边界最清晰。

Phase 1 主聊天迁入时的必填字段决议：

- 当前 Gateway runtime 契约涉及两个必填集合：`POST /api/runtime/threads` 需要 `portal_session_id`，`POST /api/runtime/threads/{id}/messages:stream` 需要 `group_key` / `allowed_agents`；而 DeerFlow 主聊天当前没有稳定的 platform session / group / allowed agents 概念。
- MR 内必须拍板其中一种，不得跨 MR 悬置：
  - **方案 α（推荐）**：后端把这些字段改为可选，并由 Gateway 为 DeerFlow 主聊天填安全默认值。例如 `portal_session_id = "deerflow-web:{thread_id}"`，`group_key = "default"` 或 settings 派生值，`allowed_agents = tenant/user 可见 agent 集合`。
  - **方案 β**：前端在创建/提交前从现有 settings 或 agent registry 明确生成这些字段，但仍不得生成或传递身份字段。
- 无论采用哪种方案，都必须保留后端校验：`allowed_agents` 不能越过 tenant/user 可见范围，`entry_agent` 必须属于 `allowed_agents`。

### D2: Gateway SSE Contract For Main Chat

Phase 1 前端迁主聊天前，需要确认 Gateway `iter_events` 是否足够支撑现有 UI：

- message token / complete message
- custom task events
- workflow shell / todo / task pool updates
- run id / title / finish state
- error event

如果字段不够，前端不得自行回退 `/api/langgraph`；应在 `handoffs/frontend-to-backend.md` 追加 open 项，由后端补 Gateway event projection。

前端 stream mode 硬约束：

- Phase 1 主聊天迁入后，主聊天路径禁止再通过 `useStream` 挂 `onLangChainEvent` / `onCustomEvent` 来订阅 LangGraph `events` stream。
- 若仍存在任何 SDK submit/request 构造层，`streamMode` 必须显式为 `["values", "messages-tuple", "custom"]`。
- 契约测试必须断言主聊天 submit payload **不包含** `"events"` stream mode，防止 LangGraph server 因不支持或不期望的 stream mode 返回 422。

### D3: Resume Endpoint Payload

Phase 2 前必须确认 resume endpoint request model 至少能承载：

- `checkpoint`
- `command` 或等价 resume command
- `workflow_clarification_resume`
- `workflow_resume_run_id`
- `workflow_resume_task_id`
- human resume message
- stream mode / stream resumable 语义

字段映射规则：

| Gateway request field | Upstream LangGraph mapping | Rule |
|---|---|---|
| `interrupt_feedback` | `Command.resume` | 原样作为 resume payload；允许对象或字符串，具体 schema 由 intervention/governance 调用方定义 |
| `goto` | `Command.goto` | 仅允许后端白名单中的 node/route；不能让浏览器传任意内部节点名绕过流程 |
| `checkpoint` | SDK `checkpoint` / resume options | 原样透传给 LangGraph SDK；不得写入 identity context |
| `workflow_clarification_resume` | trusted `context["workflow_clarification_resume"]` | 业务字段透传，由 Gateway 放入 trusted context |
| `workflow_resume_run_id` | trusted `context["workflow_resume_run_id"]` | 业务字段透传，由 Gateway 放入 trusted context |
| `workflow_resume_task_id` | trusted `context["workflow_resume_task_id"]` | 业务字段透传，由 Gateway 放入 trusted context |
| `message` / human resume text | `input.messages` 或 `Command.resume.message`（按 endpoint 语义二选一） | 必须在 endpoint 内固定规则，不允许前端同时驱动两种语义 |
| `config.configurable` | **禁止接受** | request body 中出现时必须拒绝或忽略；Gateway 只发送 `config={"recursion_limit": ...}` |
| `thread_context` / `auth_user` / `tenant_id` / `user_id` | **禁止接受为身份来源** | 身份只能由 Gateway auth + `resolve_thread_context()` 注入 |

Gateway 组装伪代码：

```python
ctx = resolve_thread_context(thread_id, tenant_id, user_id)
context = {
    "thread_id": thread_id,
    "tenant_id": tenant_id,
    "user_id": user_id,
    "thread_context": ctx.serialize(),
    "auth_user": auth_user_snapshot,
    "workflow_clarification_resume": body.workflow_clarification_resume,
    "workflow_resume_run_id": body.workflow_resume_run_id,
    "workflow_resume_task_id": body.workflow_resume_task_id,
}

command = Command(
    resume=body.interrupt_feedback,
    goto=_validate_resume_goto(body.goto),
)

upstream_iter = client.runs.stream(
    thread_id,
    ENTRY_GRAPH_ASSISTANT_ID,
    input=_build_resume_input(body),
    command=command,
    checkpoint=body.checkpoint,
    config={"recursion_limit": 1000},
    context=context,
    stream_mode=["values", "messages-tuple", "custom"],
    multitask_strategy="reject",
)
```

Resume endpoint 验收断言：

- 请求体带 `config.configurable`、`thread_context`、`auth_user`、`tenant_id` 或 `user_id` 时，不会影响 Gateway 注入身份；测试应断言被拒绝或被忽略。
- SDK kwargs 里 `config` 不含 `configurable`，`context` 含 Gateway 注入的 `thread_context/auth_user`，`command.resume` 与 `command.goto` 来自映射后的安全字段。
- `checkpoint` 与 `workflow_resume_*` 能到达上游，intervention/governance resume 恢复原 interrupted run，而不是创建普通新消息。

## Phase 0: Backend Runtime Unblock

目标：先修复 Gateway runtime 400 和 subagent 上下文缺失，不改变前端直连范围。

### Task 0.1 Backend: Gateway context-only submit

Owner: Backend

Files:

- `backend/src/gateway/runtime_service.py`
- `backend/src/gateway/routers/runtime.py`

Functional boundary:

- Only change `/api/runtime/threads/{id}/messages:stream` upstream submit kwargs.
- `client.runs.stream(...)` must use `config={"recursion_limit": 1000}` plus `context=dict(context)`.
- Remove `configurable` injection from `runtime_service.start_stream()`.
- Keep `ThreadDataMiddleware` unchanged.
- Do not trust request body identity fields.

Out of scope:

- Do not change frontend `/api/langgraph` usage in this task.
- Do not change local `DeerFlowClient` / `SubagentExecutor` channel model.
- Do not downgrade LangGraph.

Acceptance:

- `backend/scripts/sso_e2e_smoke.py` Phase 3 no longer fails with `Cannot specify both configurable and context`.
- Unit test asserts `client.runs.stream` kwargs do not contain `config["configurable"]`.
- Unit test asserts `context` contains `thread_context`, `auth_user`, `tenant_id`, `user_id`, `thread_id`.
- Cross tenant/user thread submit still returns 403.

### Task 0.2 Backend: Subagent trusted context propagation

Owner: Backend

Files:

- `backend/src/tools/builtins/task_tool.py`
- `backend/src/subagents/executor.py`

Functional boundary:

- In `task_tool.py`, read parent `thread_context` and `auth_user` from `runtime.config.configurable`.
- Pass `thread_context` and `auth_user` into `SubagentExecutor(...)`.
- In `SubagentExecutor.__init__`, add optional `thread_context` and `auth_user` parameters.
- In child `run_config.configurable`, include `thread_context`, `auth_user`, `thread_id`, `tenant_id`, `user_id`.
- Keep child pregel call as dual channel `config + context`.

Out of scope:

- Do not migrate local pregel to context-only.
- Do not make `ThreadDataMiddleware` trust bare `runtime.context["tenant_id"]`.
- Do not synthesize thread context from loose IDs if parent context is missing.

Acceptance:

- Main agent can invoke `task_tool` and child agent `ThreadDataMiddleware` resolves the same `ThreadContext` as parent.
- Child agent sandbox / uploads / memory paths remain under the same `{tenant_id, user_id, thread_id}` scope.
- Child agent `identity_guard` still receives `auth_user` and rejects identity rewrite attempts.
- Existing non-subagent chat path remains unchanged.

### Task 0.3 Backend tests

Owner: Test / Backend

Functional boundary:

- Add focused tests for Task 0.1 and Task 0.2.
- Prefer unit tests for SDK kwargs and integration-style tests for subagent propagation.

Acceptance:

- Gateway submit kwargs regression test fails on old dual-channel code and passes after Task 0.1.
- Subagent propagation test fails before `thread_context` is added and passes after Task 0.2.
- SSO/OIDC existing tests stay green.

## Phase 1: Main Chat Submit Through Gateway

目标：把网页主聊天 submit 从 `/api/langgraph` 迁到 Gateway，关闭主聊天身份伪造风险；resume / governance 暂不迁。

### Task 1.1 Frontend + Gateway: Thread binding lifecycle

Owner: Frontend + Backend

Functional boundary:

- Implement D1 decision.
- Ensure every frontend main chat thread submitted to Gateway has a Gateway `ThreadRegistry` binding before first message.
- Cover normal chat and agent chat.

Recommended implementation:

- Use `POST /api/runtime/threads` for new thread creation.
- Frontend receives Gateway `thread_id` and navigates to `/workspace/chats/{thread_id}` or `/workspace/agents/{agent_name}/chats/{thread_id}`.
- Remove or bypass local `uuid()` as the authoritative id for Gateway-backed chats.

Out of scope:

- Do not migrate intervention resume / governance resume in this task.
- Do not allow frontend to submit `tenant_id`, `user_id`, `thread_context`, or `auth_user`.

Acceptance:

- New normal chat first message returns 200 through Gateway, not 403.
- New agent chat first message returns 200 through Gateway, not 403.
- Gateway `ThreadRegistry` has binding for the thread before message stream starts.
- Attempting to use another tenant/user's thread id still returns 403.

### Task 1.2 Frontend: Main chat submit adapter

Owner: Frontend

Files:

- `frontend/src/core/threads/hooks.ts`
- Call sites that consume `useThreadStream` for normal chat / agent chat / bootstrap.

Functional boundary:

- Replace main chat `thread.submit(...)` with Gateway `/api/runtime/threads/{id}/messages:stream`.
- Map local settings to Gateway request fields:
  - `message`
  - `group_key`
  - `allowed_agents`
  - `entry_agent`
  - `requested_orchestration_mode`
  - primitive `metadata` only
- Consume Gateway SSE and update the same UI state currently driven by `useStream`.
- Preserve file upload behavior before submit.

Out of scope:

- Do not change `intervention-card.tsx`.
- Do not change `governance/utils.ts`.
- Do not send `checkpoint` / `workflow_resume_*` through `messages:stream`.
- Do not send identity fields from browser.

Acceptance:

- Browser Network for normal chat and agent chat no longer shows `/api/langgraph/*` run submit.
- Browser Network for main chat shows Gateway `/api/runtime/threads/{id}/messages:stream`.
- Main chat sends a message and receives final assistant response.
- Agent chat still respects `agent_name`.
- Workflow / task custom events still render or have documented handoff if Gateway event projection is missing.
- Browser attempts to include fake `thread_context` / `auth_user` are ignored or rejected server-side.
- Intervention resume and governance resume still behave as before and are explicitly allowed to remain on `/api/langgraph` until Phase 2.

### Task 1.3 Phase 1 integration tests

Owner: Test

Functional boundary:

- Cover Gateway-backed frontend main chat path and isolation.

Acceptance:

- New chat through frontend creates/binds thread and streams successfully.
- Existing authorized thread streams successfully through Gateway.
- Unauthorized thread submit returns 403.
- Main chat no longer fails with `configurable['thread_context'] is required`.

### Phase 1 Exit Note: Conditional Pass And Resume Follow-Up

Phase 1 can be accepted for its original scope once the main chat / agent chat
Gateway submit path is green. The original Phase 1 boundary intentionally
excluded `intervention-card.tsx` and `governance/utils.ts`; those resume paths
were allowed to stay on `/api/langgraph` until Phase 2.

However, the Phase 1 frontend implementation now uses Gateway SSE
`liveValuesPatch` as the authoritative live workflow projection for main chat
UI state. That means the old `InterventionCard -> thread.submit(...)` resume
path can leave stale `liveValuesPatch.task_pool` / `workflow_stage` in front of
newer `thread.values`, causing the resolved waiting card to remain visible and
the workflow footer/task panel to appear stuck until a full refresh.

Therefore the Phase 1 exit status is **conditional pass**:

- Main chat Gateway submit is accepted within Phase 1 scope.
- Intervention/governance resume remain formally Phase 2 scope.
- The first Phase 2 implementation item must be intervention resume migration
  to the Gateway live stream path, because it is now a user-visible blocker
  rather than a purely deferred cleanup.

## Phase 2: Resume And Governance Through Gateway

目标：补齐 resume / interrupt / governance 的 Gateway 专用 endpoint，最终下线浏览器直连 `/api/langgraph`。

Phase 2 starts with a P0 compatibility fix for intervention resume. The current
browser path persists the intervention resolution via
`POST /api/threads/{thread_id}/interventions/{request_id}:resolve`, then calls
`thread.submit(...)` from `intervention-card.tsx`. After Phase 1 this bypasses
the Gateway SSE adapter and does not refresh `liveValuesPatch`, so the UI can
keep rendering the old waiting intervention snapshot. Phase 2 must move this
resume flow onto a Gateway-owned streaming path before governance hardening.

### Task 2.1 Backend: Resume endpoint

Owner: Backend

Files:

- `backend/src/gateway/routers/runtime.py`
- `backend/src/gateway/runtime_service.py`

Functional boundary:

- Add `POST /api/runtime/threads/{id}/resume`.
- Request model must support intervention resume semantics, including checkpoint / command / workflow resume fields confirmed in D3.
- The endpoint must cover the existing intervention-card resume contract:
  `checkpoint`, `resume_payload.message`, `workflow_clarification_resume`,
  `workflow_resume_run_id`, and `workflow_resume_task_id`.
- Route must authenticate, resolve thread ownership, inject trusted `thread_context` / `auth_user`, and submit context-only to LangGraph API.
- Client-supplied identity fields must be ignored or rejected.
- It must stream normalized Gateway SSE events using the same state projection
  contract as `messages:stream` so frontend `liveValuesPatch` converges.

Acceptance:

- Intervention resume can resume an interrupted run, not create a fresh unrelated message.
- Submitting a waiting intervention decision clears the waiting card without a
  browser refresh.
- Workflow task execution resumes and emits updated `task_pool` /
  `workflow_stage` through Gateway SSE.
- Cross tenant/user resume returns 403.
- SDK kwargs remain single-channel.

### Task 2.2 Backend: Governance resume endpoint

Owner: Backend

Files:

- `backend/src/gateway/routers/runtime.py`
- governance-related backend tests

Functional boundary:

- Add `POST /api/runtime/threads/{id}/governance:resume` or agreed equivalent.
- Preserve governance resume semantics and audit requirements.
- Use the same trusted context injection path as Task 2.1.

Acceptance:

- Governance resume succeeds through Gateway.
- Governance resume preserves run/task identifiers.
- Audit / governance history remains correct.
- Cross tenant/user governance resume returns 403.

### Task 2.3 Frontend: Switch resume callers

Owner: Frontend

Priority note: migrate `InterventionCard` first. It currently calls
`thread.submit(...)` after `resolveIntervention`, which bypasses the Gateway
SSE adapter and can leave stale `liveValuesPatch` workflow state in the UI.
The migrated resume path must update the same live state used by main chat,
so the waiting card disappears and workflow progress continues without a
refresh.

Files:

- `frontend/src/components/workspace/messages/intervention-card.tsx`
- `frontend/src/core/governance/utils.ts`

Functional boundary:

- Switch intervention resume to Gateway resume endpoint.
- Switch governance resume to Gateway governance resume endpoint.
- Preserve current user interaction and optimistic/loading behavior.
- Do not use `thread.submit(...)` or `client.runs.create(...)` for browser resume after this phase.

Acceptance:

- Browser Network shows no `/api/langgraph/*` calls for intervention resume.
- Browser Network shows no `/api/langgraph/*` calls for governance resume.
- Intervention resume end-to-end passes.
- Governance resume end-to-end passes.
- Fake identity fields from browser are ignored or rejected.

### Task 2.4 Nginx / exposure hardening

Owner: Backend / DevOps

Files:

- `templates/nginx.offline-runtime.conf.template`
- deployment-specific nginx config if any

Functional boundary:

- After frontend has no browser dependency on `/api/langgraph`, restrict `/api/langgraph` to Gateway-internal access or remove browser exposure.

Acceptance:

- Browser cannot directly access `/api/langgraph/*` in protected deployment.
- Gateway can still reach LangGraph server.
- Offline/runtime deployment smoke test passes.

## Phase 3: Hardening And Documentation

目标：补调试能力、依赖注释和探针说明，降低后续升级回归风险。

### Task 3.1 Observability

Owner: Backend

Functional boundary:

- Add `GATEWAY_DEBUG_LG_ERRORS=true` or equivalent debug switch around sanitized LangGraph errors.
- Default behavior remains sanitized.

Acceptance:

- Default production response does not leak internal exception details.
- Debug mode includes raw LG error enough to diagnose channel mismatch.

### Task 3.2 Dependency notes

Owner: Backend

Functional boundary:

- Add comment near LangGraph dependency declaration explaining `configurable + context` mutual exclusion since LG 0.6.

Acceptance:

- Future dependency bump reviewers can see the channel constraint from dependency file comments or adjacent docs.

### Task 3.3 Probe script docs

Owner: Backend / Test

Functional boundary:

- Document `backend/scripts/_probe_channels.py`, `probe_lg_channels.py`, `probe_local_pregel.py`.
- State when to rerun them: LangGraph API/core major upgrade or channel behavior suspicion.

Acceptance:

- A developer can rerun probes and compare `.probe_out` results without rereading the bug investigation thread.

## Cross-Phase Acceptance Matrix

The whole feature is accepted only when:

- Gateway `/api/runtime/threads/{id}/messages:stream` no longer sends both non-empty `configurable` and `context`.
- `ThreadDataMiddleware` continues using trusted `thread_context`.
- Main chat no longer submits runs through browser `/api/langgraph`.
- Resume / governance either remain explicitly out of Phase 1 or are migrated through Phase 2 endpoints.
- Browser cannot forge tenant/user/thread identity for migrated paths.
- Subagent inherits parent `thread_context` and `auth_user`.
- Cross tenant/user/thread tests return 403.
- OIDC/SSO missing user still returns 401/403.
- Uploads / artifacts / sandbox / memory paths remain under the correct tenant/user/thread scope.

## Handoff Rules

Use `collaboration/handoffs/frontend-to-backend.md` when frontend discovers:

- Gateway SSE is missing an event or field needed by existing UI.
- Gateway request model cannot express a current main chat setting.
- Resume endpoint needs an additional field not listed in D3.

Use `collaboration/handoffs/backend-to-frontend.md` when backend needs:

- Frontend to confirm event rendering behavior.
- Frontend to choose UX for server-created thread ids or failed binding.
- Frontend to confirm whether a resume/governance state can be represented by the proposed endpoint.

When a handoff is resolved, copy the final contract back into this feature document.

## Open Questions

- D1 route decision is settled for implementation planning: use server-created Gateway thread ids via `POST /api/runtime/threads`, with the required-field handling described in D1.
- D2 Gateway SSE parity should be verified before replacing `useStream` state handling.
- D3 resume/governance payload mapping is settled for implementation planning; see the field table and pseudo-code in D3.
