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
