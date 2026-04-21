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

## [open] Gateway SSE event parity for main chat (Phase 1 D1.2 blocker)
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
