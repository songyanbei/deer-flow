# Workflow User Intervention State Refactor Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-user-intervention-state-refactor.md`

## 1. Executor Classification Coverage

- [ ] Add or extend backend tests for executor normalization
- [ ] Cover:
  - `request_help + user_clarification + clarification_options`
    -> `WAITING_INTERVENTION`
  - `request_help + user_confirmation`
    -> `WAITING_INTERVENTION`
  - `request_help + user_multi_select`
    -> `WAITING_INTERVENTION`
  - true helper request without user resolution strategy
    -> `WAITING_DEPENDENCY`
- Done when:
  - blocker ownership classification is deterministic under test

## 2. Room Selection Regression

- [ ] Add a regression test for the meeting-room selection scenario
- [ ] Verify:
  - parent task resumes after helper completion
  - room selection is emitted as intervention, not dependency
  - authoritative task status is `WAITING_INTERVENTION`
  - `intervention_request.kind` remains selection-oriented
- [ ] Verify resolve succeeds without `404`
- Done when:
  - previously broken room-selection path stays green

## 3. State Consistency Coverage

- [ ] Add assertions that no active pending intervention remains on a task with:
  - `status = WAITING_DEPENDENCY`
- [ ] If compatibility path remains, verify it only applies to legacy-state simulation
- [ ] If reducer transition table is relaxed temporarily, verify it does not mask
  fresh-task classification mistakes
- Done when:
  - mixed dependency/intervention states are prevented or explicitly bounded

## 4. Dependency Regression Coverage

- [ ] Re-run and extend dependency helper tests
- [ ] Verify:
  - helper lookup still writes `WAITING_DEPENDENCY`
  - helper completion still resumes parent as `RUNNING`
  - no false intervention is emitted for true system dependency waits
- Done when:
  - dependency flow remains stable after executor-side intervention normalization

## 5. Confirmation Flow Regression

- [ ] Re-run intervention confirmation tests
- [ ] Verify:
  - confirmation-style intervention still goes `RUNNING -> WAITING_INTERVENTION -> RUNNING`
  - `intervention_required` path behavior is unchanged
- Done when:
  - room selection and approval flow both use the same top-level intervention path

## 6. Resolve API Coverage

- [ ] Cover valid resolve for:
  - `single_select`
  - `multi_select`
  - `confirm`
  - `input`
- [ ] Cover error cases:
  - stale fingerprint
  - invalid action key
  - invalid payload
  - duplicate submit
- [ ] Specifically verify:
  - active room-selection resolve no longer returns `404` due to wrong task status
- Done when:
  - resolve behavior matches authoritative intervention state consistently

## 7. Frontend Regression Validation

- [ ] Validate no frontend code changes are required for this refactor
- [ ] Smoke-test existing intervention card behavior for:
  - single-select rendering
  - successful submit
  - resumed run visibility
- [ ] If any frontend contract drift is found, write it to:
  - `collaboration/handoffs/backend-to-frontend.md`
- Done when:
  - current frontend works against refactored backend without code changes

## 8. Manual Or End-To-End Validation

- [ ] Real workflow run:
  - helper resolves employee info
  - workflow asks user to select room
  - task shows `WAITING_INTERVENTION`
  - user selects room
  - workflow resumes successfully
- [ ] Refresh during pending room-selection intervention restores same blocking card
- [ ] Repeat click or duplicate submit does not corrupt state
- Done when:
  - real user journey works without relying on backend log inspection

## 9. Final Sign-Off

- [ ] Backend checklist items required for first rollout are closed
- [ ] Main feature doc still matches shipped semantics
- [ ] Any discovered frontend/API drift is documented through handoff or feature doc update
