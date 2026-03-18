# Workflow Executor Structured Outcome Refactor Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature:
  - [workflow-executor-structured-outcome-refactor.md](/E:/work/deer-flow/collaboration/features/workflow-executor-structured-outcome-refactor.md)
- Frontend impact expectation: `none required`

## 0. Test Scope Guardrails

- [ ] Validate refactor without requiring frontend code changes
- [ ] Keep existing workflow intervention UX contract intact
- [ ] Verify old persisted tasks remain resumable during migration
- Done when:
  - the refactor is proven compatible with current frontend behavior

## 1. Outcome Normalization Unit Tests

- [ ] Add direct unit coverage for:
  - `backend/src/agents/executor/outcome.py`
- [ ] Cover current-round-only classification:
  - old `request_help` in replayed history + new success path -> must not classify as dependency request
  - old `ask_clarification` in replayed history + new success path -> must not classify as clarification
  - old `intervention_required` in replayed history + new success path -> must not classify as intervention
- [ ] Cover explicit terminal tools:
  - `task_complete` -> `complete`
  - `task_fail` -> `fail`
  - `request_help` -> `request_dependency`
  - `ask_clarification` -> `request_clarification`
  - `intervention_required` -> `request_intervention`
- [ ] Cover fallback path:
  - no explicit terminal tool present
  - fallback still classifies sensibly for legacy agents
- Done when:
  - outcome normalization logic is deterministic under unit test

## 2. Executor Branching Tests

- [ ] Extend:
  - `backend/tests/test_multi_agent_core.py`
- [ ] Verify executor branches on `outcome.kind`, not old global history scan
- [ ] Verify `complete` marks task `DONE`
- [ ] Verify `request_dependency` marks task `WAITING_DEPENDENCY`
- [ ] Verify `request_clarification` follows existing clarification behavior
- [ ] Verify clarification compatibility is unchanged:
  - task status remains `RUNNING`
  - `status_detail = @waiting_clarification`
  - `clarification_prompt` is still populated
- [ ] Verify `request_intervention` marks task `WAITING_INTERVENTION`
- [ ] Verify `fail` marks task `FAILED`
- Done when:
  - executor state transitions match normalized outcomes exactly

## 3. Continuation Mode Tests

- [ ] Add coverage for:
  - `continuation_mode = resume_tool_call`
  - `continuation_mode = continue_after_dependency`
  - `continuation_mode = continue_after_clarification`
- [ ] Verify executor chooses the correct resume branch from state alone
- [ ] Verify old tasks without `continuation_mode` still work through fallback path
- [ ] Verify continuation fields are cleared on terminal success/failure
- Done when:
  - persisted task state is sufficient to drive resume behavior

## 4. Intervention Fast-Path Regression Tests

- [ ] Add a dedicated regression for the confirmed booking bug
- [ ] Required scenario:
  - original task asks user to select room
  - user selects room
  - task continues and triggers `intervention_required` before `meeting_createMeeting`
  - user approves
  - executor resumes via stored tool call
  - `meeting_createMeeting` succeeds
  - task completes
  - old room-selection `request_help` must not be re-triggered
- [ ] Assert:
  - no new `WAITING_DEPENDENCY`
  - no new `WAITING_INTERVENTION`
  - no second room-selection clarification after approval
- Done when:
  - the original production bug is covered end-to-end in backend tests

## 5. Clarification And Dependency Resume Regression Tests

- [ ] Re-run or extend existing clarification resume tests
- [ ] Re-run or extend dependency helper resume tests
- [ ] Verify intervention resume marker is never treated as clarification content
- [ ] Verify dependency resume still preserves `resolved_inputs`
- [ ] Verify clarification UI-facing fields remain unchanged so no frontend patch is needed
- Done when:
  - intervention refactor does not regress clarification or dependency paths

## 6. TaskState Persistence Tests

- [ ] Verify new task fields persist through reducer merges:
  - `continuation_mode`
  - `pending_interrupt`
  - `pending_tool_call`
  - `agent_history_cutoff`
- [ ] Verify valid status transitions still hold
- [ ] Verify continuation fields survive refresh/reconnect in thread state
- Done when:
  - new task metadata is durable in checkpointed state

## 7. API / Gateway Validation

- [ ] Verify intervention resolve endpoint still returns:
  - `ok`
  - `thread_id`
  - `request_id`
  - `fingerprint`
  - `accepted`
  - `checkpoint` if applicable
- [ ] Verify no frontend request-body change is required
- [ ] Verify resolve + submit + resume path still works with current frontend behavior
- Done when:
  - refactor does not alter the external intervention API unexpectedly

## 8. Log-Based Validation

- [ ] Verify executor logs include:
  - `continuation_mode`
  - `new_messages_start`
  - `outcome_kind`
  - `used_fallback`
- [ ] Verify logs clearly show current-round classification
- [ ] Verify the old misleading warning pattern no longer appears for the booking bug:
  - `honoring it as the terminal signal even though trailing messages were present`
- Done when:
  - logs are sufficient to explain outcome selection during incident review

## 9. Manual Workflow Validation

- [ ] Use real meeting-booking scenario
- [ ] Validate:
  - first user clarification asks for room choice once
  - user selects one room
  - system asks for booking confirmation once
  - user confirms once
  - booking tool executes once
  - workflow ends in success
  - system does not ask for room choice again
- [ ] Validate rejection flow:
  - confirmation rejected
  - task fails clearly
  - booking tool does not execute
- [ ] Validate refresh/reconnect during `WAITING_INTERVENTION`
- Done when:
  - real browser behavior matches backend unit-test expectations

## 10. Backward Compatibility Matrix

- [ ] Case A: old task without continuation fields
- [ ] Case B: new task with explicit continuation fields
- [ ] Case C: old agent without `task_complete` tool usage
- [ ] Case D: new agent using `task_complete`
- [ ] Case E: resume after dependency
- [ ] Case F: resume after clarification
- [ ] Case G: resume after intervention approval
- Done when:
  - migration safety is demonstrated across both old and new task shapes

## 11. Release Readiness

- [ ] All new unit tests pass
- [ ] Existing workflow regression tests still pass
- [ ] Manual booking scenario is verified on a real backend run
- [ ] No mandatory frontend patch is required to consume the backend rollout
- [ ] Existing clarification UI still works without frontend code changes
- [ ] Remaining gaps are written back to the feature doc if deferred
