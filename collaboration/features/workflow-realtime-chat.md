# Feature: Workflow Real-Time Chat Feedback

- Status: `draft`
- Owner suggestion: `backend` for protocol, `frontend` for rendering
- Related area: workflow mode chat experience

## Goal

Improve workflow-mode interaction so the user gets faster visible feedback in
the main chat area instead of only in workflow cards.

## User-Facing Expectations

1. Right after the user sends a question, the UI should show an immediate
   acknowledgement or greeting-like response.
2. During workflow execution, subtask progress should not exist only in the
   workflow card. Important progress should also appear in the main chat area.

## Current Behavior

### Backend

- Workflow mode emits custom events for orchestration resolution, workflow stage
  changes, and task state changes.
- Workflow executor emits status-level task events but not rich intermediate
  domain-agent chat output.

### Frontend

- Workflow events are converted into task state.
- Workflow task progress is shown in the task panel and subtask cards.
- Main message rendering intentionally filters workflow/task-like content in
  workflow mode.
- Optimistic UI exists for the user message and file uploads, but not yet for a
  workflow acknowledgement assistant message.

## Main Collaboration Question

How should workflow events be projected into the main chat timeline without
duplicating too much information or polluting persistent conversation history?

## Suggested Split

### Backend Responsibilities

- Decide whether timeline feedback is:
  - stream-only, or
  - persisted into thread messages/state
- Define one chat-facing event payload for workflow timeline messages
- Decide which backend emit points should create timeline items
- Clarify dedup/replacement rules

### Frontend Responsibilities

- Support a local optimistic acknowledgement message
- Render workflow chat events in the main timeline
- Avoid noisy duplication with task panel cards
- Apply replacement/collapse logic once authoritative backend events arrive

## Proposed Minimal Protocol

Backend can add one custom event for timeline use:

```ts
type WorkflowChatMessageEvent = {
  type: "workflow_chat_message";
  run_id: string;
  message_id: string;
  phase: "ack" | "task_started" | "task_progress" | "task_result";
  text: string;
  task_id?: string;
  agent_name?: string;
  replace?: boolean;
  created_at: string;
};
```

## Proposed Backend Insertion Points

- Selector
  - emit `ack` when workflow mode is resolved and accepted
- Router
  - emit `task_started` when a task is assigned to an agent
- Executor
  - emit `task_progress` for meaningful status transitions
  - emit `task_result` when a task is completed or fails

## Proposed Frontend Rendering Plan

- Keep task panel as the structured orchestration surface
- Add a lightweight timeline projection in the main message list
- For the first step, show only high-signal messages:
  - workflow acknowledged
  - task assigned
  - task completed
  - waiting for clarification

## Missing Capability Already Identified

- The current workflow executor does not forward rich intermediate domain-agent
  output. If product later wants very granular live subtask narration, backend
  must extend executor-side streaming behavior.

## Acceptance Criteria

- User sees an immediate feedback bubble after submitting in workflow mode.
- Main timeline shows key workflow progress updates in chronological order.
- Workflow card remains available and does not drift from timeline state.
- Timeline updates do not duplicate every internal task detail.

## Open Questions

- Should acknowledgement be frontend-only optimistic UI or backend-authored?
- Should timeline workflow messages be persisted or stream-only?
- What is the exact replacement rule between optimistic ack and backend ack?
