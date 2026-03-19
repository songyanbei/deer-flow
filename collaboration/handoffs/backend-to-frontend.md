# Backend To Frontend Handoffs

Use this file when backend needs frontend rendering rules, UX decisions, or
clarification on how a payload will be displayed.

## Entry Template

```md
## [open] Short title
- Date:
- Related feature:
- Blocking area:
- Backend question:
- Frontend decision needed:
- Suggested UI behavior:
- Notes:
```

## Open Items

## [closed] Workflow first-screen contract is now fully verified for queued refresh recovery
- Date: 2026-03-14
- Related feature:
  - `features/workflow-entry-feedback-and-runtime-polish.md`
- Blocking area:
  - live busy-worker queued-shell verification and queued-window refresh recovery
- Backend question:
  - backend has now reworked the runtime enqueue-time queue staging twice:
    first to hit the real thread-state persistence path, and now to also write
    the same queued shell into checkpoint history; frontend should confirm that
    refresh during the queued window hydrates the same shell while keeping the
    same parsing contract.
- Frontend decision needed:
  - confirmed: workflow shell hydrates from thread state first and merges
    incremental events by `run_id`
  - confirmed: terminal `summarizing` stays visible until the next user
    turn/run replaces it
- Suggested UI behavior:
  - treat thread-level workflow shell as authoritative before `task_pool`
  - render `acknowledged -> queued -> planning -> routing -> executing ->
    summarizing`
  - keep terminal `summarizing` visible after `DONE` / `ERROR` until the next
    run replaces it
  - never merge stage updates across different `run_id`
- Notes:
  - frontend does not need a contract rename or any new page-only fields
  - canonical fields for both hydration and stream patches are:
    - `run_id`
    - `workflow_stage`
    - `workflow_stage_detail`
    - `workflow_stage_updated_at`
  - `orchestration_mode_resolved` now carries `run_id` when the resolved mode
    is `workflow`
  - `workflow_stage_changed` now always carries `run_id`
  - `queued` is now a backend-authoritative stage, not a frontend-only local
    shell
  - selector emits `acknowledged`, planner emits backend `queued` before real
    planning work, router/executor continue to drive `routing` / `executing`,
    and planner keeps terminal `summarizing` detail on success and failure for
    reconnect recovery
  - new user turns create a new workflow `run_id`; clarification resumes reuse
    the existing `run_id`
  - frontend implementation started on 2026-03-13 and currently includes:
    - optimistic local `acknowledged` shell
    - run-scoped hydration/patch merge
    - same-run stage advancement by `workflow_stage_updated_at`
    - workflow shell priority over task-level waiting summaries
    - automated regression coverage for immediate ack, backend queued
      transition, and planning recovery rendering
    - real-browser verification of the supported meeting-booking workflow path
      from the home page `Workflow` mode selector
    - live refresh/recovery verification on a completed workflow thread
  - remaining frontend work before feature close:
    - no blocking items remain for this feature
    - tighter live capture of the earliest transient `acknowledged` shell
      before the supported meeting-booking flow completes remains
      observational only
  - latest real-browser verification on 2026-03-13 used the supported request:
    - `我叫孙琦，帮我预定明天上午9-10点的会议室，10个人左右，产品介绍会`
  - result from the live run:
    - frontend correctly navigated to a real thread page after selecting
      `Workflow` from the home input mode menu
    - workflow completed successfully with a booked room result and visible
      terminal footer state
    - refresh preserved both the final result and the workflow footer state
  - a temporary `Console ConnectionError / Failed to fetch` seen during testing
    was traced to the local LangGraph dev service becoming unreachable on
    `127.0.0.1:2024`, not to a frontend rendering bug
  - follow-up live verification on 2026-03-13 found one remaining backend gap:
    a truly queued run can sit in backend queue for a long time before
    `Starting background run`, but frontend does not receive a visible queued
    stage during that window; see
    `handoffs/frontend-to-backend.md` for the reopened blocker
  - backend confirmed on 2026-03-14 that the earlier queued-window repair did
    not actually use the live runtime persistence path
  - current backend rework now does the following without changing the
    frontend contract:
    - keeps the hook at the run-creation boundary
    - persists enqueue-time `queued` through both:
      - checkpointer/history
      - `Threads.get -> Threads.set_status`
    - patches the relevant run-creation entrypoints instead of only the source
      model function
    - preserves selector protection against same-run `queued -> acknowledged`
      regression
  - targeted backend regression verification for the repaired helper path is:
    - `56 passed in 27.87s`
  - frontend-side follow-up after backend redeploy should be:
    - keep consuming the same
      `run_id/workflow_stage/workflow_stage_detail/workflow_stage_updated_at`
      fields
    - rerun the live two-run busy-worker scenario
    - confirm `queued` appears before planner start
    - confirm refresh during the queued window still hydrates the same shell
  - latest frontend live verification after service restart on 2026-03-14 now
    confirms:
    - second run thread `bd65c991-c735-446d-8b77-c05f0fd1f820` visibly showed
      `已提交，正在排队启动...` around `2.2s` after submit
    - the same run later advanced into `planning`
    - immediate refresh during that queued window now restores the queued copy
    - frontend therefore considers both live queued visibility and queued
      refresh/reconnect hydration verified
  - latest backend diagnosis and repair on 2026-03-14:
    - the missing piece was that frontend refresh hydrates from
      `threads.getHistory(limit=1)`, which reads checkpoint history rather than
      only the thread-row mirror
    - backend now persists the enqueue-time queued shell into that same
      reconnect-safe history path before worker startup
    - frontend contract remains unchanged
  - final frontend live verification after backend redeploy confirmed:
    - second run thread `44d2a074-ad29-4522-a554-9adcf5b12110` showed
      `已提交，正在排队启动...` before planner start
    - refresh during that queued window preserved the queued shell
    - a follow-up live run later advanced from refreshed `queued` into
      `planning`

## [closed] Intervention flow backend implementation ready for frontend integration
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-flow.md`
- Blocking area: intervention card rendering and resolve action submission
- Backend question:
  backend has implemented the full Phase 1 intervention protocol. Frontend can
  now begin integration against the following contract.
- Frontend decision needed:
  confirm that the event/state contract below is sufficient for rendering
  `InterventionCard` and submitting resolutions.
- Backend contract summary:
  1. **New task status**: `WAITING_INTERVENTION` in `task_pool`
  2. **New stream event**: `task_waiting_intervention` with full
     `intervention_request` payload
  3. **Task fields added**:
     - `intervention_request` (InterventionRequest object)
     - `intervention_status` (`pending` | `resolved` | `consumed` | `rejected`)
     - `intervention_fingerprint` (string)
     - `intervention_resolution` (InterventionResolution object, after resolve)
  4. **Resolve endpoint**:
     `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
     Body: `{ fingerprint, action_key, payload }`
     Success: `{ ok, thread_id, request_id, fingerprint, accepted, resume_action, resume_payload }`
     Errors: 404 (not found), 409 (stale fingerprint), 422 (invalid action)
  5. **status_detail**: `@waiting_intervention` for localization
  6. **Hydration**: intervention state persists in `task_pool` and survives
     refresh/reconnect
- Suggested UI behavior:
  - treat `waiting_intervention` as highest-priority blocking state in footer
  - render `InterventionCard` from `intervention_request.action_schema`
  - submit resolution to the dedicated endpoint, not chat
  - handle 409 as stale (show "已过期" or similar)
- Notes:
  - backend does not hard-code action labels; frontend should render from
    `action_schema.actions[].label`
  - Phase 1 supports `button` and `input` action kinds only
  - `select` and `composite` are protocol-reserved but not rendered in Phase 1
  - the resolve endpoint returns a `resume_action` hint; the frontend submits
    the resume run via `thread.submit()` for SSE observability
  - frontend has confirmed this contract is sufficient for the current Phase 1
    implementation
  - clarified on 2026-03-17:
    - all Phase 1 `input` actions submit `payload.comment`
    - frontend may ignore `intervention_resolution` in Phase 1
    - `422/409/404` currently return simple `{ detail: string }` bodies
  - no additional frontend-blocking contract gaps remain for intervention flow

## [closed] Intervention resolve now returns resume hint instead of creating background run
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-flow.md`
- Blocking area: workflow continuation after intervention resolve
- Backend question:
  the original resolve endpoint called `client.runs.create()` to resume the
  workflow after resolve. This created an invisible background run that the
  frontend's SSE stream (`useStream`) could not observe, causing the UI to
  appear stuck at `WAITING_INTERVENTION` after clicking resolve.
- Backend changes:
  1. **Resolve endpoint** (`gateway/routers/interventions.py`):
     - removed `client.runs.create()` call
     - response now includes `resume_action` and `resume_payload` fields:
       ```json
       {
         "ok": true,
         "resume_action": "submit_resume",
         "resume_payload": { "message": "[intervention_resolved] request_id=... action_key=..." }
       }
       ```
     - for reject actions (`fail_current_task`), `resume_action` is `null`
  2. **Queue staging** (`agents/runtime_queue_stage.py`):
     - added `_is_intervention_resume_submission()` to skip enqueue-time
       workflow staging for intervention resume runs
     - prevents `task_pool` from being wiped when the resume run enters the
       queue
  3. **Frontend** (`intervention-card.tsx`):
     - after successful resolve with `resume_action === "submit_resume"`,
       calls `thread.submit()` to create an observable SSE-streamed resume run
     - uses `workflow_clarification_resume: true` context since
       `[intervention_resolved]` messages are treated as clarification-like
       resumes by the backend
- Frontend decision needed: none — fix already applied
- Notes:
  - the `[intervention_resolved]` message prefix is recognized by
    `latest_user_message_is_clarification_answer()` in `workflow_resume.py`
  - orchestration selector preserves workflow mode for these resume runs
  - planner detects RUNNING tasks and returns `execution_state: "RESUMING"`
  - all 395 backend tests pass (4 pre-existing failures unrelated)
  - all 73 frontend tests pass (4 pre-existing failures unrelated)

## [open] Display projection layer added to intervention requests
- Date: 2026-03-17
- Related feature: `features/workflow-intervention-display-projection.md`
- Blocking area: intervention card rendering
- Backend question:
  backend has added a `display` field to `InterventionRequest` that provides
  user-readable content for the intervention card. Frontend should prefer
  rendering from `display` over raw protocol fields.
- Frontend decision needed:
  confirm the `display` contract below is sufficient for rendering polished
  intervention cards.
- Backend contract summary:
  1. **New field**: `InterventionRequest.display` (optional `InterventionDisplay`)
  2. **Display schema** (`InterventionDisplay`):
     - `title` (string) — user-readable card title (e.g. "确认预定会议室")
     - `summary` (string, optional) — one-line description
     - `sections` (list of `InterventionDisplaySection`, optional) — structured
       label-value pairs grouped by section
     - `risk_tip` (string, optional) — risk/warning hint
     - `primary_action_label` (string, optional) — approve button text
     - `secondary_action_label` (string, optional) — reject button text
     - `respond_action_label` (string, optional) — input action button text
     - `respond_placeholder` (string, optional) — input placeholder text
     - `debug` (InterventionDisplayDebug, optional) — raw details for dev tools
  3. **Section schema** (`InterventionDisplaySection`):
     - `title` (string, optional) — section heading
     - `items` (list of `InterventionDisplayItem`) — each has `label` and `value`
  4. **Debug schema** (`InterventionDisplayDebug`):
     - `source_agent` (string, optional)
     - `tool_name` (string, optional)
     - `raw_args` (dict, optional)
  5. **Projection layers** (backend resolves in priority order):
     - Scenario-specific (e.g. meeting booking → polished business fields)
     - Operation-type (e.g. "创建操作" for create-like tools)
     - Generic fallback (humanized key-value pairs)
- Suggested UI behavior:
  - if `display` is present, render `display.title` as card title instead of
    `intervention_request.title`
  - render `display.sections` as structured content (label-value pairs grouped
    by optional section title)
  - if `display.primary_action_label` is set, use it as approve button text;
    otherwise fall back to `action_schema.actions[].label`
  - same for `secondary_action_label` and `respond_action_label`
  - collapse `display.debug` under a "详情" toggle, hidden by default
  - if `display` is absent (backward compatibility), fall back to existing
    rendering from raw `action_schema`
- Notes:
  - `display` is purely additive; all existing protocol fields remain unchanged
  - `display` does not affect the resolve endpoint contract
  - currently implemented scenario projections: meeting create/update/cancel
  - all other tools get operation-type or generic fallback projections
  - frontend test checklist: `workflow-intervention-display-projection-test-checklist.md`

## [open] Workflow timeline duplication rule
- Date: 2026-03-13
- Related feature: `features/workflow-realtime-chat.md`
- Blocking area: workflow timeline projection
- Backend question:
  when a workflow event is shown in the main timeline, should the frontend also
  keep showing the same task detail in the workflow card at full verbosity?
- Frontend decision needed:
  define the duplication strategy between main timeline and task panel.
- Suggested UI behavior:
  main timeline shows only high-signal summaries; task panel keeps full task
  detail.
- Notes:
  this affects how verbose backend message text should be.

## [open] Runtime steady-state additive intervention fields
- Date: 2026-03-19
- Related feature: `features/workflow-intervention-runtime-steady-state.md`
- Blocking area: optional intervention UI dedup / diagnostics enhancement
- Backend update:
  intervention payloads and task state now include additive optional fields for
  steady-state runtime identity:
  - `intervention_request.interrupt_kind`
  - `intervention_request.semantic_key`
  - `intervention_request.source_signal`
  - `task.pending_interrupt.interrupt_kind`
  - `task.pending_interrupt.semantic_key`
  - `task.pending_interrupt.source_signal`
  - `task.pending_interrupt.source_agent`
- Frontend impact:
  no blocking contract change. Existing rendering may continue to rely on
  current `fingerprint`, `intervention_request`, and `intervention_status`
  fields only.
- Suggested UI behavior:
  optionally prefer these fields for authoritative-card dedup, debugging, and
  stale-state diagnostics, but do not make business decisions that diverge from
  backend authoritative state.
- Notes:
  `fingerprint` remains the public compatibility field; the new fields are
  additive only.
