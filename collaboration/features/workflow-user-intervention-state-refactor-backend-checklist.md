# Workflow User Intervention State Refactor Backend Checklist

- Status: `implemented`
- Owner: `backend`
- Related feature: `workflow-user-intervention-state-refactor.md`

## 1. Runtime Semantics

- [x] Confirm framework-level rule:
  - `WAITING_DEPENDENCY` means system-owned blocking only
  - `WAITING_INTERVENTION` means user-owned blocking only
- [x] Document that task blocking state is decided by blocker owner, not by
  terminal tool name alone
- Done when:
  - task-state semantics are explicit and stable in runtime code

## 2. Executor Normalization

- [x] Update `backend/src/agents/executor/executor.py`
- [x] In `request_dependency` handling, detect user-owned help requests via:
  - `resolution_strategy = user_clarification`
  - `resolution_strategy = user_confirmation`
  - `resolution_strategy = user_multi_select`
  - or bounded `clarification_options`
- [x] For user-owned help requests, do not write:
  - `status = WAITING_DEPENDENCY`
- [x] Instead, directly write:
  - `status = WAITING_INTERVENTION`
  - `status_detail = @waiting_intervention`
  - `intervention_request = ...`
  - `intervention_status = pending`
- [x] Keep true helper/capability waits on:
  - `WAITING_DEPENDENCY`
- Done when:
  - room-selection style user blocking enters intervention path directly from executor

## 3. Shared Protocol Builder

- [x] Extract help-request-to-intervention builder logic from
  `backend/src/agents/router/semantic_router.py`
- [x] Add reusable module for:
  - clarification option normalization
  - interaction kind resolution
  - intervention option construction
  - intervention question construction
  - intervention request construction
- [x] Reuse shared builder from executor
- [x] Reuse shared builder from router fallback path
- Done when:
  - executor does not depend on router-local helper implementations
- Implementation: `backend/src/agents/intervention/help_request_builder.py`

## 4. Router Compatibility Path

- [x] Keep existing router dependency-to-intervention upgrade path as fallback
- [x] Mark it as compatibility logic, not preferred main path
- [x] Ensure new runtime flow does not rely on router reclassification for fresh tasks
- Done when:
  - old checkpoints can still recover
  - new tasks do not require router to correct task semantic class

## 5. Task State Consistency

- [x] Verify no mixed state can remain after executor or router upsert where:
  - `status = WAITING_DEPENDENCY`
  - but `intervention_request` is active and `intervention_status = pending`
- [x] Decide whether reducer transition table needs temporary compatibility
  support for:
  - `WAITING_DEPENDENCY -> WAITING_INTERVENTION`
- [x] Decision: no temporary compatibility needed — the transition remains
  invalid.  The executor now writes `WAITING_INTERVENTION` directly from
  `RUNNING`, which is a valid transition.  The old invalid transition
  (`WAITING_DEPENDENCY -> WAITING_INTERVENTION`) was the root cause of
  mixed states and is intentionally kept blocked.
- Done when:
  - active task state always matches blocking payload semantics

## 6. Resolve Path Stability

- [x] Keep `backend/src/gateway/routers/interventions.py` resolve contract unchanged
- [x] Confirm resolve lookup continues to require:
  - `status = WAITING_INTERVENTION`
  - `intervention_status = pending`
- [x] Confirm no resolve-path broadening is introduced just to accommodate mixed states
- Done when:
  - intervention resolve remains authoritative and narrow

## 7. Frontend Impact Review

- [x] Confirm no frontend API contract changes are required
- [x] Confirm no new action kind is introduced
- [x] No backend output shape changes — existing intervention card contract
  is unchanged
- Done when:
  - frontend can continue using existing intervention card contract unchanged

## 8. Final Backend Sign-Off

- [x] User-owned blocking reaches `WAITING_INTERVENTION` directly
- [x] True dependencies still reach `WAITING_DEPENDENCY`
- [x] Existing intervention middleware path still works
- [x] Existing dependency helper path still works
- [ ] Main feature doc reflects final backend decision
