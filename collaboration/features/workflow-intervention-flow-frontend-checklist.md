# Workflow Intervention Flow Frontend Checklist

- Status: `draft`
- Owner: `frontend`
- Related feature: `workflow-intervention-flow.md`

## 1. Thread And Task Types

- [ ] Build strictly against the frozen Phase 1 protocol fields from
  `workflow-intervention-flow.md`
- [ ] Extend `frontend/src/core/threads/types.ts` with:
  - `WAITING_INTERVENTION`
  - `intervention_request`
  - `intervention_status`
  - `intervention_fingerprint`
- [ ] Extend task view models in `frontend/src/core/tasks/types.ts`
- [ ] Keep compatibility with existing workflow tasks
- Done when:
  - intervention state can hydrate from thread snapshot without custom casting

## 2. Task Event Ingestion

- [ ] Extend `frontend/src/core/tasks/adapters.ts` to map:
  - `WAITING_INTERVENTION` -> `waiting_intervention`
  - `task_waiting_intervention` event payload -> task upsert
- [ ] Extend `frontend/src/core/threads/hooks.ts` to merge intervention events
  by:
  - `run_id`
  - `task_id`
- [ ] Preserve current anti-stale behavior for older runs
- Done when:
  - an intervention event appears in the same workflow store path as other task
    states
  - old-run intervention events cannot pollute the current run

## 3. Workflow Summary Priority

- [ ] Update `frontend/src/components/workspace/workflow-progress.ts`
  priority order to put `waiting_intervention` above:
  - `waiting_clarification`
  - `waiting_dependency`
  - `in_progress`
- [ ] Add intervention summary copy and detail selection rules
- [ ] Keep existing workflow stage shell behavior intact
- Done when:
  - the footer summary surfaces intervention as the primary blocking action

## 4. Footer And Task Panel

- [ ] Update `frontend/src/components/workspace/workflow-footer-bar.tsx`
  primary task selection to prioritize intervention
- [ ] Update `frontend/src/components/workspace/task-panel.tsx`
  active-task logic to include `waiting_intervention`
- [ ] Ensure compact title/detail does not collapse important intervention
  context
- Done when:
  - intervention tasks are always chosen as the main visible workflow blocker

## 5. Generic Intervention Card

- [ ] Add or adapt a reusable `InterventionCard` in the workflow task surface
- [ ] Render from `intervention_request.action_schema` rather than
  scenario-specific booleans
- [ ] Support at least:
  - button actions
  - input actions
- [ ] Do not implement in Phase 1:
  - select-style actions
  - composite actions
- [ ] Respect `resolution_behavior` as backend-owned metadata and do not infer
  runtime semantics from action labels
- [ ] Show:
  - title
  - reason
  - description
  - context
  - action summary
  - risk level when present
- Done when:
  - approval, confirmation, and override-input scenarios all render through one
    component path

## 6. Timeline Integration

- [ ] Update
  `frontend/src/components/workspace/messages/message-group.tsx`
  to recognize intervention hints if backend emits them
- [ ] Keep the timeline lightweight and let task card remain the detail surface
- Done when:
  - main chat can acknowledge intervention without duplicating full details

## 7. Intervention Action Handling

- [ ] Implement one frontend action path for
  `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
- [ ] Submit:
  - `action_key`
  - `payload`
  - `fingerprint`
- [ ] Treat the dedicated resolve endpoint as frozen and do not fall back to
  generic chat submit
- [ ] Do not submit natural-language chat text for intervention resolution
- [ ] Keep optimistic updates minimal and let backend state remain authoritative
- [ ] Handle backend responses:
  - `200/202` accepted
  - `409` stale fingerprint
  - `422` invalid payload
- Done when:
  - UI does not need new code for every new intervention scenario
  - frontend implementation stays within the frozen Phase 1 scope

## 8. Copy And Localization

- [ ] Add intervention-related copy in:
  - `frontend/src/core/i18n/locales/zh-CN.ts`
  - `frontend/src/core/i18n/locales/en-US.ts`
- [ ] Cover:
  - waiting intervention
  - generic continue/resolve wording
  - action labels from schema fallback
  - validation errors for missing payload
- Done when:
  - intervention UI does not ship with hard-coded scenario text

## 9. Validation

- [ ] Verify thread hydration with `WAITING_INTERVENTION`
- [ ] Verify stream event merge for `task_waiting_intervention`
- [ ] Verify footer summary priority
- [ ] Verify schema-driven card rendering and action submission
- Validation:
  - `frontend/src/core/tasks/adapters.test.ts`
  - `frontend/src/core/threads/hooks.orchestration.test.tsx`
  - `frontend/src/components/workspace/workflow-progress.test.ts`
  - `frontend/src/components/workspace/workflow-footer-bar.test.tsx`
  - intervention card related tests
