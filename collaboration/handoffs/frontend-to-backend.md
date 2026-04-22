# Frontend To Backend Handoffs

Use this file when frontend is blocked by missing backend behavior, payloads, or
contracts.

## Entry Template

```md
## [open] Short title
- Date:
- Related feature:
- Blocking area:
- Current frontend assumption:
- What is missing from backend:
- Needed response:
- Suggested payload or API:
- Notes:
```

## Open Items

## [closed] Gateway runtime context does not forward app-level submit fields (Phase 1 D1.2 blocker)
- Closed: 2026-04-21 by backend (commit pending on branch `claude/runtime-lg1x-phase1`).
- Resolution:
  - `backend/src/gateway/routers/runtime.py` now defines three Pydantic models
    with `ConfigDict(extra="forbid")`: `ClarificationAnswer`,
    `WorkflowClarificationResponse`, and `AppRuntimeContext` (8 fields:
    `thinking_enabled`, `is_plan_mode`, `subagent_enabled`, `is_bootstrap`,
    `workflow_clarification_resume`, `workflow_resume_run_id`,
    `workflow_resume_task_id`, `workflow_clarification_response`).
  - `MessageStreamRequest.app_context: AppRuntimeContext | None` is the
    transport channel. Unknown keys at any nesting level surface as HTTP 422.
  - `stream_runtime_message` now merges
    `body.app_context.model_dump(exclude_none=True)` into the runtime
    `context` FIRST, then overlays server-sourced identity/routing fields —
    identity always wins by merge order. A belt-and-braces
    `_IDENTITY_CONTEXT_KEYS` filter drops identity keys from `app_fields`
    before the merge even though `extra="forbid"` already rejects them.
  - `app_context` is per-run: it is NOT persisted into ThreadRegistry binding
    metadata (verified by `test_app_context_fields_not_in_binding`).
  - Contract tests: `backend/tests/test_runtime_app_context.py` (10 cases
    across 4 classes — round-trip / absence / partial / 422-extra-forbid at
    3 levels / identity defense for 5 keys / body identity still overridden
    by identity-merge-last / not persisted). Regression suite stays green:
    `test_runtime_app_context.py + test_runtime_router.py +
    test_runtime_router_phase1.py + test_runtime_sse_projection.py +
    test_runtime_service_channels.py` → **95/95 passed**.
  - Frontend can now wire all 8 fields through the `app_context` channel.

## [archived] Gateway runtime context does not forward app-level submit fields (Phase 1 D1.2 blocker)
- Date: 2026-04-21
- Related feature:
  - `features/runtime-lg1x-trusted-context-submit.md` (D2 Gateway submit contract)
  - `features/runtime-lg1x-trusted-context-submit-development-checklist.md` (D1.2)
- Blocking area:
  Frontend main chat submit migration from `/api/langgraph` to Gateway
  `POST /api/runtime/threads/{id}/messages:stream`. The Gateway router only
  forwards `requested_orchestration_mode` and `entry_agent` into the LangGraph
  runtime `context`; every other app-level field that today's frontend passes
  via `thread.submit(..., { context })` is silently dropped. Without a
  transport for these fields, D1.2 cannot faithfully reproduce current main
  chat behavior. The user has explicitly required a complete migration with no
  deferred gaps, so D1.2 is paused until this gap is closed.
- Current frontend assumption:
  `frontend/src/core/threads/hooks.ts::sendMessage` currently calls
  `thread.submit(threadId, input, { context: { ...settingsContext, ...extraContext, thinking_enabled, is_plan_mode, subagent_enabled, thread_id, workflow_clarification_resume, workflow_resume_run_id, workflow_resume_task_id }, config: { recursion_limit: 1000 } })`.
  These fields reach the agent runtime through LangGraph native `context` and
  drive middleware wiring / workflow resume logic.
- What is missing from backend:
  `backend/src/gateway/routers/runtime.py` (`stream_messages`, lines ~390–414)
  builds `context` using only identity fields (`tenant_id`, `user_id`,
  `username`, `thread_id`, `allowed_agents`, `group_key`, `thread_context`,
  `auth_user`) plus `requested_orchestration_mode` and `agent_name`.
  `MessageStreamRequest.metadata` is persisted to the `ThreadRegistry` binding
  but is NOT merged into the LangGraph runtime `context`. The following
  app-level fields that the current UI depends on are therefore lost:

  1. `thinking_enabled` — drives thinking-capable model selection
     (flash/thinking/pro/ultra modes) and the thinking code path in
     `src/models/factory.py::create_chat_model`.
  2. `is_plan_mode` — gates `TodoListMiddleware` (Plan mode + `write_todos`
     tool) in `src/agents/lead_agent/agent.py`.
  3. `subagent_enabled` — gates the `task` tool and `SubagentLimitMiddleware`.
  4. `workflow_clarification_resume` — boolean flag consumed by
     `workflow_resume.py` to recognize a clarification-resume turn.
  5. `workflow_resume_run_id` — identifies which suspended workflow run the
     clarification answer belongs to.
  6. `workflow_resume_task_id` — identifies which suspended workflow task
     within that run is being resumed.
  7. `workflow_clarification_response` — payload carrying the user's
     clarification answers (`{ answers: { [id]: { text } } }`), used by the
     resume path to unblock `ClarificationMiddleware`-suspended tasks.
  8. `is_bootstrap` — agent bootstrap marker for the
     `/workspace/agents/[agent]/chats/new` path; distinguishes first-turn
     bootstrap from regular sends.
- Why this matters:
  - Items 4–7 are the workflow clarification / resume lifecycle. Dropping
    them means a user's clarification answer is submitted as a plain message;
    the interrupted workflow task stays suspended forever and the run cannot
    recover. This is a hard regression of the current workflow mode.
  - Items 1–3 control main-chat model selection, plan mode, and subagent
    delegation. Dropping them silently downgrades every main-chat submit to
    the default non-thinking, non-plan, non-subagent path. Users flipping
    mode toggles see no effect.
  - Item 8 affects the agent bootstrap UX on `/agents/[agent]/chats/new`.
  - Phase 1 D2 hard constraint "frontend must not fall back to
    `/api/langgraph`" means these fields must travel through Gateway or the
    Phase 1 feature goal is unmet.
- Needed response:
  Extend the Gateway submit contract so the frontend can pass the above 8
  app-level fields and the router merges them into the LangGraph runtime
  `context` dict (alongside the existing identity fields). Identity fields
  (`tenant_id`, `user_id`, `thread_id`, `thread_context`, `auth_user`) must
  still be sourced only from the server; the new channel must not be a
  general-purpose `context` passthrough.
- Suggested payload or API:
  Add a server-validated app-runtime block to `MessageStreamRequest`, e.g.:

  ```python
  class AppRuntimeContext(BaseModel):
      model_config = ConfigDict(extra="forbid")
      thinking_enabled: bool | None = None
      is_plan_mode: bool | None = None
      subagent_enabled: bool | None = None
      is_bootstrap: bool | None = None
      workflow_clarification_resume: bool | None = None
      workflow_resume_run_id: str | None = None
      workflow_resume_task_id: str | None = None
      workflow_clarification_response: WorkflowClarificationResponse | None = None

  class WorkflowClarificationResponse(BaseModel):
      model_config = ConfigDict(extra="forbid")
      answers: dict[str, ClarificationAnswer]

  class ClarificationAnswer(BaseModel):
      model_config = ConfigDict(extra="forbid")
      text: str

  class MessageStreamRequest(BaseModel):
      # ... existing fields ...
      app_context: AppRuntimeContext | None = None
  ```

  Router behavior:
  - Merge `body.app_context.model_dump(exclude_none=True)` into the runtime
    `context` dict AFTER the identity fields are set, so identity cannot be
    overwritten (explicit key-allow-list is also acceptable).
  - `extra="forbid"` on both the app-context block and its nested types so
    unknown keys surface as 422 instead of being silently dropped.
  - Keep `MessageStreamRequest.extra="ignore"` on the top-level model so
    stray identity fields from the browser continue to be dropped.
  - Do not store `app_context` in `ThreadRegistry` binding metadata; it is
    per-run state, not per-thread.
- Acceptance:
  - Contract tests assert each of the 8 fields round-trips into the runtime
    `context` when supplied, and is absent when not supplied.
  - Contract tests assert supplying an unknown key under `app_context`
    returns 422 (prevents silent drops like the current regression).
  - Contract tests assert supplying `tenant_id` / `user_id` / `thread_id` /
    `thread_context` / `auth_user` inside `app_context` is rejected or
    ignored (never overrides server-sourced identity).
  - End-to-end regression: submit with `workflow_clarification_resume=true`
    + `workflow_resume_run_id` + `workflow_resume_task_id` +
    `workflow_clarification_response.answers` against a suspended workflow
    task advances the task past `ClarificationMiddleware` (same behavior as
    today's `/api/langgraph` path).
- Notes:
  - D1.1 (thread lifecycle) and the Gateway SSE projection (previous closed
    entry) are unaffected.
  - Phase 2 intervention resume still targets the dedicated
    `/api/interventions/{id}/resolve` endpoint, not this submit path.
  - Frontend will wire all 8 fields through the new `app_context` channel in
    D1.2 once this contract lands; no fallback to `/api/langgraph` will be
    introduced.

## [closed] Gateway SSE event parity for main chat (Phase 1 D1.2 blocker)
- Resolved: 2026-04-21 by backend commit `512938ea` (feat(runtime): Phase 1 D1.3
  — extend Gateway SSE projection for main chat). All 8 items below are
  projected; `state_snapshot`, allow-listed custom task events, and enriched
  `run_completed` are documented in `collaboration/handoffs/backend-to-frontend.md`.
  Frontend D1.2 adapter unblocked.

- Date: 2026-04-21
- Related feature:
  - `features/runtime-lg1x-trusted-context-submit.md` (D2 Gateway SSE Contract For Main Chat)
  - `features/runtime-lg1x-trusted-context-submit-development-checklist.md` (D1.2, D1.3)
- Blocking area:
  Frontend main chat submit migration from `/api/langgraph` to Gateway
  `POST /api/runtime/threads/{id}/messages:stream`. Phase 1 D1.1 (thread
  lifecycle) can proceed independently, but D1.2 (submit adapter) cannot ship
  until the Gateway SSE projection exposes the fields listed below.
- Current frontend assumption:
  `useThreadStream` in `frontend/src/core/threads/hooks.ts` currently consumes
  the native LangGraph `useStream` with `streamMode=["values","messages-tuple","custom"]`
  plus `onLangChainEvent` / `onCustomEvent`. The UI state is driven by full
  `values` snapshots, custom multi-agent task events, and `onFinish(state)`.
- What is missing from backend:
  `backend/src/gateway/runtime_service.py::iter_events` currently projects only
  `ack`, `message_delta`, `message_completed`, `artifact_created`,
  `intervention_requested`, `governance_created`, `run_completed`, `run_failed`.
  The following signals that today's UI depends on are not projected:
  1. `title` updates (auto-generated thread title) — needed by `ThreadTitle`.
  2. `todos[]` updates — needed by `TodoList`.
  3. `task_pool[]` updates (multi-agent tasks, including clarification/intervention fields) — needed by `task-panel.tsx`.
  4. `workflow_stage` / `workflow_stage_detail` / `workflow_stage_updated_at` — needed by `WorkflowFooterBar` and `mergeThreadValuesWithPatch`.
  5. `resolved_orchestration_mode` / `orchestration_reason` — needed by `OrchestrationSummary`.
  6. Multi-agent custom task events: `task_started`, `task_running`,
     `task_waiting_intervention`, `task_waiting_dependency`, `task_help_requested`,
     `task_resumed`, `task_completed`, `task_failed`, `task_timed_out` — consumed by
     `classifyTaskEvent` / `fromMultiAgentTaskEvent` / `fromLegacyTaskEvent` in `hooks.ts`.
  7. A terminal full-state snapshot equivalent to LangGraph `onFinish(state)` —
     today's `run_completed` carries only `{thread_id, run_id}`, not the final
     `messages`/`title` used for desktop notifications and query invalidation.
  8. Streaming human-message echo (so optimistic message swap works without
     reading LangGraph thread state directly). Today `hooks.ts` swaps optimistic
     messages once `thread.messages.length` grows; Gateway needs an equivalent
     authoritative signal or a state-snapshot event.
- Why this matters:
  Feature spec D2 forbids frontend from falling back to `/api/langgraph` when
  Gateway SSE is insufficient, and forbids subscribing to LangGraph `events`
  stream mode. Without these fields, D1.2 either ships a degraded main chat
  (no tasks panel, no workflow progress, no title refresh, no todos) or
  violates the spec. Therefore D1.2 is paused until the backend extends the
  projection.
- Needed response:
  Extend `iter_events` (or introduce additional event names) so the Gateway
  SSE stream is sufficient to drive the current main chat UI without relying
  on LangGraph native `values` / `custom` / `events` stream modes. At
  minimum, surface the eight items above. Concrete schemas can be decided
  jointly; the frontend is flexible on event names as long as payloads map
  1:1 to the existing state/event shapes in `core/threads/hooks.ts`.
- Suggested payload or API:
  - Add an `SSE_STATE_SNAPSHOT` event emitted on each `values` chunk (or
    periodic coalesced snapshots) with the fields listed in items 1–5 and
    7 above, preserving LangGraph shapes.
  - Mirror the multi-agent `custom` task events 1:1 under their existing
    `type` names (item 6), or expose them under a single Gateway event
    `task_event` with a `type` field.
  - Extend `run_completed` payload with the final state summary (title,
    last AI message, artifacts count) so `onFinish` behavior is preserved.
- Notes:
  - D1.1 (thread lifecycle via `POST /api/runtime/threads`) is unaffected
    and proceeds now.
  - Intervention / governance resume flows are out of scope for this
    handoff (tracked by Phase 2 D2.1 / D2.2).
  - Contract tests must continue to assert main chat submit payload does
    not include the `events` stream mode (spec D2 hard constraint).

## [closed] Governance history lacks server-side time range filtering
- Date: 2026-03-26
- Related feature:
  - `features/workflow-phase5-two-stage-governance-core-and-operator-console.md`
  - `features/workflow-phase5-two-stage-governance-core-and-operator-console-frontend-checklist.md`
- Blocking area:
  operator console history filtering accuracy for large governance ledgers
- Current frontend implementation:
  Phase 5B governance console is now using `GET /api/governance/history` for
  status/risk/agent/thread/run filters and then applying `dateFrom/dateTo`
  locally on the loaded history results.
- Current frontend assumption:
  each history item already exposes enough timestamp data (`created_at`,
  `resolved_at`) for a basic date range filter, so the UI can provide an
  immediate first version without inventing additional contracts.
- What is missing from backend:
  the history API does not currently accept server-side date range filters, so
  frontend cannot guarantee a complete time-bounded result set once the ledger
  grows beyond the loaded page/limit.
- Why this matters:
  local filtering over the current response page is acceptable for initial
  operator console development, but it is not equivalent to true backend
  filtering plus pagination. The UI may under-report matching records outside
  the loaded slice.
- Needed response:
  backend should add one of these stable options to
  `GET /api/governance/history`:
  1. `created_from` / `created_to`
  2. `resolved_from` / `resolved_to`
  3. a clearly documented single time-field contract for history filtering
- Suggested payload or API:
  preferred smallest-scope contract:
  - `resolved_from`: ISO datetime or date
  - `resolved_to`: ISO datetime or date
  because the history tab is operator-facing and primarily about terminal
  decisions.
- Backend response (2026-03-26):
  implemented all four server-side time range filters on both queue and history
  endpoints. The contract is:

  **Queue API** (`GET /api/governance/queue`):
  - `created_from`: ISO datetime — filter by `created_at >=`
  - `created_to`: ISO datetime — filter by `created_at <=`

  **History API** (`GET /api/governance/history`):
  - `created_from`: ISO datetime — filter by `created_at >=`
  - `created_to`: ISO datetime — filter by `created_at <=`
  - `resolved_from`: ISO datetime — filter by `resolved_at >=`
  - `resolved_to`: ISO datetime — filter by `resolved_at <=`

  Design rationale:
  - `created_from/created_to` applies to all entries (both decided and resolved)
    and is the primary general-purpose time filter. Available on both queue and
    history endpoints.
  - `resolved_from/resolved_to` is history-only and targets the operator use
    case of "show me what was resolved in this window". Entries without
    `resolved_at` (e.g., auto-decided allow/deny) are automatically excluded
    when `resolved_from` or `resolved_to` is specified.
  - ISO-8601 string comparison is used (lexicographically sortable). Frontend
    should pass full ISO datetime strings (e.g., `2026-03-26T00:00:00+00:00`).
  - All time range filters compose with existing filters (thread_id, run_id,
    risk_level, source_agent, status) and with pagination (limit/offset).
  - The `total` field in paginated responses reflects the true count of all
    matching entries after all filters are applied, not just the returned page.

  Implementation:
  - `GovernanceLedger.query()` now accepts `created_from`, `created_to`,
    `resolved_from`, `resolved_to` parameters.
  - Both API endpoints pass these through to the ledger query.
  - 5 ledger-level tests + 2 API-level tests cover the new filters.
  - All 61 governance tests pass.

  Frontend can now remove the "date filtering is applied on loaded results only"
  UI hint and switch to server-side filtering.

- Notes:
  frontend has consumed this contract in Phase 5B:
  - governance history date filters now submit server-side `resolved_from` /
    `resolved_to`
  - the old "loaded results only" UI hint has been removed
  no further backend work is required for this specific blocker.

## [open] Intervention resolve accepted but runtime continuation is not yet verifiably observable
- Date: 2026-03-17
- Related feature:
  - `features/workflow-intervention-flow.md`
  - `features/workflow-intervention-display-projection.md`
- Blocking area:
  post-confirmation workflow continuation after `WAITING_INTERVENTION`
- Current frontend observation:
  in the real meeting-booking scenario, user clicks the confirm action and
  receives a success toast (`已提交决策`), but the visible workflow task remains
  in the waiting-intervention surface instead of clearly moving forward.
- Confirmed frontend-side fact:
  current frontend implementation did not originally perform any local task
  status advancement after a successful resolve response; it only showed a
  success toast and then waited for authoritative backend state/event updates.
  Therefore the UI can remain visually stuck in `waiting_intervention` unless
  backend promptly drives a visible state change back to the same thread.
- Confirmed backend-side fact from code inspection:
  `backend/src/gateway/routers/interventions.py` does attempt to:
  1. update the task from `WAITING_INTERVENTION` to `RUNNING` or `FAILED`
  2. persist `intervention_status="resolved"`
  3. create a follow-up run via `client.runs.create(...)`
  however, from frontend-visible behavior we cannot yet verify that the resume
  run is actually starting successfully and producing a visible task/status
  update for the same thread after resolve.
- What needs backend investigation:
  1. whether `client.threads.update_state(... values={"task_pool": [updated_task]})`
     is replacing the whole task pool and unintentionally dropping sibling
     tasks or authoritative state needed for continuation
  2. whether the resume `client.runs.create(...)` call is succeeding in the
     real environment after resolve
  3. whether that resume run is bound to the correct assistant / graph entry
     path for workflow continuation
  4. whether the resumed run produces any custom/task events that the existing
     frontend stream connection can actually receive
  5. whether the resolved task ever transitions from:
     - `WAITING_INTERVENTION -> RUNNING`
     - then onward into `DONE` / downstream workflow progress
  6. whether `workflow_resume.py` is correctly recognizing the synthetic
     `[intervention_resolved] ...` human message and routing back into the
     suspended task flow instead of idling or ending
- Needed response:
  backend should confirm, with logs or runtime evidence, which of the following
  is true:
  1. resolve endpoint persists state but resume run is failing to start
  2. resume run starts but does not emit visible state updates
  3. resume run starts and emits updates, but they are not attached to the same
     thread/run state that frontend is hydrating from
  4. some other backend continuation bug is preventing the task from resuming
- Suggested backend debug evidence:
  - resolve endpoint logs for:
    - task status before update
    - updated task payload written to thread state
    - result of `client.runs.create(...)`
  - thread state snapshot immediately after resolve
  - follow-up run creation result / run id
  - whether the resumed run emits:
    - `task_running`
    - `task_resumed`
    - `workflow_stage_changed`
    - task pool updates with the same task id
- Notes:
  frontend can add a minimal optimistic local transition to avoid stale UI, but
  that would only mask the symptom. Backend still needs to confirm whether the
  authoritative workflow continuation is truly happening after resolve.

## [closed] Intervention input action payload contract needs to be frozen
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-flow.md`
- Blocking area: schema-driven `input` action submission for intervention resolve
- Current frontend assumption:
  frontend has completed the Phase 1 intervention integration and currently
  treats `input` actions as a single free-text field submitted as:
  `payload.comment`.
- What is missing from backend:
  the current handoff and feature doc freeze the top-level resolve body
  (`fingerprint`, `action_key`, `payload`) but do not yet freeze how Phase 1
  `input` actions map `payload_schema` into concrete payload field names.
- Needed response:
  backend should freeze one of the following for Phase 1 so frontend can remain
  generic without per-scenario branching:
  1. all Phase 1 `input` actions submit `{ comment: string }`
  2. `payload_schema` exposes the canonical field name plus required/optional
     rule, and frontend should build the payload from that schema
- Suggested payload or API:
  preferred smallest-scope option for Phase 1:
  - `input` kind always maps to:
    ```json
    {
      "fingerprint": "...",
      "action_key": "provide_input",
      "payload": {
        "comment": "..."
      }
    }
    ```
  if backend wants schema-driven field names in Phase 1, please specify the
  minimum supported `payload_schema` shape explicitly.
- Backend response (2026-03-17):
  frozen for Phase 1: all `input` kind actions use `{ comment: string }` as the
  payload shape. Backend resolve endpoint accepts any `payload` object, so
  `{ "comment": "用户输入内容" }` is the canonical Phase 1 contract.
  Phase 2 may introduce `payload_schema`-driven field names, but Phase 1
  frontend can hardcode `comment` for `input` actions without risk.
- Notes:
  backend default action schema for `provide_input` already uses
  `placeholder: "请输入修改意见..."` which aligns with the `comment` field
  semantics.

## [closed] Clarify whether intervention_resolution is frontend-visible in Phase 1
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-flow.md`
- Blocking area: post-resolution task rendering and hydration behavior
- Current frontend assumption:
  Phase 1 UI only requires `intervention_request`, `intervention_status`, and
  `intervention_fingerprint` to render active intervention state.
- What is missing from backend:
  backend handoff mentions an additional `intervention_resolution` field but
  does not define whether frontend should consume or display it.
- Needed response:
  please confirm one of:
  1. Phase 1 frontend may ignore `intervention_resolution`
  2. frontend should display the accepted user choice after resolve/reconnect
- Backend response (2026-03-17):
  confirmed option 1: Phase 1 frontend may ignore `intervention_resolution`.
  This field is a backend-only persistence artifact used to drive resume
  behavior. Frontend only needs:
  - `intervention_request` (to render the card)
  - `intervention_status` (to know pending vs resolved vs consumed)
  - `intervention_fingerprint` (to submit resolution)
  `intervention_resolution` is a non-blocking extension field for Phase 1.
  Phase 2 may expose it for post-resolution display (e.g., "你选择了：批准执行").
- Notes:
  after resolution, the task transitions from `WAITING_INTERVENTION` to
  `RUNNING` or `FAILED`. Frontend can rely on the status change to dismiss
  the intervention card.

## [closed] Intervention 422 error body shape is not yet specified
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-flow.md`
- Blocking area: validation error presentation for resolve submission
- Current frontend assumption:
  frontend currently handles `409` and `422` by status code and shows generic
  localized error copy.
- What is missing from backend:
  the handoff does not yet specify whether `422` returns only a simple `detail`
  string or a structured validation payload.
- Needed response:
  please confirm the response shape for invalid payload submissions so frontend
  can decide whether generic toast copy is enough for Phase 1 or whether field
  errors should be surfaced.
- Backend response (2026-03-17):
  confirmed option 1 for Phase 1: `422` returns FastAPI default
  `{ "detail": "Invalid action_key: xxx" }` — a simple string.
  `409` returns `{ "detail": "Fingerprint mismatch: intervention may be stale" }`.
  `404` returns `{ "detail": "No pending intervention found for request_id: xxx" }`.
  Frontend generic toast copy is sufficient for Phase 1. Phase 2 may introduce
  structured field-level errors if needed.
- Notes:
  current frontend implementation with status-code-based generic error handling
  is fully compatible with the backend response shape.

## [closed] Refresh during true queued window now hydrates queued shell
- Date: 2026-03-14
- Related feature: `features/workflow-entry-feedback-and-runtime-polish.md`
- Blocking area: live `queued` workflow shell recovery during refresh/reconnect
- Current frontend assumption:
  when a workflow run is enqueued behind another active run, backend will both
  render a visible `queued` stage before planner start and persist enough
  thread state for refresh/reconnect to recover that same shell.
- What was missing from backend:
  refresh/reconnect during the queued window previously dropped the shell back
  to a bare `Workflow` placeholder instead of hydrating queued from
  thread state/history.
- Backend response:
  backend now persists enqueue-time `queued` into reconnect-safe checkpoint
  history as well as the thread-row mirror, so the same queued shell survives
  refresh before worker startup.
- Suggested payload or API:
  keep the current contract; no new frontend-only fields are required if
  backend truly emits at enqueue time:
  `run_id`, `workflow_stage=queued`, `workflow_stage_detail`,
  `workflow_stage_updated_at`.
- Validation standard:
  - reproduce with two real `Workflow` submissions while a single worker is
    busy
  - second run must show `workflow_stage=queued` with visible queued shell copy
    before its `Starting background run` moment
  - second run must keep the same `run_id` when transitioning
    `queued -> planning`
  - refresh during that queued window must still hydrate the queued shell from
    thread state
- Notes:
  real browser verification on 2026-03-13 first reproduced a genuine backend
  queue:
  - second run created at `2026-03-13T08:57:50Z`
  - second background run started at `2026-03-13T08:59:10Z`
  - backend logged `run_queue_ms=79602`
  - frontend did not show a visible queued shell during that wait window
  backend documentation later marked this as repaired, but frontend reran the
  live scenario on 2026-03-14 after the stack was restarted and initially
  still could not verify the fix:
  - first run thread:
    - `0ac6af7a-53f8-42cc-8187-537a8620fac6`
  - second run thread:
    - `315680a3-fbce-49dc-b52f-ebe02cad27ab`
  - second run page behavior:
    - around `2.2s` after submit, the real thread page opened and showed only
      the user request plus `Workflow`, with no visible `acknowledged` or
      `queued` shell copy
    - around `60.2s`, the first visible workflow shell copy was already
      `planning`:
      `正在理解你的需求，规划执行步骤…`
    - the page never showed:
      `已提交，正在排队启动...`
  - because the page never entered a visible queued shell, frontend could not
    complete the required refresh-during-queued verification either
  backend reworked the runtime path again, and frontend reran the live
  scenario after another service restart on 2026-03-14:
  - first run thread:
    - `c847675a-000c-4a97-89da-78fd9f66b16c`
  - second run thread:
    - `bd65c991-c735-446d-8b77-c05f0fd1f820`
  - second run page behavior:
    - around `2.2s` after submit, the page showed the expected queued copy:
      `已提交，正在排队启动...`
    - frontend refreshed immediately during that queued window
    - after refresh, the page showed only `Workflow` and did not restore the
      queued copy
    - around `56.9s`, the same run later advanced into `planning`
  frontend status:
  - current frontend merge/render logic already supports authoritative
    `queued` for the same `run_id`
  - no frontend contract rename is needed
  - live enqueue-time `queued` visibility is now verified
  backend confirmed the previous root cause on 2026-03-14:
  - the earlier repair matched a test-double style `conn.store` path and did
    not reliably hit the live runtime persistence chain
  - patching only `langgraph_api.models.run.create_valid_run` was not enough
    to guarantee interception of already-bound run-creation entrypoints
  backend repair status after the 2026-03-14 rework:
  - enqueue-time `queued` now targets the existing `Threads.get ->
    Threads.set_status` persistence path
  - the same `workflow_stage_changed` payload contract is preserved
  - frontend had verified live queued visibility
  backend follow-up after the latest 2026-03-14 investigation:
  - frontend refresh was confirmed to hydrate from `threads.getHistory(limit=1)`
    rather than from the thread-row mirror alone
  - backend repaired enqueue-time queue staging again so the same queued shell
    now writes into checkpoint history before worker startup, while preserving
    the existing thread-row mirror and `workflow_stage_changed` payload
  - backend regression result for the repaired helper path is now:
    `56 passed in 27.87s`
  frontend close-out after the redeployed backend was re-tested in a real
  browser session:
  - second run thread `44d2a074-ad29-4522-a554-9adcf5b12110` visibly showed
    `已提交，正在排队启动...` around `2.1s` after submit
  - immediate refresh during that queued window preserved the same queued copy
  - a follow-up live run on refreshed state later advanced into `planning`
  - frontend therefore considers this blocker resolved and closed

## [open] Workflow timeline event contract
- Date: 2026-03-13
- Related feature: `features/workflow-realtime-chat.md`
- Blocking area: main chat timeline rendering in workflow mode
- Current frontend assumption:
  frontend can consume custom stream events, but there is no dedicated
  chat-facing workflow timeline event yet.
- What is missing from backend:
  a stable event type and payload for timeline messages, plus replacement/dedup
  rules.
- Needed response:
  confirm whether backend will emit a new event, and from which nodes.
- Suggested payload or API:
  `workflow_chat_message` with `run_id`, `message_id`, `phase`, `text`,
  optional `task_id`, optional `agent_name`, `replace`, `created_at`.
- Notes:
  status-only task events are enough for cards, but not ideal for main timeline
  text generation.
