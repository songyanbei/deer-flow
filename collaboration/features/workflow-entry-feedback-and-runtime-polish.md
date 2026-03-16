# Feature: Workflow Entry Feedback And Runtime Polish

- Status: `done`
- Owner suggestion: `backend` for stage contract and runtime lifecycle, `frontend` for footer/task-shell rendering
- Related area: workflow mode first-screen feedback, queue/planning visibility, runtime recovery

## Goal

Make `workflow` mode feel responsive and explainable from the first screen, even
before `task_pool` is hydrated.

This feature focuses on the structured workflow surface, not the main chat
timeline bubble. The main objectives are:

1. right after a user question enters `workflow`, the page should show an
   immediate workflow acknowledgement and stage shell
2. when the runtime is queued or still planning, the page should show a clear
   workflow state instead of falling back to generic loading
3. after refresh/reconnect, the workflow stage should recover from thread state
   and remain consistent with the current run
4. workflow stage transitions should be stable enough to support later features
   such as timeline bubbles and complete HITL approval

## Why This Needs Frontend/Backend Collaboration

This requirement crosses the boundary between:

- backend orchestration selection, workflow stage emission, run lifecycle, and
  thread-state persistence
- frontend footer bar, task panel shell, hydration order, and reconnect
  recovery

Backend cannot finish this alone because “what to show first” is a rendering
decision. Frontend cannot finish this alone because the authoritative stage and
run lifecycle come from backend state and stream events.

## Background And Priority

This is the current first-priority workflow requirement.

It should be completed before:

- complete HITL approval flow
- richer workflow timeline bubbles in the main chat area
- parallel workflow scheduling

Reason:

1. complete HITL depends on a stable visible runtime state machine
2. chat bubbles should not be added before the underlying workflow stage model
   is stable
3. queue/planning ambiguity currently makes runtime issues hard to observe and
   debug

## Frontend Status Update

- Date: 2026-03-14
- Current frontend status: core integration implemented; browser-level
  regression coverage implemented; live integration is partially verified in a
  real browser session
- Completed on frontend:
  - explicit workflow submit now shows an immediate local `acknowledged` shell
    instead of a local-only `queued` placeholder
  - hydration and stream patch merging now use `run_id` as the primary
    isolation boundary
  - same-run stage updates advance by `workflow_stage_updated_at`
  - different `run_id` values never merge into the same workflow shell state
  - workflow shell remains the top-level summary surface; task waiting states
    now complement the shell detail instead of replacing the shell model
  - this feature still does not project workflow progress into main-chat
    bubbles
- Verified locally on frontend:
  - unit coverage for optimistic `acknowledged` shell
  - unit coverage for authoritative `queued` replacing optimistic
    `acknowledged` for the same run
  - unit coverage for same-run stage advancement
  - unit coverage for new-run replacement of stale shell state
  - render coverage for acknowledged/queued/planning shell states before
    `task_pool` hydration
- Verified in a real running app session:
  - correct manual path is: home chat page -> switch orchestration mode from
    `自动` to `Workflow` -> submit the request
  - real browser request used:
    - `我叫孙琦，帮我预定明天上午9-10点的会议室，10个人左右，产品介绍会`
  - real workflow run completed successfully and returned a meeting booking
    result
  - workflow footer remained visible with terminal summarizing/completed copy
  - refresh on the live thread page recovered the workflow shell and final
    result correctly
  - real busy-worker verification was attempted with two live `Workflow`
    submissions after a cold start
  - result:
    - the second run was genuinely queued in backend before it started
    - backend evidence showed:
      - second run created at `2026-03-13T08:57:50Z`
      - second background run started at `2026-03-13T08:59:10Z`
      - `run_queue_ms=79602`
    - but the frontend did not receive or display a visible `queued` shell
      during that waiting window
  - after the backend runtime repair was reworked and the stack was restarted,
    frontend reran the same live busy-worker scenario on 2026-03-14
  - result from the latest live run:
    - second run thread:
      - `44d2a074-ad29-4522-a554-9adcf5b12110`
    - visible queued copy now appears in the real page before planner start:
      - `已提交，正在排队启动...`
    - immediate refresh during that queued window now recovers the same queued
      shell from thread state/history
    - a follow-up live run confirmed that the same refreshed run later
      advances into `planning`
- Remaining before feature close:
  - no blocking items remain for this feature
  - non-blocking observation:
    tighter real-browser capture of the earliest transient
    `acknowledged`/pre-task shell state is still less stable than automated
    coverage because the supported meeting-booking flow completes quickly

## Backend Status Update

- Date: 2026-03-14
- Current backend status: live enqueue-time `queued` visibility and
  queued-window refresh recovery are both verified in a real browser session
- Confirmed root cause:
  - the previous repair relied on a `conn.store`-style write path that matched
    test doubles but did not match the live LangGraph runtime connection model
  - patching only `langgraph_api.models.run.create_valid_run` was not enough to
    guarantee interception of already-bound runtime entry call sites
  - after live queued visibility was repaired, frontend refresh still fell back
    to a bare `Workflow` shell because hydration uses
    `threads.getHistory(limit=1)`, which reads checkpointer history rather than
    the thread-row mirror written by `Threads.set_status`
- Current backend implementation:
  - enqueue-time `queued` now persists through the existing thread authority
    path by reading the current thread and writing merged `values` through
    `Threads.set_status`
  - the same enqueue-time `queued` now also persists into the existing
    checkpointer/history path before worker startup, so refresh/reconnect reads
    the same authoritative workflow shell instead of relying only on the live
    custom event or thread-row mirror
  - the runtime patch still hooks the run-creation boundary, but it now
    reuses the existing `ThreadState.workflow_stage*` contract instead of
    mutating a fake in-memory store branch
  - the same enqueue-time state still reuses the existing authoritative
    frontend fields:
    - `run_id`
    - `workflow_stage`
    - `workflow_stage_detail`
    - `workflow_stage_updated_at`
  - the same queued state is still emitted through the existing
    `workflow_stage_changed` stream contract
  - selector still preserves a same-run authoritative `queued` stage and does
    not regress it back to optimistic `acknowledged`
  - no new frontend-only fields or replacement event types were introduced
- Backend verification completed so far:
  - targeted regression coverage now verifies the repaired helper path and
    contract via:
    - `backend/tests/test_runtime_queue_stage.py`
    - `backend/tests/test_multi_agent_core.py`
  - current targeted verification result:
    - `56 passed in 27.87s`
- Remaining before backend can claim feature close:
  - no backend blocker remains for this feature

## Current Blocker Resolution Plan

### Backend Status

- previous live verification did not confirm the first repair, so backend has
  intentionally rolled back the old "already fixed" claim
- current runtime repair now targets the full real data flow:
  - run creation boundary remains the trigger point
  - real queue staging now persists through both:
    - checkpointer/history
    - `Threads.get -> Threads.set_status`
  - the same custom payload continues to flow through
    `workflow_stage_changed`
- runtime patch coverage now includes both the source function and the
  already-bound API entry references that create runs
- backend live-stack verification for this blocker is now complete:
  - refresh during the queued window hydrates queued from thread
    history/thread state

### Frontend Should Change

- no new payload contract is required
- keep the current run-scoped merge strategy:
  - hydration and stream patch merge remain keyed by `run_id`
  - queued continues to replace the optimistic local shell when it arrives as
    authoritative state
- after the repaired backend build is running, frontend should:
  - keep the existing live result as verification that queued is now visible
    before planner start
  - keep the latest live result as verification that queued survives refresh
    and then advances to planning without run mix-up
- frontend code changes are not expected if backend keeps the current fields:
  - `resolved_orchestration_mode`
  - `run_id`
  - `workflow_stage`
  - `workflow_stage_detail`
  - `workflow_stage_updated_at`

## Current Behavior

### Backend

- `orchestration_selector` already resolves `auto / leader / workflow` and can
  emit `orchestration_mode_resolved`
- selector already writes `workflow_stage = "acknowledged"` into thread state
  when the run is resolved to workflow
- planner, router, and executor already emit `workflow_stage_changed`
- `ThreadState` already contains:
  - `workflow_stage`
  - `workflow_stage_detail`
  - `workflow_stage_updated_at`
- `task_pool` is still the source of truth for workflow task execution, but it
  may remain empty for some time before planning finishes or before a worker
  starts the actual run
- runtime enqueue handling now patches true busy-worker queue visibility:
  - when a workflow run is accepted but cannot start immediately, backend
    persists and streams `workflow_stage=queued` before `Starting background
    run`
  - the queued window uses the same `run_id` that later advances into
    `planning`
  - selector detects this authoritative same-run queued state and avoids
    regressing it to `acknowledged`
- latest live frontend verification confirms:
  - the queued shell is now visible before planner start in a real busy-worker
    run
  - refresh during that queued window still does not restore the queued shell
    from thread state
- current runtime still has cases where:
  - the user is already in workflow mode
  - but the page has no subtask yet
  - and the UI can only show generic loading or a weak placeholder

### Frontend

- `WorkflowFooterBar` already exists and can render workflow-level progress
- workflow progress helpers already understand:
  - `queued`
  - `acknowledged`
  - `planning`
  - `routing`
  - `summarizing`
- local shell support already exists for an early workflow placeholder
- workflow task cards already recover from `task_pool`
- current gaps are:
  - first-screen feedback still feels weak when no task has been created yet
  - queued/planning states are not always strong enough as the primary user
    feedback
  - refresh/reconnect behavior is better than before, but early-stage shell
    consistency still depends on timing between hydration and stream events

## In Scope

1. workflow mode only
2. first-screen acknowledgement on the structured workflow surface
3. queued/planning/routing/executing/summarizing stage visibility
4. thread-state persistence and reconnect recovery for workflow stage
5. consistent precedence between:
   - thread-level `workflow_stage`
   - `execution_state`
   - `task_pool`
6. stage wording and stage-detail contract needed by frontend rendering
7. browser-level verification for:
   - immediate entry feedback
   - queued worker case
   - reconnect recovery

## Out Of Scope

1. complete HITL approval UI and approval decision flow
2. rich main-chat timeline bubbles for workflow progress
3. executor-side forwarding of full domain-agent intermediate content
4. parallel scheduling with `asyncio.gather`
5. redesign of legacy `leader` mode message rendering

## Target User Experience

### Happy Path

1. user sends a request
2. backend resolves this run to `workflow`
3. page immediately shows a workflow acknowledgement state
4. if execution has not started yet, page shows:
   - `queued` when waiting for runtime/worker
   - `planning` when planner is already working
5. once tasks are available, footer/task panel transitions into execution view
6. after tasks complete, page transitions into summarizing/completed view

### Worker Busy Path

1. user sends a workflow request while another long run is occupying the worker
2. `task_pool` is not yet available for the new run
3. page still shows a workflow shell with `queued` detail
4. when the worker starts the run, the shell transitions into `planning`
5. no generic “silent waiting” period remains

### Worker Busy Validation Standard

1. cold-start the local stack with a single worker
2. submit one real `Workflow` request that occupies the worker
3. immediately submit a second real `Workflow` request from another live page
4. expected backend evidence:
   - second run is created before it starts background execution
   - second run has measurable queue time
   - queued stage is persisted for that same `run_id` during the wait
5. expected frontend evidence:
   - second thread shows the queued shell copy instead of a silent placeholder
   - refresh during that wait still shows queued
   - once started, the same run advances into planning/executing normally

### Refresh/Reconnect Path

1. user refreshes during queued/planning/executing
2. frontend hydrates from thread state first
3. workflow shell and task panel recover the correct run stage
4. later stream events patch the state without replacing the shell incorrectly

## Contract To Confirm First

- Event/API:
  - continue to use `workflow_stage_changed` as the canonical stage event
  - continue to use `orchestration_mode_resolved` for mode resolution
  - do not require a chat-facing bubble event in this feature
- Payload shape:
  - authoritative fields are:
    - `run_id`
    - `workflow_stage`
    - `workflow_stage_detail`
    - `workflow_stage_updated_at`
- Persistence:
  - these fields must exist in thread state and be reconnect-safe
  - early workflow shell must not depend solely on ephemeral custom events
- Error behavior:
  - if planning fails before task creation, thread state must still expose a
    terminal stage/detail that frontend can present
- Dedup/replacement:
  - thread-level workflow shell is the primary surface before `task_pool`
  - once concrete tasks exist, task panel becomes richer, but stage shell
    remains the summary source

## Proposed Stage Model

The feature uses the following workflow-stage meanings:

- `acknowledged`
  - request has been accepted as workflow and the run is entering workflow
- `queued`
  - run has not started meaningful planner work yet, usually due to worker/run
    queueing or pre-planning wait
- `planning`
  - planner is decomposing or validating work
- `routing`
  - router is selecting or resuming the next task
- `executing`
  - executor/domain agent is currently running a task
- `summarizing`
  - planner is doing final validation/synthesis
- `null`
  - workflow shell is cleared because the run has finished or control moved out
    of workflow state

Additional visible states may continue to come from task status:

- `WAITING_DEPENDENCY`
- `WAITING_CLARIFICATION`
- `FAILED`

Those task-level states should not replace the workflow shell model. They should
complement it.

## Backend Changes

1. Formalize the workflow-stage lifecycle for one run:
   - selector sets `acknowledged`
   - backend moves to `queued` when the run is accepted but planner work has not
     materially started yet
   - planner sets `planning`
   - router/executor drive `routing` and `executing`
   - planner sets `summarizing` before final result
   - terminal completion clears or finalizes the stage consistently
2. Ensure every stage change carries:
   - `run_id`
   - `workflow_stage`
   - `workflow_stage_detail`
   - `workflow_stage_updated_at`
3. Ensure stage writes are persisted back into thread state, not only emitted as
   stream events
4. Make stage transitions monotonic per `run_id` so reconnect and patch merging
   stay predictable
5. Clarify what `queued` means operationally and from which backend point it is
   emitted
6. Add regression coverage for:
   - workflow acknowledged before `task_pool`
   - queued state before planner output
   - planning state without tasks
   - reconnect/hydration using persisted workflow stage
   - stage reset when a new run replaces the old one
7. Fix runtime copy/wording if needed so stage-detail text is user-readable and
   not too backend-internal

## Backend TODO

Status note on 2026-03-13:

- items A-F in this section are now substantially implemented in backend for
  the current phase
- the list below is retained as an implementation record and checklist for
  regression review, not as an indicator that the backend side is still blocked
- the remaining work for this feature is now primarily frontend live
  verification of the repaired busy-worker `queued` path and earliest-shell
  observation

### A. Run Identity And Stage Contract

1. Move `run_id` creation/assignment earlier so the first workflow-visible stage
   already has a stable run identity.
   - Target files:
     - `backend/src/agents/orchestration/selector.py`
     - `backend/src/agents/thread_state.py`
     - `backend/src/agents/entry_graph.py`
   - Goal:
     - `acknowledged` stage and related event/state carry `run_id`
     - frontend can safely merge early-stage shell by `run_id`

2. Standardize workflow-stage payload shape across selector/planner/router.
   - Required fields:
     - `run_id`
     - `workflow_stage`
     - `workflow_stage_detail`
     - `workflow_stage_updated_at`
   - Goal:
     - no stage event or persisted stage update is emitted without the same core
       identity fields

### B. Queued Stage

3. Decide and implement backend semantics for `queued`.
   - Decision needed:
     - real backend authoritative `queued`
     - or explicitly document that `queued` is a frontend local shell before
       planner starts
   - Preferred direction:
     - if backend can own it, emit/persist `queued` before planner starts real
       work
   - Constraint:
     - do not introduce a separate side-channel state model outside current
       `ThreadState + workflow_stage_changed`

4. If real backend `queued` is implemented, define the exact transition:
   - `acknowledged -> queued -> planning`
   - and document the emit point clearly
   - Target files likely include:
     - `backend/src/agents/orchestration/selector.py`
     - `backend/src/agents/planner/node.py`
     - possibly run-entry boundary code if graph-start delay must be modeled

### C. Planner / Router Stage Consistency

5. Make stage transitions monotonic and easy to reason about for one run.
   - Expected sequence:
     - `acknowledged`
     - `queued` if applicable
     - `planning`
     - `routing`
     - `executing`
     - `summarizing`
     - terminal clear/final state
   - Goal:
     - avoid ambiguous backward jumps unless they are intentional and documented

6. Revisit active-stage derivation when planner resumes with existing tasks.
   - Target file:
     - `backend/src/agents/planner/node.py`
   - Check:
     - whether `WAITING_DEPENDENCY` and pending-task situations should continue
       to map to `executing` / `routing`
     - whether stage-detail text is user-readable enough for footer shell

### D. Terminal And Error-State Polish

7. Define final behavior for workflow-stage on success.
   - Decision needed:
     - clear immediately after completion
     - or keep a short-lived final shell/detail for reconnect visibility
   - Constraint:
     - behavior must stay consistent with thread-state hydration

8. Define final behavior for workflow-stage on planning/validation failure.
   - Current gap:
     - planner currently clears stage on several error exits
   - Goal:
     - preserve enough terminal stage/detail for reconnect and visible failure
       explanation
   - Target file:
     - `backend/src/agents/planner/node.py`

9. Audit wording of `workflow_stage_detail` and `status_detail`.
   - Remove backend-internal or debug-like phrasing where it leaks to user UI
   - Keep wording compatible with the technical-route requirement of
     observability, but optimize text for end-user readability

### E. Recovery And Reset

10. Ensure new-run reset fully isolates old workflow shell state.
    - Target file:
      - `backend/src/agents/planner/node.py`
    - Goal:
      - new user turn resets stage/run metadata cleanly
      - no stale stage from previous run remains after reset

11. Ensure reconnect/hydration can rely on persisted thread state alone.
    - Goal:
      - early workflow shell does not depend only on stream timing
      - stage fields in thread state always match latest authoritative run state

### F. Tests

12. Add/extend backend tests for early workflow shell lifecycle.
    - Target files:
      - `backend/tests/test_multi_agent_core.py`
      - `backend/tests/test_entry_graph_routing.py`
      - `backend/tests/test_multi_agent_graph.py`
    - Required coverage:
      - `acknowledged` includes stable `run_id`
      - `queued` behavior if backend-owned
      - `planning` exists before `task_pool` hydration
      - reconnect/hydration can recover stage from thread state
      - new run clears old stage/run residue
      - terminal success/error stage behavior is deterministic

13. Add graph-level regression for worker-delay style early shell behavior.
    - Even if local runtime cannot fully simulate true queueing, tests should
      still verify the intended stage contract before task creation.

### G. Nice-To-Have But Not Blocking

14. Consolidate duplicated helper functions for workflow-stage updates if
    practical.
    - Current stage helpers are duplicated in selector/planner/router
    - Only do this if it does not destabilize current graph behavior
    - This is lower priority than contract completeness and tests

## Frontend Changes

1. Treat thread-level workflow shell as a first-class rendering source before
   any subtask exists
2. Show a stable footer/task-shell for:
   - `acknowledged`
   - `queued`
   - `planning`
   - `routing`
   - `executing`
   - `summarizing`
3. Do not wait for `task_pool` before rendering the first workflow feedback
4. Merge hydration and stream patches by `run_id` so old-stage residue does not
   leak into a new run
5. Make queued/planning/routing copy explicit enough that the user understands
   “the system accepted my request and is working”
6. Keep the main chat area clean in this feature; do not introduce timeline
   bubbles here
7. Add browser/E2E coverage for:
   - immediate workflow shell after submit
   - long-worker queued case
   - refresh during planning

### Frontend Implementation Notes

- Implemented:
  - local optimistic shell now starts at `acknowledged`
  - thread hydration and custom-event patch merge are both run-scoped by
    `run_id`
  - stage patch merge prefers newer `workflow_stage_updated_at` within one run
  - workflow shell title remains primary when task-level clarification or
    dependency detail is present
- Verified against a live app session:
  - homepage mode switch to `Workflow` persists into the real submitted run
  - real meeting-booking workflow produced a successful final answer in the
    main thread area
  - workflow footer displayed terminal completion/summarizing copy and
    preserved state after refresh
  - a real busy-worker queue was reproduced, but frontend could not show
    `queued` because the backend did not surface that state during the actual
    wait before background execution began
- Not yet completed:
  - no blocking implementation items remain
  - non-blocking observation gap:
    stable live capture of the very first transient `acknowledged` text before
    the meeting-booking run advances to completion

## Risks

- If stage semantics are fuzzy, frontend may render contradictory status between
  footer bar and task panel
- If thread-state persistence lags behind stream events, refresh/reconnect can
  regress into generic loading
- If `queued` is emitted too aggressively, users may see unnecessary extra stage
  transitions
- If this feature is mixed with timeline bubbles in the same iteration, the
  debugging surface will become much larger

## Acceptance Criteria

1. When a run resolves to `workflow`, the page shows visible workflow feedback
   before the first task card exists.
2. If the worker is busy and `task_pool` is still empty, the page shows a clear
   `queued` workflow state rather than generic loading.
3. When planner starts, the page transitions into `planning` without requiring
   task hydration first.
4. After tasks are created, the workflow shell and task panel remain consistent
   and do not contradict each other.
5. After refresh/reconnect during queued/planning/executing, the workflow shell
   recovers from thread state and patches forward correctly.
6. New runs do not inherit stale workflow shell state from the previous run.
7. This feature does not add main-chat workflow bubbles yet.

## Resolved Decisions

- `queued` is backend-authoritative in this feature; frontend should not derive
  it locally as the primary stage model
- terminal `summarizing` should remain visible until the next user turn/run
  replaces it
- waiting-dependency and waiting-clarification should complement the workflow
  shell detail, not replace the shell as the top-level stage summary
