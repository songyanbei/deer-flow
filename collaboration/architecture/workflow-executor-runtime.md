# Workflow Executor Runtime Notes

## Scope

This document describes the **currently implemented** workflow executor runtime
for `workflow` mode after the structured outcome and typed continuation
refactor.

It is not a proposal document. It is a description of the backend behavior that
is now present in code.

Relevant backend files:

- [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)
- [outcome.py](/E:/work/deer-flow/backend/src/agents/executor/outcome.py)
- [thread_state.py](/E:/work/deer-flow/backend/src/agents/thread_state.py)
- [workflow_resume.py](/E:/work/deer-flow/backend/src/agents/workflow_resume.py)
- [semantic_router.py](/E:/work/deer-flow/backend/src/agents/router/semantic_router.py)
- [task_complete_tool.py](/E:/work/deer-flow/backend/src/tools/builtins/task_complete_tool.py)
- [task_fail_tool.py](/E:/work/deer-flow/backend/src/tools/builtins/task_fail_tool.py)

Frontend impact:

- no new mandatory frontend logic is required
- current frontend still consumes `task_pool`, `status`, `status_detail`,
  `clarification_prompt`, `request_help`, and `intervention_request`

## What Changed Conceptually

Before this refactor, executor control flow mainly depended on scanning agent
message history and heuristically inferring whether the agent:

1. completed
2. needed dependency help
3. needed user clarification
4. needed human intervention

Now the runtime is designed around two explicit concepts:

1. `AgentOutcome`
   - structured result of the **current execution round**
2. `continuation_mode`
   - structured declaration of how a paused task should resume

This means:

1. old replayed terminal messages should not decide the current round's result
2. resume branch selection should not require guessing from old history

## Runtime Model

The workflow runtime now has three layers inside executor behavior.

### 1. Outcome Normalization

Implemented in:

- [outcome.py](/E:/work/deer-flow/backend/src/agents/executor/outcome.py)

Responsibility:

1. take the executor's message list
2. consider only the messages belonging to the current round
3. classify the round into one normalized outcome

Supported normalized kinds:

1. `complete`
2. `request_dependency`
3. `request_clarification`
4. `request_intervention`
5. `fail`

### 2. Continuation Selection

Implemented in:

- [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)

Responsibility:

1. read the current task
2. read `continuation_mode`
3. decide which resume branch should execute

Supported continuation modes:

1. `resume_tool_call`
2. `continue_after_dependency`
3. `continue_after_clarification`
4. `replan`

### 3. State Persistence

Implemented in:

- [thread_state.py](/E:/work/deer-flow/backend/src/agents/thread_state.py)

Responsibility:

1. persist interruption cause
2. persist pending tool execution intent
3. persist resume mode
4. preserve backward compatibility with existing workflow task projections

## AgentOutcome

## Purpose

`AgentOutcome` is the executor's normalized view of what happened in the
current execution round.

It is defined in:

- [outcome.py](/E:/work/deer-flow/backend/src/agents/executor/outcome.py)

## Current Types

### `complete`

Meaning:

1. the task finished successfully
2. executor may mark task `DONE`
3. executor may persist result to `verified_facts`

Fields:

- `result_text`
- `fact_payload`

### `request_dependency`

Meaning:

1. current agent cannot finish the task alone
2. another helper agent or dependency path is required

Fields:

- `help_request`

### `request_clarification`

Meaning:

1. user clarification is required before continuing
2. clarification is still represented in UI using current compatibility fields

Fields:

- `prompt`

### `request_intervention`

Meaning:

1. a risky side-effect tool or explicit intervention gate blocked execution
2. the task must pause for structured user resolution

Fields:

- `intervention_request`
- `pending_tool_call`

### `fail`

Meaning:

1. task cannot continue successfully
2. executor should mark the task as failed

Fields:

- `error_message`
- `retryable`

## Current Outcome Classification Rules

Normalization currently works as follows:

1. determine the boundary of the current round using `new_messages_start`
2. scan only `messages[new_messages_start:]`
3. prefer explicit terminal tool signals over heuristics

Current explicit terminal tools:

1. `intervention_required`
2. `request_help`
3. `ask_clarification`
4. `task_complete`
5. `task_fail`

If none of the explicit terminal tools appear in the current round:

1. fallback heuristics still exist for backward compatibility
2. current-round AI text may still be interpreted as implicit clarification or
   final completion

Important invariant:

- old replayed terminal tool messages outside the current round must not define
  the current round's `AgentOutcome`

This invariant is the main protection against the historical bug where an older
`request_help` re-opened a resolved dependency after the task had already
continued.

## Completion And Failure Tools

Two workflow-only builtin tools now exist:

### `task_complete`

Defined in:

- [task_complete_tool.py](/E:/work/deer-flow/backend/src/tools/builtins/task_complete_tool.py)

Purpose:

1. let workflow domain agents explicitly mark task success
2. reduce dependence on free-text completion inference

Payload:

- `result_text`
- `fact_payload?`

### `task_fail`

Defined in:

- [task_fail_tool.py](/E:/work/deer-flow/backend/src/tools/builtins/task_fail_tool.py)

Purpose:

1. let workflow domain agents explicitly mark terminal failure
2. reduce dependence on exception-only failure semantics

Payload:

- `error_message`
- `retryable?`

## Continuation State

Continuation state is stored on `TaskStatus`.

Defined in:

- [thread_state.py](/E:/work/deer-flow/backend/src/agents/thread_state.py)

### `continuation_mode`

Purpose:

1. declare how executor should resume the task
2. remove ambiguity from resume branch selection

Current values:

1. `resume_tool_call`
2. `continue_after_dependency`
3. `continue_after_clarification`
4. `replan`

### `pending_interrupt`

Purpose:

1. record the most recent unresolved interruption cause
2. provide structured pause context to backend runtime

Current shape:

- `interrupt_type`
- `request_id?`
- `fingerprint?`
- `prompt?`
- `options?`
- `source?`
- `created_at?`

Current supported interrupt types:

1. `dependency`
2. `clarification`
3. `intervention`

### `pending_tool_call`

Purpose:

1. store the tool call that must be executed after intervention approval
2. avoid depending purely on replayed agent message history

Current shape:

- `tool_name`
- `tool_args`
- `tool_call_id?`
- `idempotency_key?`
- `source_agent?`
- `source_task_id?`

### `agent_history_cutoff`

Purpose:

1. record the old/new boundary for restored agent message history
2. support observability and debugging of current-round classification

## Current Task Status Contract

Task statuses remain:

1. `PENDING`
2. `RUNNING`
3. `WAITING_DEPENDENCY`
4. `WAITING_INTERVENTION`
5. `DONE`
6. `FAILED`

Important compatibility rule:

- no new `WAITING_CLARIFICATION` status was introduced

Clarification remains represented as:

1. `status = RUNNING`
2. `status_detail = "@waiting_clarification"`
3. `clarification_prompt = ...`

This is intentional to keep frontend rendering compatible.

## Executor Flow

The executor now follows this model.

### Step 1: Start Task Execution

Executor reads:

1. task metadata
2. `continuation_mode`
3. `resolved_inputs`
4. any persisted agent history

### Step 2: Select Resume Branch

Branch selection order:

1. if `continuation_mode == "resume_tool_call"`
   - execute stored `pending_tool_call`
2. else if `continuation_mode == "continue_after_dependency"`
   - continue with `resolved_inputs`
3. else if `continuation_mode == "continue_after_clarification"`
   - continue with clarification answer
4. else
   - run normal task execution

Compatibility behavior:

- legacy tasks without `continuation_mode` may still fall back to older
  heuristics, such as `intercepted_tool_call`

### Step 3: Produce Current-Round Messages

Executor invokes the domain agent or continuation path and receives a message
sequence.

If prior history was restored, the executor tracks the cutoff point so that the
current round can be normalized correctly.

### Step 4: Normalize `AgentOutcome`

Executor calls:

- `normalize_agent_outcome(...)`

and gets:

1. `outcome`
2. `used_fallback`

### Step 5: Branch On `outcome.kind`

Current branch semantics:

1. `request_intervention`
   - task becomes `WAITING_INTERVENTION`
   - `continuation_mode = "resume_tool_call"`
   - `pending_interrupt.interrupt_type = "intervention"`
   - `pending_tool_call` is written
2. `request_dependency`
   - task becomes `WAITING_DEPENDENCY`
   - `continuation_mode = "continue_after_dependency"`
   - `pending_interrupt.interrupt_type = "dependency"`
3. `request_clarification`
   - task remains `RUNNING`
   - `status_detail = "@waiting_clarification"`
   - `continuation_mode = "continue_after_clarification"`
   - `pending_interrupt.interrupt_type = "clarification"`
4. `fail`
   - task becomes `FAILED`
   - continuation fields are cleared
5. `complete`
   - task becomes `DONE`
   - continuation fields are cleared

## Current Intervention Flow

The implemented intervention flow is:

1. agent attempts a side-effect tool call
2. intervention middleware intercepts it before execution
3. executor persists:
   - `status = WAITING_INTERVENTION`
   - `intervention_request`
   - `continuation_mode = "resume_tool_call"`
   - `pending_tool_call`
4. frontend resolves intervention through the existing endpoint
5. workflow resumes with `continuation_mode = "resume_tool_call"`
6. executor executes the stored tool directly
7. executor continues normal outcome normalization on the current round

This means the "confirm before booking meeting room" capability is implemented
as a first-class backend continuation flow, not a frontend-only simulation.

## Current Dependency Flow

The implemented dependency flow is:

1. a workflow agent emits `request_help`
2. executor persists:
   - `status = WAITING_DEPENDENCY`
   - `request_help`
   - `continuation_mode = "continue_after_dependency"`
   - `pending_interrupt.interrupt_type = "dependency"`
3. router creates or selects helper task(s)
4. helper task completes and writes `resolved_inputs`
5. parent task resumes
6. executor continues via dependency continuation branch

## Current Clarification Flow

The implemented clarification flow is:

1. a workflow agent emits `ask_clarification`
   or fallback logic classifies the current round as clarification
2. executor persists:
   - `status = RUNNING`
   - `status_detail = "@waiting_clarification"`
   - `clarification_prompt`
   - `continuation_mode = "continue_after_clarification"`
   - `pending_interrupt.interrupt_type = "clarification"`
3. frontend shows clarification UI using existing compatibility fields
4. user responds
5. workflow resumes through clarification continuation logic

## Backward Compatibility

The runtime currently preserves compatibility with older task shapes.

### Supported Compatibility Paths

1. old tasks with `intercepted_tool_call` but no `pending_tool_call`
2. old tasks with no `continuation_mode`
3. old agent behavior that still completes by final AI text rather than
   `task_complete`
4. old agent behavior that still fails via exception rather than `task_fail`

### Compatibility Intent

This compatibility layer exists to:

1. support older persisted checkpoints
2. allow incremental migration of workflow prompts and agents

It should not be treated as the preferred long-term protocol.

## How To Extend This Runtime

This section describes how to extend the runtime safely.

## Rule 1: Prefer Extending `AgentOutcome` Over Adding Heuristics

If a new workflow stop/continue condition is needed:

1. first ask whether it should be a new explicit `AgentOutcome.kind`
2. avoid adding new message-text heuristics unless strictly required for
   compatibility

Examples of acceptable future extensions:

1. `request_external_wait`
2. `partial_complete`
3. `handoff_required`

If a new kind is added:

1. update `outcome.py`
2. update executor branch logic
3. add explicit tests
4. add a new explicit tool if the agent needs a structured way to emit it

## Rule 2: Extend Continuation State, Not Frontend Contracts, First

If backend needs new resume behavior:

1. prefer adding or extending:
   - `continuation_mode`
   - `pending_interrupt`
   - `pending_tool_call`
2. only add frontend fields if the UI genuinely needs to render something new

This keeps control-flow extensibility backend-first and avoids unnecessary UI
coupling.

## Rule 3: Keep Existing UI Projection Fields Stable

If new backend control fields are added, do not remove or repurpose these
frontend-facing task fields unless a coordinated frontend migration is planned:

1. `clarification_prompt`
2. `request_help`
3. `intervention_request`
4. `intervention_status`
5. `status`
6. `status_detail`

New backend control fields should be additive.

## Rule 4: Prefer Structured Payloads For New Interrupt Types

If new interruption kinds are introduced, extend `pending_interrupt` using
optional fields rather than inventing free-form text contracts.

Current extension-safe pattern:

```python
pending_interrupt = {
    "interrupt_type": "...",
    "source": "...",
    "created_at": "...",
    # add optional typed fields here
}
```

Recommended future additions if needed:

1. `payload`
2. `expected_fields`
3. `policy_name`
4. `resume_token`

## Rule 5: Use `pending_tool_call` For Side-Effect Tool Evolution

If a side-effect tool workflow needs extra control, extend `pending_tool_call`
instead of encoding control metadata into message text.

Recommended future-safe additions:

1. `risk_level`
2. `approval_policy`
3. `execution_record_id`
4. `tool_version`
5. `confirmation_summary`

## Rule 6: New Agent Exit Paths Should Be Tool-Backed

If an agent needs a new explicit terminal behavior, prefer creating a bounded
tool over relying on AI free-text phrasing.

This keeps executor behavior deterministic.

## Extension Examples

### Example A: Add External Approval Queue

Do:

1. add a new interrupt flavor inside `pending_interrupt`
2. optionally add a new `continuation_mode`
3. add explicit executor/router handling

Avoid:

1. using plain AI text like "waiting for finance approval" as the only signal

### Example B: Add Partial Completion

Do:

1. add `partial_complete` to `AgentOutcome`
2. define exact task state semantics
3. decide whether task remains `RUNNING` or transitions into a new status

Avoid:

1. inferring partial completion from AI summary text

### Example C: Add Stronger Tool Idempotency

Do:

1. extend `pending_tool_call` with execution metadata
2. preserve `idempotency_key`
3. optionally persist a separate execution record

Avoid:

1. encoding retry semantics in user-facing text or prompt-only conventions

## Current Safe Field Extension Points

These fields are the preferred extension anchors:

### In `TaskStatus`

Safe additive extension points:

1. `continuation_mode`
2. `pending_interrupt`
3. `pending_tool_call`
4. `agent_history_cutoff`
5. `resolved_inputs`

### In `PendingInterrupt`

Safe additive extension points:

1. `payload`
2. `policy`
3. `ui_hint`
4. `retry_after`

### In `pending_tool_call`

Safe additive extension points:

1. `risk_level`
2. `approval_policy`
3. `execution_record_id`
4. `tool_metadata`

## What Should Not Be Extended Casually

Do not casually change:

1. meaning of existing task statuses
2. clarification compatibility semantics
3. intervention resolve API shape
4. event names already consumed by frontend
5. outcome classification priority without explicit regression review

## Operational Summary

The workflow executor runtime is now built around:

1. explicit current-round outcome normalization
2. explicit typed continuation state
3. structured side-effect tool resume
4. frontend-compatible task projection fields

This gives the backend a stable base for adding new workflow capabilities
without reintroducing the older "scan whole message history and guess what the
agent meant" failure mode.
