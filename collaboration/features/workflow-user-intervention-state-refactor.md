# Workflow User Intervention State Refactor

- Status: `draft`
- Owner suggestion: `backend` for runtime state semantics, `test` for regression coverage
- Related area: workflow mode, task state machine, user intervention, dependency routing

## Goal

Refactor workflow blocking-state semantics so that user-owned blocking steps are
modeled directly as `WAITING_INTERVENTION`, instead of first being written as
`WAITING_DEPENDENCY` and later upgraded by router logic.

This change should make the framework consistently distinguish:

- system-owned blocking: helper agents, dependency results, external capability waits
- user-owned blocking: input, selection, confirmation

## Why This Needs Frontend/Backend Collaboration

This is primarily a backend state-semantics refactor.

Frontend already supports:

- `WAITING_INTERVENTION`
- `single_select`
- `multi_select`
- `confirm`
- `input`
- intervention resolve flow

So the expected impact on frontend is:

- no protocol expansion required
- no new UI contract required
- only regression verification is needed

Unless backend changes the payload shape or event contract, frontend code should
not need modification.

## Current Behavior

### Backend

Current workflow runtime has two blocking states:

- `WAITING_DEPENDENCY`
- `WAITING_INTERVENTION`

But some user-owned interactions still originate from `request_help` and are
handled in two phases:

1. executor classifies `request_help` as `request_dependency`
2. executor writes task as `WAITING_DEPENDENCY`
3. router inspects `resolution_strategy` or `clarification_options`
4. router attempts to upgrade the same task to `WAITING_INTERVENTION`

This leaves room for invalid mixed states when reducer transition guards reject
`WAITING_DEPENDENCY -> WAITING_INTERVENTION`.

Observed failure mode:

- `status = WAITING_DEPENDENCY`
- `status_detail = @waiting_intervention`
- `intervention_status = pending`
- `intervention_request` exists

In that state, intervention resolve endpoint cannot find a pending
`WAITING_INTERVENTION` task and returns `404`.

### Frontend

Frontend renders intervention cards correctly when backend supplies an
authoritative `WAITING_INTERVENTION` task.

Frontend failure here is downstream:

- resolve endpoint returns `404`
- UI surfaces generic submit failure toast

Current evidence does not indicate a frontend payload bug for room selection.

## Contract To Confirm First

- Event/API:
  - workflow should continue to use existing intervention resolve endpoint
  - no new frontend API should be required
- Payload shape:
  - user selection / input / confirmation should still use existing
    `intervention_request.action_schema` and resolve payload contract
- Persistence:
  - user-owned blocking must persist as `WAITING_INTERVENTION`
  - system-owned blocking must persist as `WAITING_DEPENDENCY`
- Error behavior:
  - backend should not leave mixed task states where status and blocking payload disagree
- Dedup/replacement:
  - old router compatibility path may remain as fallback, but new tasks should not rely on
    dependency-to-intervention upgrade

## Backend Changes

- Normalize user-owned `request_help` into intervention semantics in executor
- Keep `request_help` only for true system dependency waits
- Reuse or extract shared help-request-to-intervention builders so executor and router
  do not duplicate protocol assembly
- Keep router fallback for old checkpoints or legacy state recovery
- Preserve existing intervention resolve contract

## Frontend Changes

- No planned code change by default
- Regression validation only:
  - room selection still renders as `single_select`
  - resolve success resumes the same task
  - no unexpected contract change

## Risks

- If executor-side classification is too broad, real dependency requests may be
  incorrectly upgraded to intervention
- If compatibility path is removed too early, old checkpoints may become
  unrecoverable
- If continuation semantics are changed together with blocking semantics,
  resume-path regressions may spread beyond this fix

## Acceptance Criteria

- User-owned blocking steps enter `WAITING_INTERVENTION` directly
- True system dependencies still enter `WAITING_DEPENDENCY`
- Meeting-room selection no longer produces mixed dependency/intervention state
- Intervention resolve endpoint no longer returns `404` for the active room-selection task
- Existing frontend intervention UI works without contract changes

## Open Questions

- Whether we should keep `continuation_mode = "continue_after_dependency"` for
  user-originated `request_help` conversions in the first implementation, or
  introduce a clearer continuation value later
- Whether temporary compatibility should explicitly allow
  `WAITING_DEPENDENCY -> WAITING_INTERVENTION` in reducer rules, or rely only on
  executor-side normalization
