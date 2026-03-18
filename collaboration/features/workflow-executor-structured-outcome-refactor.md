# Feature: Workflow Executor Structured Outcome Refactor

- Status: `draft`
- Owner suggestion: `backend`
- Related area: workflow mode, executor reliability, interruption/resume, task state
- Frontend impact: `none required`

## Goal

Refactor workflow execution so that backend control flow is driven by
structured task outcomes and explicit continuation state, rather than by
heuristic inspection of agent message history.

This refactor is intended to solve the current class of failures where:

1. old `request_help` / `ask_clarification` / `intervention_required` signals
   can be mistaken for the current execution result
2. resume behavior depends on replaying prior messages and then guessing why
   the task was previously interrupted
3. side-effect tool confirmation flows are fragile under repeated resume
4. task continuation semantics are not explicit in persisted state

The first deliverable is **not** a full planner/router/runtime rewrite. The
first deliverable is a backend-only reliability refactor focused on:

1. explicit `AgentOutcome`
2. explicit `continuation_mode`
3. bounded message interpretation
4. deterministic intervention/dependency/clarification resume

The target is that a backend developer can implement this in stages without
changing the frontend contract, except for already-existing resume checkpoint
support.

## Why This Refactor Is Needed

Current workflow execution in:

- [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)

is fragile for three structural reasons:

### 1. Terminal result is guessed from free-form message history

Current logic:

- `_find_last_terminal_tool_signal(...)`
- `_looks_like_implicit_clarification(...)`

tries to infer whether the agent:

- completed
- needs dependency help
- needs user clarification
- needs intervention

This is unreliable when one execution round contains both:

- older terminal-like signals
- newer tool results
- trailing AI summaries

Confirmed production example:

- user chose a room
- system asked for meeting creation confirmation
- user approved
- backend correctly resumed and executed `meeting_createMeeting`
- agent returned success output
- executor still reclassified the task as `request_help` because an older
  room-selection `request_help` remained in the message history

### 2. Resume semantics are implicit, not modeled

Current persisted state already stores useful fields such as:

- `resolved_inputs`
- `intervention_request`
- `intervention_resolution`
- `intercepted_tool_call`
- `agent_messages`

However, the state does **not** explicitly declare:

- why the task is currently resumable
- what operation should run next
- which slice of history belongs to the prior interruption vs current round

As a result, resume behavior is reconstructed indirectly from old messages.

### 3. Message history currently acts as both trace and control source

Historical agent messages are valuable for debugging, but they should not be
the authoritative control-flow source for the workflow runtime.

The runtime should consume:

- structured task state
- structured pending interrupt state
- structured continuation mode
- structured current-round outcome

and keep message history as an audit/debug artifact only.

## Non-Goals For Phase 1 Of This Refactor

This document does **not** propose:

1. replacing the entire planner/router graph
2. rebuilding workflow into a full DAG scheduler
3. introducing full event sourcing now
4. redesigning the frontend task/intervention UI
5. changing the high-level intervention API already used by frontend
6. removing all current fallback heuristics in one PR

This is intentionally a staged backend refactor.

## Frozen Compatibility Requirements

The following current contracts must remain valid during this refactor.

### Frontend Contracts That Must Not Break

Do not break:

1. `task_pool` as the authoritative workflow task source
2. existing task statuses:
   - `PENDING`
   - `RUNNING`
   - `WAITING_DEPENDENCY`
   - `WAITING_INTERVENTION`
   - `DONE`
   - `FAILED`
3. existing intervention resolve endpoint:
   - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
4. existing event names currently used by frontend:
   - `task_running`
   - `task_waiting_dependency`
   - `task_help_requested`
   - `task_waiting_intervention`
   - `task_completed`
   - `task_failed`
   - `workflow_stage_changed`

### Frontend Changes Preferred To Be Avoided

Preferred approach:

1. no new mandatory frontend logic
2. no rename of existing event types
3. no change to current intervention-card submission flow

Allowed frontend impact only if necessary:

1. passive tolerance of extra backend fields
2. optional logging/debug display

## Implementation Strategy

Use a three-phase backend implementation:

1. introduce explicit `AgentOutcome`
2. introduce explicit continuation state on tasks
3. move executor branches from message-guessing to outcome-driven state

Each phase should land in a backward-compatible manner.

## Phase 1: Introduce `AgentOutcome`

### Objective

Create one normalized structured outcome object for each executor round.

The executor should stop making control-flow decisions directly from scattered
message checks. Instead it should:

1. gather the current round's output
2. normalize that output into `AgentOutcome`
3. switch on `outcome.kind`

### New Backend Type

Create a new type definition module:

- `backend/src/agents/executor/outcome.py`

Suggested shape:

```python
from typing import Any, Literal, NotRequired, TypedDict


class ToolIntent(TypedDict):
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: NotRequired[str | None]
    idempotency_key: NotRequired[str | None]
    source_agent: NotRequired[str | None]
    source_task_id: NotRequired[str | None]


class AgentOutcomeBase(TypedDict):
    kind: str
    messages: list[Any]
    new_messages_start: int


class CompleteOutcome(AgentOutcomeBase):
    kind: Literal["complete"]
    result_text: str
    fact_payload: dict[str, Any]


class RequestDependencyOutcome(AgentOutcomeBase):
    kind: Literal["request_dependency"]
    help_request: dict[str, Any]


class RequestClarificationOutcome(AgentOutcomeBase):
    kind: Literal["request_clarification"]
    prompt: str


class RequestInterventionOutcome(AgentOutcomeBase):
    kind: Literal["request_intervention"]
    intervention_request: dict[str, Any]
    pending_tool_call: ToolIntent | None


class FailOutcome(AgentOutcomeBase):
    kind: Literal["fail"]
    error_message: str
    retryable: bool


AgentOutcome = (
    CompleteOutcome
    | RequestDependencyOutcome
    | RequestClarificationOutcome
    | RequestInterventionOutcome
    | FailOutcome
)
```

Notes:

1. `messages` must be the full result message list for observability/debugging
2. `new_messages_start` must record the boundary between prior history and
   current-round messages
3. `pending_tool_call` is included only for intervention outcomes

### Required Behavior

Add a normalization function in:

- `backend/src/agents/executor/outcome.py`

Suggested API:

```python
def normalize_agent_outcome(
    *,
    task: TaskStatus,
    messages: list[Any],
    new_messages_start: int,
) -> AgentOutcome:
    ...
```

This function must only inspect messages from:

- `messages[new_messages_start:]`

It must **not** scan the entire replayed history when classifying the current
round's result.

### Outcome Classification Rules

Implement these rules in order:

1. if current-round messages contain `ToolMessage(name="intervention_required")`
   then outcome is `request_intervention`
2. else if current-round messages contain `ToolMessage(name="request_help")`
   then outcome is `request_dependency`
3. else if current-round messages contain `ToolMessage(name="ask_clarification")`
   then outcome is `request_clarification`
4. else if current-round messages contain `ToolMessage(name="task_complete")`
   then outcome is `complete`
5. else if current-round messages contain `ToolMessage(name="task_fail")`
   then outcome is `fail`
6. else fallback to legacy behavior for compatibility:
   - old explicit parser paths
   - then `_looks_like_implicit_clarification(...)`
   - then final AI text as `complete`

Important boundary:

- legacy fallback may inspect current-round text
- legacy fallback must **not** treat pre-existing terminal tool messages from
  replayed history as the result of the current round

### New Builtin Tool Requirement

Add a new builtin tool:

- `task_complete`

Suggested file:

- `backend/src/tools/builtins/task_complete_tool.py`

Suggested schema:

```python
{
  "result_text": str,
  "fact_payload": dict | None,
}
```

Purpose:

1. let domain agents explicitly mark a task as completed
2. reduce dependence on "final free text means success"

Optional but recommended in same phase:

- `task_fail`

Suggested schema:

```python
{
  "error_message": str,
  "retryable": bool | None,
}
```

### Tool Exposure Rules

Update:

- `backend/src/tools/tools.py`
- `backend/src/agents/lead_agent/agent.py`

Requirements:

1. workflow domain agents should receive `task_complete`
2. workflow domain agents may receive `task_fail`
3. existing tools remain available
4. current `request_help` and `ask_clarification` remain supported

### Prompt Contract Update

Update the workflow domain-agent instruction layer so agents are explicitly told:

1. when they need another agent, call `request_help`
2. when they need user clarification, call `ask_clarification`
3. when they need user confirmation before side effects, rely on
   intervention middleware / tool interception
4. when the task is successfully done, call `task_complete`
5. if task cannot be completed, call `task_fail`

This prompt update must be added where workflow domain-agent behavior is
currently described. Relevant current construction points include:

- `backend/src/agents/lead_agent/agent.py`
- any workflow/domain-agent prompt template modules currently used by executor

### Executor Refactor Scope In Phase 1

Refactor only the decision portion in:

- `backend/src/agents/executor/executor.py`

Current target sections:

1. `_find_last_terminal_tool_signal(...)`
2. `_looks_like_implicit_clarification(...)`
3. the `if terminal_tool_signal ...` branch chain

Do **not** delete the old helpers immediately. Instead:

1. keep them for fallback compatibility
2. stop using them as the primary control path

### Acceptance Criteria For Phase 1

1. executor uses `normalize_agent_outcome(...)` as the primary branching input
2. current-round classification ignores older replayed terminal signals
3. the meeting-booking intervention regression no longer reopens the older room
   selection question after successful booking
4. existing frontend contract remains unchanged

## Phase 2: Add Explicit Continuation State

### Objective

Persist why a task is resumable and what operation should happen next.

This phase removes the need to infer continuation semantics from old messages.

### `TaskStatus` Changes

Extend:

- [thread_state.py](/E:/work/deer-flow/backend/src/agents/thread_state.py)

Add the following fields only:

```python
ContinuationMode = Literal[
    "resume_tool_call",
    "continue_after_dependency",
    "continue_after_clarification",
    "replan",
]
```

```python
class PendingInterrupt(TypedDict):
    interrupt_type: Literal["dependency", "clarification", "intervention"]
    request_id: NotRequired[str | None]
    fingerprint: NotRequired[str | None]
    prompt: NotRequired[str | None]
    options: NotRequired[list[str] | None]
    source: NotRequired[str | None]
    created_at: NotRequired[str | None]
```

Add to `TaskStatus`:

```python
continuation_mode: NotRequired[ContinuationMode | None]
pending_interrupt: NotRequired[PendingInterrupt | None]
pending_tool_call: NotRequired[dict[str, Any] | None]
agent_history_cutoff: NotRequired[int | None]
```

Field semantics:

1. `continuation_mode`
   - authoritative declaration of how executor should resume
2. `pending_interrupt`
   - authoritative description of why execution paused
3. `pending_tool_call`
   - structured tool call to execute on intervention approval
4. `agent_history_cutoff`
   - number of prior messages restored into the current invocation
   - used for observability and safe current-round slicing

### Required State Writes

When executor produces `request_dependency`:

1. task status becomes `WAITING_DEPENDENCY`
2. `continuation_mode = "continue_after_dependency"`
3. `pending_interrupt.interrupt_type = "dependency"`

When executor produces `request_clarification`:

1. task status remains `RUNNING`
2. `continuation_mode = "continue_after_clarification"`
3. `pending_interrupt.interrupt_type = "clarification"`
4. `status_detail = "@waiting_clarification"`
5. keep using the existing persisted field:
   - `clarification_prompt`

Important compatibility note:

1. this phase does **not** introduce a new `WAITING_CLARIFICATION` task status
2. current frontend behavior depends on clarification remaining a `RUNNING`
   task with `status_detail = "@waiting_clarification"` and
   `clarification_prompt`

When executor produces `request_intervention`:

1. task status becomes `WAITING_INTERVENTION`
2. `continuation_mode = "resume_tool_call"` for `before_tool` intervention
3. `pending_interrupt.interrupt_type = "intervention"`
4. `pending_tool_call = outcome.pending_tool_call`

When task produces `complete` or `fail`:

1. `continuation_mode = None`
2. `pending_interrupt = None`
3. `pending_tool_call = None`

### Workflow Resume Semantics

Update:

- [workflow_resume.py](/E:/work/deer-flow/backend/src/agents/workflow_resume.py)

Requirements:

1. intervention resolution messages must only mark the workflow as resumable
2. they must not be treated as user clarification content
3. task resume eligibility should primarily read:
   - `continuation_mode`
   - `pending_interrupt`
   - `resolved_inputs`

Do not add new frontend-visible message protocols in this phase.

### Executor Resume Logic

Update:

- [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)

Execution entry rules:

1. if `continuation_mode == "resume_tool_call"`:
   - require `pending_tool_call`
   - execute the stored tool call directly
   - then continue agent execution from the tool result
2. if `continuation_mode == "continue_after_dependency"`:
   - continue with `resolved_inputs`
   - restore prior messages if needed
3. if `continuation_mode == "continue_after_clarification"`:
   - continue with clarification answer
4. otherwise:
   - run normal task execution

Important boundary:

The executor may still reuse prior agent messages for continuity, but
continuation selection must no longer depend on message inspection.

### Acceptance Criteria For Phase 2

1. executor resume branch is selected from `continuation_mode`
2. intervention resume never depends on scanning old tool messages
3. task interruption cause is visible directly in persisted task state
4. frontend still does not require code changes

## Phase 3: Structure Side-Effect Tool Continuation

### Objective

Upgrade `intercepted_tool_call` into a stable structured tool intent.

This phase is the bridge toward stronger idempotency and retry behavior without
rewriting the whole tool runtime.

### Replace / Alias Current Field

Current field in tasks:

- `intercepted_tool_call`

Transition target:

- `pending_tool_call`

Recommended compatibility plan:

1. write both fields temporarily
2. executor reads `pending_tool_call` first
3. fallback to `intercepted_tool_call` while old tasks still exist
4. remove `intercepted_tool_call` only after test coverage is complete

### `pending_tool_call` Shape

Suggested minimum structure:

```python
{
  "tool_name": str,
  "tool_args": dict[str, Any],
  "tool_call_id": str | None,
  "idempotency_key": str,
  "source_agent": str,
  "source_task_id": str,
}
```

### Idempotency Rule

For side-effect tools resumed after intervention approval:

1. generate `idempotency_key` at interception time
2. preserve it in task state
3. pass it through to tool execution if the underlying tool layer supports it

If the tool layer does not yet support idempotency keys, still persist the key
now. It will act as the future migration anchor and immediate debug context.

### Acceptance Criteria For Phase 3

1. intervention resume uses structured pending tool intent
2. side-effect tool resume path is deterministic
3. duplicated resume submission does not create ambiguous executor state

## Required File-Level Changes

This section is intentionally explicit.

### Must Change

1. [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)
   - primary refactor target
   - add outcome normalization integration
   - add continuation-mode dispatch
   - restrict current-round classification to new messages only
2. [thread_state.py](/E:/work/deer-flow/backend/src/agents/thread_state.py)
   - add `continuation_mode`
   - add `pending_interrupt`
   - add `pending_tool_call`
   - optionally add `agent_history_cutoff`
3. [workflow_resume.py](/E:/work/deer-flow/backend/src/agents/workflow_resume.py)
   - align resume detection with explicit continuation semantics
4. `backend/src/agents/executor/outcome.py`
   - new module
5. `backend/src/tools/builtins/task_complete_tool.py`
   - new module

### Likely Change

1. [tools.py](/E:/work/deer-flow/backend/src/tools/tools.py)
   - expose `task_complete`
2. [lead_agent/agent.py](/E:/work/deer-flow/backend/src/agents/lead_agent/agent.py)
   - include new completion tool for workflow domain agents
   - update workflow/domain-agent prompt instructions
3. [semantic_router.py](/E:/work/deer-flow/backend/src/agents/router/semantic_router.py)
   - preserve / consume new continuation fields where resume is triggered

### Should Not Need Frontend Changes

Preferred:

1. no change to [intervention-card.tsx](/E:/work/deer-flow/frontend/src/components/workspace/messages/intervention-card.tsx)
2. no change to existing workflow task rendering
3. no new frontend branching on `continuation_mode`
4. no new frontend handling for clarification state

Reason:

- continuation state is backend-internal control flow, not frontend presentation
- clarification must remain compatible with the current frontend projection:
  `status = RUNNING` + `status_detail = "@waiting_clarification"` +
  `clarification_prompt`

## Behavior Boundaries

These boundaries are intentionally strict.

### What Counts As Current-Round Output

If executor resumes using:

- `prior_messages + new HumanMessage(context)`

then:

1. `new_messages_start = len(prior_messages)`
2. only messages at or after that index may determine the new `AgentOutcome`

Older messages may be used for:

1. agent continuity
2. debugging
3. serialization persistence

Older messages must **not** determine:

1. current interruption reason
2. current completion/failure result
3. current dependency request

### What `continuation_mode` Must Mean

`continuation_mode` is not a UI hint.

It is the executor's authoritative instruction for the next runnable step.

It must not be overloaded for presentation or analytics.

### What `pending_interrupt` Must Mean

`pending_interrupt` describes the last unresolved blocking condition.

It must be cleared when:

1. resolution is accepted and consumed
2. the task completes
3. the task fails terminally

It must not be left stale after successful continuation.

Compatibility constraint:

1. `pending_interrupt` is backend-internal runtime state
2. existing frontend-visible fields such as:
   - `clarification_prompt`
   - `request_help`
   - `intervention_request`
   remain the authoritative UI projection inputs during this refactor

### What `task_complete` Must Mean

`task_complete` means:

1. the domain agent considers the current task done
2. executor may persist task result and mark status `DONE`

It must not be used for:

1. partial progress
2. "I think I am done but please interpret my text"

## Migration And Compatibility Rules

### Rule 1: Backward-Compatible Rollout

Each phase must preserve current behavior for tasks created before the new
fields exist.

Implementation expectation:

1. new fields are optional
2. executor must handle both:
   - old tasks without `continuation_mode`
   - new tasks with explicit continuation

### Rule 2: Fallbacks Stay Temporarily

Temporary compatibility is allowed:

1. keep `_find_last_terminal_tool_signal(...)`
2. keep `_looks_like_implicit_clarification(...)`
3. keep `intercepted_tool_call`

But these must become fallbacks, not the primary protocol.

### Rule 3: No Frontend Migration Block

Backend rollout must not require coordinated frontend deployment.

If extra backend fields appear in `task_pool`, frontend should simply ignore
them.

## Testing Plan

### Unit Tests To Add

Add or extend backend tests in:

- `backend/tests/test_multi_agent_core.py`

Required new test groups:

1. `normalize_agent_outcome` ignores old terminal signals from replayed history
2. intervention fast-path:
   - old `request_help`
   - new tool execution success
   - final classification must be `complete`, not `request_dependency`
3. dependency resume:
   - `continuation_mode = continue_after_dependency`
   - executor chooses correct branch
4. clarification resume:
   - `continuation_mode = continue_after_clarification`
   - intervention resume marker is not injected as clarification answer
5. intervention resume:
   - `continuation_mode = resume_tool_call`
   - stored `pending_tool_call` is executed directly

### Regression Test That Must Exist

Create a dedicated regression that models the exact confirmed bug:

1. task previously emitted room-selection `request_help`
2. user selected room and task resumed
3. task later emitted `intervention_required` for `meeting_createMeeting`
4. user approved
5. resumed executor executed the intercepted tool
6. agent produced success
7. executor must **not** return `request_help` from the older room-selection
   tool message

### Manual Validation

Use the real meeting-booking flow and verify:

1. user selects room once
2. system asks for booking confirmation once
3. user approves once
4. backend executes `meeting_createMeeting`
5. workflow finishes without re-asking room selection

## Observability Requirements

Keep or extend the logging already added during investigation.

At minimum, log:

1. current `continuation_mode`
2. `new_messages_start`
3. `outcome.kind`
4. whether fallback classification was used
5. whether current-round messages contained:
   - `request_help`
   - `ask_clarification`
   - `intervention_required`
   - `task_complete`
   - `task_fail`

Suggested new log shape inside executor:

```python
logger.info(
    "[Executor] Outcome normalized task_id=%s continuation_mode=%s "
    "new_messages_start=%s outcome_kind=%s used_fallback=%s",
    task["task_id"],
    task.get("continuation_mode"),
    new_messages_start,
    outcome["kind"],
    used_fallback,
)
```

## Risks

### Risk 1: Domain agents may not reliably call `task_complete`

Mitigation:

1. keep fallback complete detection during migration
2. update prompts clearly
3. add tests for configured workflow agents

### Risk 2: Continuation state may be partially written

Mitigation:

1. always write `continuation_mode`, `pending_interrupt`, and status together
2. clear them together on completion/failure

### Risk 3: Old tasks remain in persisted history

Mitigation:

1. keep backward-compatible fallback reads
2. avoid hard requirements on new fields until migration is complete

## Recommended Delivery Order

This order is required unless a developer documents a clear alternative.

1. add `AgentOutcome` module and normalization tests
2. add `task_complete` builtin tool
3. refactor executor to branch on `AgentOutcome`
4. add `continuation_mode` / `pending_interrupt` / `pending_tool_call`
5. refactor intervention fast-path to use continuation state
6. refactor dependency and clarification resume to use continuation state
7. add idempotency key to `pending_tool_call`

## Done Definition

This refactor is considered complete for its first reliable slice when:

1. executor primary control flow is driven by `AgentOutcome`
2. resume branch selection is driven by `continuation_mode`
3. old replayed terminal messages cannot change the result of the current round
4. the real meeting-booking regression is fixed
5. frontend behavior does not require additional changes to use the new backend
   runtime
