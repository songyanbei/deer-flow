# Frontend Architecture Notes

## Scope

Frontend ownership is the `Next.js` app under `frontend/`.

## Current High-Level Structure

- App routes: `frontend/src/app/`
- Workspace/chat pages:
  - `frontend/src/app/workspace/chats/[thread_id]/page.tsx`
  - `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Chat rendering:
  - `frontend/src/components/workspace/messages/`
- Workflow/task UI:
  - `frontend/src/components/workspace/task-panel.tsx`
  - `frontend/src/components/workspace/messages/subtask-card.tsx`
- Thread streaming and submit flow:
  - `frontend/src/core/threads/hooks.ts`
- Task event adapters/store:
  - `frontend/src/core/tasks/adapters.ts`
  - `frontend/src/core/tasks/context.tsx`

## Current Workflow-Mode Behavior

- The frontend already receives custom workflow events in
  `frontend/src/core/threads/hooks.ts`.
- These events are converted into task view models and shown in workflow UI.
- In workflow mode, the main message list filters out workflow/task-like
  content in:
  - `frontend/src/components/workspace/messages/message-list.tsx`
  - `frontend/src/components/workspace/messages/workflow-message-filter.ts`

## Frontend Change Hotspots For Cross-Boundary Features

- `frontend/src/core/threads/hooks.ts`
  - submit behavior
  - optimistic messages
  - custom event handling
- `frontend/src/components/workspace/messages/message-list.tsx`
  - whether workflow events appear in the main timeline
- `frontend/src/components/workspace/messages/message-list-item.tsx`
  - rendering new message variants
- `frontend/src/core/tasks/adapters.ts`
  - mapping backend event payloads to UI state

## Frontend Risks

- Showing both workflow cards and timeline messages can create duplicate noise.
- Optimistic UI can drift from backend truth if IDs or replacement rules are
  not defined.
- If backend emits only status events, frontend may overfit by generating too
  much text locally.

## Frontend Collaboration Rule

If a feature needs new backend events, message fields, or persistence behavior,
frontend should not infer them silently. Record them in a feature doc or
handoff first.
