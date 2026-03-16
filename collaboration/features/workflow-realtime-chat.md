# Feature: Workflow Real-Time Chat Feedback

- Status: `draft`
- Owner suggestion: `backend` for event contract, `frontend` for timeline rendering
- Related area: workflow mode main chat timeline

## Goal

When the user asks a question and the run enters `workflow`, the main chat area
should show real-time assistant-style bubbles instead of staying almost silent
until the final answer.

This feature is about the main chat timeline. It is intentionally separate from
`workflow-entry-feedback-and-runtime-polish.md`, which focuses on the workflow
footer/task-shell and runtime stage recovery.

## Dependency

This feature should be implemented after the workflow stage and first-screen
runtime shell are stable.

Reason:

1. timeline bubbles need a stable backend run/stage model
2. without a stable workflow shell, it is hard to decide what should live in
   the main timeline versus the task panel
3. complete HITL should also build on the same stable lifecycle

## User-Facing Expectations

1. after the user sends a question, the main chat area should show an immediate
   assistant bubble acknowledging that workflow has started
2. during execution, key progress should appear as chat bubbles in time order
3. the main timeline should surface only high-signal updates, not every
   low-level internal task detail
4. the task panel remains the structured detail surface; the timeline is the
   narrative surface

## Why This Needs Frontend/Backend Collaboration

Backend must define a dedicated chat-facing event contract. Frontend must define
how these events are rendered, replaced, collapsed, and deduplicated against
workflow cards.

Without a shared contract:

- backend will not know how verbose timeline text should be
- frontend will not know whether a bubble is optimistic, authoritative,
  replaceable, or persistent

## Current Behavior

### Backend

- workflow mode already emits:
  - `orchestration_mode_resolved`
  - `workflow_stage_changed`
  - task lifecycle events such as `task_started`, `task_completed`,
    `task_failed`, `task_waiting_dependency`, `task_resumed`
- these events are good for structured state, but not ideal as direct chat
  bubbles
- executor currently emits task-level status, but does not stream rich
  intermediate domain-agent narrative output into workflow chat

### Frontend

- workflow progress is already shown in footer/task cards
- workflow-like internal content is intentionally filtered out of the main
  timeline in workflow mode
- optimistic UI already exists for the user message and uploads
- there is not yet a stable workflow-assistant bubble contract for the timeline

## In Scope

1. workflow mode only
2. assistant-style timeline bubbles in the main chat area
3. high-signal event projection from backend workflow runtime to chat timeline
4. optimistic acknowledgement and backend replacement rules
5. dedup strategy between:
   - main timeline bubbles
   - footer bar summary
   - task panel cards

## Out Of Scope

1. full token-level streaming of domain-agent intermediate text
2. replacing the task panel with timeline-only UX
3. timeline persistence as real LangGraph `messages` in the first iteration
4. legacy `leader` mode timeline redesign
5. HITL approval UI

## Proposed User Experience

### Minimal First Version

The main timeline shows only these workflow bubble types:

1. `ack`
   - workflow accepted and starting
2. `task_started`
   - a meaningful task has been assigned and started
3. `task_progress`
   - only when the progress is meaningful to the user
   - examples:
     - waiting for dependency
     - waiting for clarification
     - resumed after helper result
4. `task_result`
   - a high-signal task outcome
5. `summary`
   - optional final workflow summary before the normal final answer, only if
     product needs it

### What Should Not Happen

- every internal task state should not become a bubble
- helper internals should not spam the chat timeline
- the same verbose detail should not appear both in bubble form and card form
- old-run bubbles should not leak into the next run

## Contract To Confirm First

- Event/API:
  - add a dedicated custom event for timeline projection
- Payload shape:
  - must include run identity and replacement semantics
- Persistence:
  - first version should be stream-only unless explicitly decided otherwise
- Error behavior:
  - timeline must still show failure/clarification bubbles for high-signal
    cases
- Dedup/replacement:
  - frontend needs a clear optimistic-ack replacement rule
  - task cards and timeline must not compete as equal-detail surfaces

## Proposed Minimal Protocol

```ts
type WorkflowChatMessageEvent = {
  type: "workflow_chat_message";
  run_id: string;
  message_id: string;
  phase:
    | "ack"
    | "task_started"
    | "task_progress"
    | "task_result"
    | "clarification"
    | "error";
  text: string;
  task_id?: string;
  agent_name?: string;
  replace?: boolean;
  ephemeral?: boolean;
  created_at: string;
};
```

## Proposed Backend Insertion Points

### Selector

- emit `ack` after workflow mode is resolved
- this is the authoritative replacement target for any optimistic frontend ack

### Router

- emit `task_started` when a task is assigned or resumed in a user-meaningful
  way
- emit `task_progress` for:
  - helper-routing start
  - dependency wait
  - resume after dependency resolution

### Executor

- emit `task_result` when a task completes or fails with a user-meaningful
  summary
- emit `clarification` when workflow escalates to top-level user clarification

## Proposed Rendering Rules

### Main Timeline

- show only high-signal workflow narrative bubbles
- keep bubble text short and user-readable
- render in chronological order within the current run

### Footer / Task Panel

- remain the structured detail surfaces
- keep full task detail, status fields, dependency data, and result structure

### Dedup Strategy

- timeline = summary
- task panel = detail
- if a task card already contains the full detail, timeline bubble should use a
  shorter sentence

## Backend Changes

1. Define and emit `workflow_chat_message`
2. Make payload text user-readable rather than debug-oriented
3. Ensure every bubble event includes `run_id`
4. Decide whether the first version is:
   - stream-only, recommended
   - or persisted into thread state/messages
5. Define replacement semantics for:
   - optimistic frontend ack
   - backend authoritative ack
6. Add regression coverage for:
   - ack event emission
   - task-started bubble emission
   - clarification bubble emission
   - no cross-run leakage by `run_id`

## Frontend Changes

1. Render optimistic acknowledgement bubble after submit when the request is
   expected to enter workflow
2. Replace or collapse the optimistic bubble when backend `ack` arrives
3. Render workflow timeline bubbles from `workflow_chat_message`
4. Keep task cards and footer as separate, structured surfaces
5. Avoid duplicate verbosity between timeline and task panel
6. Clear old optimistic/timeline bubbles when `run_id` changes
7. Add browser-level tests for:
   - optimistic ack replacement
   - ordered workflow timeline bubbles
   - clarification bubble rendering

## Missing Capability Already Identified

- executor still does not provide rich token-level domain-agent streaming inside
  workflow mode
- if product later wants “full real-time narration”, backend must extend
  executor-side streaming instead of only projecting status events

## Risks

- if backend text is too verbose, the main timeline will become noisy
- if optimistic ack has no strict replacement rule, users may see duplicate
  acknowledgement bubbles
- if stream-only events are treated as persisted history by mistake, reconnect
  behavior may become confusing
- if task cards and bubbles present the same detail at the same level, the page
  will feel duplicated

## Acceptance Criteria

1. In workflow mode, the user sees an immediate assistant-style acknowledgement
   bubble in the main timeline.
2. The main timeline shows key workflow progress in chronological order.
3. Timeline bubbles do not duplicate every internal task detail already shown in
   the task panel.
4. Clarification or major failure states can surface in the main timeline as
   user-readable bubbles.
5. Timeline bubble rendering is isolated by `run_id`, so old-run bubbles do not
   appear in a new run.
6. First-version workflow chat bubbles can be implemented without requiring full
   executor token streaming.

## Open Questions

- Should the first acknowledgement bubble be:
  - frontend optimistic first, then backend replace
  - or backend-only with no optimistic bubble?
- Should first-version timeline bubbles be stream-only, or should some of them
  be persisted into the thread history?
- Which task events are important enough to become bubbles, and which should
  stay only in the footer/task panel?
