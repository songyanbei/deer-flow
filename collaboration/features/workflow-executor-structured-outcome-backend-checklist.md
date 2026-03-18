# Workflow Executor Structured Outcome Refactor Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature:
  - [workflow-executor-structured-outcome-refactor.md](/E:/work/deer-flow/collaboration/features/workflow-executor-structured-outcome-refactor.md)
- Frontend impact target: `none required`

## 0. Implementation Guardrails

- [ ] Do not break existing frontend task/event contracts
- [ ] Do not rename current workflow task statuses
- [ ] Do not require new frontend request fields
- [ ] Keep old fallback paths available during migration
- [ ] Keep rollout backward-compatible for existing persisted tasks
- Done when:
  - backend can be deployed without coordinating a frontend release

## 1. Add Structured Outcome Module

- [ ] Create:
  - `backend/src/agents/executor/outcome.py`
- [ ] Define:
  - `ToolIntent`
  - `CompleteOutcome`
  - `RequestDependencyOutcome`
  - `RequestClarificationOutcome`
  - `RequestInterventionOutcome`
  - `FailOutcome`
  - `AgentOutcome`
- [ ] Add `normalize_agent_outcome(...)`
- [ ] Enforce `new_messages_start` as an input to normalization
- [ ] Ensure normalization inspects only current-round messages
- [ ] Implement classification priority:
  - `intervention_required`
  - `request_help`
  - `ask_clarification`
  - `task_complete`
  - `task_fail`
  - legacy fallback
- Done when:
  - executor can branch on a single structured `outcome.kind`

## 2. Add Explicit Completion/Failure Tools

- [ ] Create builtin tool:
  - `backend/src/tools/builtins/task_complete_tool.py`
- [ ] Define payload:
  - `result_text`
  - `fact_payload?`
- [ ] Create builtin tool:
  - `backend/src/tools/builtins/task_fail_tool.py`
  - optional but strongly recommended in same phase
- [ ] Define payload:
  - `error_message`
  - `retryable?`
- [ ] Register tool loading in:
  - `backend/src/tools/tools.py`
- [ ] Expose tools only to workflow domain-agent paths
- Done when:
  - workflow task completion and failure no longer rely primarily on free-text inference

## 3. Update Workflow Domain-Agent Prompt Contract

- [ ] Update workflow/domain-agent instructions so agents are explicitly told:
  - use `request_help` for dependency requests
  - use `ask_clarification` for user clarification
  - rely on intervention middleware for risky side-effect tools
  - use `task_complete` when task is done
  - use `task_fail` when task cannot be completed
- [ ] Apply prompt update at the actual workflow domain-agent construction point(s)
- [ ] Ensure existing non-workflow/leader prompts are not unintentionally changed
- Done when:
  - workflow agents have one explicit success/failure exit path

## 4. Refactor Executor To Use Structured Outcome

- [ ] Update:
  - `backend/src/agents/executor/executor.py`
- [ ] Compute `prior_messages`
- [ ] Compute `new_messages_start`
- [ ] Normalize result through `normalize_agent_outcome(...)`
- [ ] Replace primary branching from:
  - `_find_last_terminal_tool_signal(...)`
  - `_looks_like_implicit_clarification(...)`
  - scattered terminal checks
  with:
  - `if outcome.kind == ...`
- [ ] Keep old helpers as compatibility fallback only
- [ ] Add explicit log:
  - `continuation_mode`
  - `new_messages_start`
  - `outcome.kind`
  - `used_fallback`
- Done when:
  - executor's primary control flow is outcome-driven, not message-guess-driven

## 5. Extend TaskState With Continuation Fields

- [ ] Update:
  - `backend/src/agents/thread_state.py`
- [ ] Add `ContinuationMode`
  - `resume_tool_call`
  - `continue_after_dependency`
  - `continue_after_clarification`
  - `replan`
- [ ] Add `PendingInterrupt` type
- [ ] Add task fields:
  - `continuation_mode`
  - `pending_interrupt`
  - `pending_tool_call`
  - `agent_history_cutoff` (recommended)
- [ ] Keep all new fields optional
- Done when:
  - persisted task state explicitly records how the next resume should work

## 6. Write Continuation State On Interrupt

- [ ] For dependency requests, write:
  - `status = WAITING_DEPENDENCY`
  - `continuation_mode = continue_after_dependency`
  - `pending_interrupt.interrupt_type = dependency`
- [ ] For clarification requests, write:
  - `status = RUNNING`
  - `status_detail = @waiting_clarification`
  - `clarification_prompt`
  - `continuation_mode = continue_after_clarification`
  - pending interrupt payload with prompt/options/source
- [ ] For intervention requests, write:
  - `status = WAITING_INTERVENTION`
  - `continuation_mode = resume_tool_call`
  - `pending_interrupt.interrupt_type = intervention`
  - `pending_tool_call`
- [ ] On terminal success/failure, clear:
  - `continuation_mode`
  - `pending_interrupt`
  - `pending_tool_call`
- Done when:
  - task interruption cause and resume mode are visible directly in `task_pool`

## 7. Upgrade Intervention Fast-Path State

- [ ] Keep current `intercepted_tool_call` temporarily for compatibility
- [ ] Start writing structured `pending_tool_call`
- [ ] Read `pending_tool_call` first in executor
- [ ] Fallback to `intercepted_tool_call` if the new field is absent
- [ ] Add `idempotency_key` generation to `pending_tool_call`
- Done when:
  - side-effect tool continuation has a stable state anchor

## 8. Refactor Resume Selection To Use Continuation Mode

- [ ] In `executor.py`, select execution branch from `continuation_mode`
- [ ] Implement branch:
  - `resume_tool_call`
  - execute stored tool directly, then continue agent
- [ ] Implement branch:
  - `continue_after_dependency`
  - continue with `resolved_inputs`
- [ ] Implement branch:
  - `continue_after_clarification`
  - continue with user clarification input
- [ ] Keep legacy resume behavior only as fallback for old tasks
- [ ] Preserve current clarification compatibility:
  - no new `WAITING_CLARIFICATION` status
  - keep `RUNNING + @waiting_clarification + clarification_prompt`
- Done when:
  - resume branch selection no longer depends on scanning old tool messages

## 9. Align Workflow Resume Helpers

- [ ] Update:
  - `backend/src/agents/workflow_resume.py`
- [ ] Keep intervention resume marker as control-flow signal only
- [ ] Do not inject intervention marker as clarification answer
- [ ] Prefer `continuation_mode` / `pending_interrupt` over message heuristics where possible
- [ ] Preserve existing clarification resume compatibility during migration
- Done when:
  - workflow resume semantics are explicit and deterministic

## 10. Router Compatibility Review

- [ ] Review:
  - `backend/src/agents/router/semantic_router.py`
- [ ] Ensure router preserves new continuation fields when resuming parent tasks
- [ ] Ensure router does not clear `pending_interrupt` or `continuation_mode` prematurely
- [ ] Ensure dependency resolution path sets continuation state consistently
- [ ] Do not require router/task state changes that would force frontend clarification rendering changes
- Done when:
  - router and executor agree on the task continuation contract

## 11. Backward Compatibility Fallbacks

- [ ] Keep `_find_last_terminal_tool_signal(...)` callable
- [ ] Keep `_looks_like_implicit_clarification(...)` callable
- [ ] Gate them behind fallback use only
- [ ] Ensure old tasks with no continuation fields still execute successfully
- [ ] Add comments documenting planned removal conditions
- Done when:
  - the refactor can be rolled out incrementally without breaking old threads

## 12. Logging And Observability

- [ ] Add outcome normalization log in executor
- [ ] Log whether fallback classification was used
- [ ] Log `continuation_mode` on task dispatch
- [ ] Log `pending_tool_call.tool_name` on intervention resume
- [ ] Log current-round message boundary
- Done when:
  - production incidents can identify whether the bug is in normalization, continuation, or tool resume

## 13. Required Regression Fix

- [ ] Fix the confirmed meeting-booking bug:
  - user selects room
  - system asks for booking confirmation
  - user approves
  - backend executes `meeting_createMeeting`
  - task must complete
  - backend must not reopen the old room-selection `request_help`
- Done when:
  - the same bug cannot be reproduced in logs or tests

## 14. Suggested PR Breakdown

- [ ] PR 1:
  - add `outcome.py`
  - add `task_complete` / `task_fail`
  - refactor executor primary classification
- [ ] PR 2:
  - add continuation fields to `TaskStatus`
  - write/read continuation mode in interrupt/resume paths
- [ ] PR 3:
  - upgrade intervention fast-path to `pending_tool_call`
  - add idempotency key
- [ ] PR 4:
  - cleanup and reduce legacy fallback usage
- Done when:
  - each PR can be reviewed and rolled back independently

## 15. Final Backend Sign-Off

- [ ] Executor primary control flow is `AgentOutcome`-driven
- [ ] Resume primary control flow is `continuation_mode`-driven
- [ ] Current-round classification cannot be poisoned by replayed old tool messages
- [ ] Existing frontend continues to work unchanged
- [ ] Clarification rendering behavior remains unchanged for frontend consumers
- [ ] All required backend tests pass
