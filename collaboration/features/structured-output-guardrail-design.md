# Structured Output Guardrail -- Technical Design

> Status: `design` | Author: claude | Date: 2026-03-24

## 1. Problem Statement

DeerFlow domain agents are expected to call terminal tools (`task_complete` / `task_fail` / `request_help`) to signal execution outcomes. However, LLMs occasionally "forget" and output plain text instead. When this happens, the executor falls back to `_looks_like_implicit_clarification()` — a keyword-based heuristic that is fundamentally fragile:

- **False positive**: A long HR attendance result ending with "如需查看详情请告诉我" triggers clarification detection, blocking the main workflow.
- **Narrow coverage**: `_COMPLETION_TEXT_MARKERS` only cover meeting-booking vocabulary (已预定/booked/confirmed), missing all other domains.
- **No positional awareness**: A question mark anywhere in 500 chars of substantive content causes interruption.

The `_is_trailing_followup()` patch (merged earlier) mitigates the worst case, but the root cause remains: **we are using text semantics to infer structured intent**.

### Design Goal

Replace heuristic text inference with a **closed-loop guardrail** that:
1. Detects when a domain agent violates the structured output contract (no terminal tool called)
2. Gives the agent one chance to self-correct via a focused nudge re-invocation
3. Applies a safe default policy when self-correction also fails
4. Is extensible to future guardrail types (output quality, safety, etc.)

---

## 2. Architecture Overview

```
executor_node
  |
  v
domain_agent.ainvoke()           # LangGraph agent internal loop
  |
  v
normalize_agent_outcome()        # Priority 1: terminal tool signals
  |                              # Priority 2: legacy fallback (used_fallback=True)
  v
+----------------------------------+
| OUTPUT GUARDRAIL GATE (new)      |  <-- insertion point
|                                  |
|  if used_fallback:               |
|    1. build_nudge_message()      |
|    2. domain_agent.ainvoke()     |  <-- one re-invocation with nudge
|    3. normalize_agent_outcome()  |  <-- re-classify
|    4. if still fallback:         |
|       apply_safe_default()       |  <-- default=complete, not clarification
+----------------------------------+
  |
  v
Branch on outcome.kind            # existing branching logic (unchanged)
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where to inject guardrail | Executor level (between normalization and branching) | The executor is the orchestration layer; agent internals stay untouched |
| Max retry count | 1 | If the LLM can't comply after one focused nudge, more nudges won't help; avoids latency spiral |
| Nudge message type | `HumanMessage` | LangChain agent treats these as conversational turns; SystemMessage may be ignored by some models |
| Safe default policy | `complete` (not `clarification`) | Aligns with LangGraph/AutoGen philosophy: no explicit signal = done. False-complete is much safer than false-interrupt |
| Guardrail scope | Domain agents only (`is_domain_agent=True`) | Top-level agent uses `ask_clarification` tool directly; no fallback path |

---

## 3. Module Structure

```
backend/src/agents/executor/
  guardrails/
    __init__.py              # public API: run_output_guardrails()
    base.py                  # OutputGuardrail ABC, GuardrailResult, GuardrailContext
    structured_completion.py # StructuredCompletionGuardrail implementation
    nudge.py                 # Nudge message templates (i18n-ready)
    safe_default.py          # Safe default policy when guardrail retry exhausted
```

---

## 4. Core Contracts

### 4.1 GuardrailContext

```python
@dataclass
class GuardrailContext:
    """Immutable snapshot of the execution state at guardrail evaluation time."""
    task: TaskStatus
    agent_name: str
    messages: list[Any]                 # Full message history from agent invocation
    new_messages_start: int             # Boundary for current-round messages
    outcome: AgentOutcome               # Result from normalize_agent_outcome
    used_fallback: bool                 # Whether classification relied on heuristics
    attempt: int                        # 0 = first check, 1 = after nudge retry
    agent_config: RunnableConfig        # For re-invoking the agent
    max_retries: int                    # Configurable per-guardrail (default=1)
```

### 4.2 GuardrailVerdict

```python
class GuardrailVerdict(str, Enum):
    ACCEPT = "accept"           # Outcome is fine, proceed to branching
    NUDGE_RETRY = "nudge_retry" # Inject nudge, re-invoke agent, re-classify
    OVERRIDE = "override"       # Replace outcome with guardrail-provided outcome
```

### 4.3 GuardrailResult

```python
@dataclass
class GuardrailResult:
    verdict: GuardrailVerdict
    guardrail_name: str
    reason: str                         # Human-readable explanation (for logging)
    nudge_message: HumanMessage | None  # Only for NUDGE_RETRY
    override_outcome: AgentOutcome | None  # Only for OVERRIDE
```

### 4.4 OutputGuardrail ABC

```python
class OutputGuardrail(ABC):
    """Base class for all output guardrails."""

    name: str = "base_guardrail"
    priority: int = 0  # Lower = evaluated first

    @abstractmethod
    def evaluate(self, ctx: GuardrailContext) -> GuardrailResult:
        """Evaluate whether this guardrail should intervene.

        Called ONCE per attempt (attempt=0 for initial, attempt=1 after retry).
        Must be idempotent and side-effect-free.
        """
        ...
```

---

## 5. StructuredCompletionGuardrail

This is the primary guardrail that solves the "domain agent forgot to call terminal tool" problem.

### 5.1 Trigger Condition

```python
def evaluate(self, ctx: GuardrailContext) -> GuardrailResult:
    # Only trigger for domain agents using fallback classification
    if not ctx.used_fallback:
        return GuardrailResult(verdict=ACCEPT, ...)

    # Attempt 0: nudge retry
    if ctx.attempt == 0:
        nudge = self._build_nudge(ctx)
        return GuardrailResult(
            verdict=NUDGE_RETRY,
            nudge_message=nudge,
            reason="Agent did not call terminal tool; nudging for structured output",
        )

    # Attempt 1 (after nudge): if still fallback, override to safe default
    return self._apply_safe_default(ctx)
```

### 5.2 Nudge Message Design

The nudge is the most critical component. It must be:
- **Directive**: Clear imperative, no ambiguity
- **Contextual**: Reference the agent's actual output so it can wrap it
- **Constrained**: Only allow the 3 terminal tools, nothing else

```python
NUDGE_TEMPLATE = """[STRUCTURED OUTPUT REQUIRED]

Your response was received but you did not call a required terminal tool.
Every task MUST end with exactly ONE of these tool calls:

1. task_complete(result_text="...") -- if your work is done
2. task_fail(error_message="...", retryable=True/False) -- if you cannot complete
3. request_help(problem="...", ...) -- if you need external data or user input

Your current output:
---
{agent_output_preview}
---

If this output represents a completed result, call task_complete now with this text as result_text.
If this output is asking a question that requires user input, call request_help with resolution_strategy="user_clarification".
If this output indicates a failure, call task_fail.

Do NOT repeat your previous work. Do NOT add new content. Simply call the appropriate tool."""
```

Key design choices:
- **Preview truncation**: Show first 500 chars of agent output to provide context without overwhelming the nudge
- **Explicit instruction for each case**: The agent knows exactly which tool to call for each scenario
- **"Do NOT repeat work"**: Prevents the agent from re-executing tool calls that already succeeded

### 5.3 Safe Default Policy

When the nudge retry also fails (agent still outputs plain text), apply a deterministic safe default:

```python
def _apply_safe_default(self, ctx: GuardrailContext) -> GuardrailResult:
    outcome = ctx.outcome
    agent_output = outcome.get("result_text", "") or outcome.get("prompt", "")

    # Safe default: treat as complete
    # Rationale: false-complete (user sees a result with trailing question)
    # is far less disruptive than false-interrupt (workflow blocked)
    override = CompleteOutcome(
        kind="complete",
        messages=ctx.messages,
        new_messages_start=ctx.new_messages_start,
        result_text=agent_output,
        fact_payload=_parse_json_object(agent_output) or {"text": agent_output},
    )
    return GuardrailResult(
        verdict=OVERRIDE,
        override_outcome=override,
        reason="Nudge retry exhausted; applying safe default (complete)",
    )
```

**Important**: The `_looks_like_implicit_clarification` heuristic is **not deleted**. It continues to exist but is only used for:
1. **Observability**: Log when the heuristic *would have* triggered clarification, for monitoring drift
2. **Metrics**: Track `guardrail_override_count` vs `heuristic_would_have_interrupted` to measure guardrail effectiveness

---

## 6. Guardrail Runner (Integration Point)

### 6.1 `run_output_guardrails()` Function

```python
async def run_output_guardrails(
    *,
    task: TaskStatus,
    agent_name: str,
    messages: list[Any],
    new_messages_start: int,
    outcome: AgentOutcome,
    used_fallback: bool,
    agent_config: RunnableConfig,
    make_agent_fn: Callable,      # make_lead_agent reference
    max_retries: int = 1,
) -> tuple[AgentOutcome, bool, dict[str, Any]]:
    """Run output guardrails and potentially re-invoke the agent.

    Returns:
        (final_outcome, final_used_fallback, guardrail_metadata)

    guardrail_metadata includes:
        - guardrail_triggered: bool
        - guardrail_name: str | None
        - nudge_attempted: bool
        - nudge_succeeded: bool
        - safe_default_applied: bool
        - original_outcome_kind: str
        - final_outcome_kind: str
    """
```

### 6.2 Integration into executor_node

The change to `executor_node` is minimal — insert the guardrail gate between normalization and branching:

```python
# Line ~978 (existing)
outcome, used_fallback = normalize_agent_outcome(
    task=task, messages=messages, new_messages_start=new_messages_start,
)

# NEW: Output guardrail gate
outcome, used_fallback, guardrail_meta = await run_output_guardrails(
    task=task,
    agent_name=agent_name,
    messages=messages,
    new_messages_start=new_messages_start,
    outcome=outcome,
    used_fallback=used_fallback,
    agent_config=agent_config_override,
    make_agent_fn=make_lead_agent,
)
# Update messages if guardrail re-invoked the agent
if guardrail_meta.get("nudge_messages"):
    messages = guardrail_meta["nudge_messages"]
    new_messages_start = guardrail_meta["nudge_new_messages_start"]

# Log guardrail activity
if guardrail_meta["guardrail_triggered"]:
    logger.info(
        "[Executor] Output guardrail fired: %s nudge_succeeded=%s safe_default=%s "
        "original_kind=%s final_kind=%s",
        guardrail_meta["guardrail_name"],
        guardrail_meta["nudge_succeeded"],
        guardrail_meta["safe_default_applied"],
        guardrail_meta["original_outcome_kind"],
        guardrail_meta["final_outcome_kind"],
    )
    record_decision("output_guardrail", ...)

# Line ~1009 (existing, unchanged)
outcome_kind = outcome["kind"]
```

---

## 7. Execution Flow Detail

### Scenario A: Agent calls task_complete (happy path)

```
agent.ainvoke() → messages with task_complete ToolMessage
  → normalize_agent_outcome() → kind=complete, used_fallback=False
  → run_output_guardrails() → ACCEPT (used_fallback=False, no guardrail needed)
  → branch: complete → verification → DONE
```

No additional cost. Guardrail is a no-op.

### Scenario B: Agent outputs plain text with trailing question (current bug)

```
agent.ainvoke() → messages with AIMessage("考勤20天...如需查看请告诉我")
  → normalize_agent_outcome() → kind=request_clarification, used_fallback=True
  → run_output_guardrails():
    → StructuredCompletionGuardrail.evaluate(attempt=0) → NUDGE_RETRY
    → Inject nudge HumanMessage
    → agent.ainvoke(messages + nudge) → new messages with task_complete("考勤20天...")
    → normalize_agent_outcome() → kind=complete, used_fallback=False
    → ACCEPT
  → branch: complete → verification → DONE

Guardrail metadata: nudge_attempted=True, nudge_succeeded=True
```

One extra LLM call, but the outcome is correct.

### Scenario C: Agent outputs plain text, nudge also fails

```
agent.ainvoke() → messages with AIMessage("考勤20天...如需查看请告诉我")
  → normalize_agent_outcome() → kind=request_clarification, used_fallback=True
  → run_output_guardrails():
    → StructuredCompletionGuardrail.evaluate(attempt=0) → NUDGE_RETRY
    → Inject nudge HumanMessage
    → agent.ainvoke(messages + nudge) → AIMessage("好的,如需其他帮助请告诉我")
    → normalize_agent_outcome() → kind=request_clarification, used_fallback=True
    → StructuredCompletionGuardrail.evaluate(attempt=1) → OVERRIDE(complete)
  → branch: complete → verification → DONE

Guardrail metadata: nudge_attempted=True, nudge_succeeded=False, safe_default_applied=True
```

Two extra LLM calls in worst case, but workflow is never blocked.

### Scenario D: Agent genuinely needs user input but uses request_help correctly

```
agent.ainvoke() → messages with request_help ToolMessage
  → normalize_agent_outcome() → kind=request_dependency, used_fallback=False
  → run_output_guardrails() → ACCEPT
  → branch: request_dependency → ...
```

No guardrail triggered. Correct behavior.

### Scenario E: Agent genuinely needs user input but outputs plain text question

```
agent.ainvoke() → messages with AIMessage("请问您要查哪个月的考勤？")
  → normalize_agent_outcome() → kind=request_clarification, used_fallback=True
  → run_output_guardrails():
    → StructuredCompletionGuardrail.evaluate(attempt=0) → NUDGE_RETRY
    → Inject nudge HumanMessage
    → agent.ainvoke(messages + nudge)
    → agent calls request_help(resolution_strategy="user_clarification", ...)
    → normalize_agent_outcome() → kind=request_dependency, used_fallback=False
    → ACCEPT
  → branch: request_dependency → WAITING_INTERVENTION
```

Nudge converts a fragile implicit clarification into a proper structured signal.

---

## 8. Guardrail Configuration

### 8.1 Agent-Level Config (config.yaml per agent)

```yaml
# backend/.deer-flow/agents/meeting-agent/config.yaml
name: meeting-agent
domain: meeting
guardrails:
  structured_completion:
    enabled: true          # default: true for all domain agents
    max_retries: 1         # default: 1
    safe_default: complete # "complete" | "fail" (default: "complete")
```

### 8.2 Global Defaults (agents_config.py)

```python
class AgentConfig:
    # ... existing fields ...
    guardrail_structured_completion: bool = True
    guardrail_max_retries: int = 1
    guardrail_safe_default: str = "complete"
```

### 8.3 Feature Flag

For rollout safety, the entire guardrail system can be disabled:

```python
# In executor_node, before calling run_output_guardrails:
guardrail_enabled = agent_cfg and getattr(agent_cfg, "guardrail_structured_completion", True)
if not guardrail_enabled:
    # Skip guardrails, use existing behavior
    pass
```

---

## 9. Observability

### 9.1 Structured Logging

Every guardrail evaluation emits a structured log:

```
[Executor] [Guardrail] structured_completion trigger=True attempt=0 verdict=nudge_retry
  task_id=task-123 agent=hr-agent original_kind=request_clarification
[Executor] [Guardrail] structured_completion trigger=True attempt=1 verdict=override
  task_id=task-123 agent=hr-agent nudge_succeeded=False safe_default=complete
```

### 9.2 Decision Records

```python
record_decision(
    "output_guardrail",
    run_id=task_run_id,
    task_id=task["task_id"],
    agent_name=agent_name,
    inputs={
        "original_outcome_kind": original_outcome["kind"],
        "used_fallback": True,
        "agent_output_preview": agent_output[:300],
    },
    output={
        "guardrail_name": "structured_completion",
        "nudge_attempted": True,
        "nudge_succeeded": False,
        "safe_default_applied": True,
        "final_outcome_kind": "complete",
    },
)
```

### 9.3 Shadow Mode Metrics

For the first rollout, the guardrail can run in **shadow mode**: evaluate but don't intervene, only log what *would* have happened. This allows measuring:
- How often the guardrail would trigger (per agent, per domain)
- What percentage of nudges would succeed
- How often the safe default would change the outcome vs. the heuristic

---

## 10. Relationship to Existing Systems

### 10.1 vs. `_looks_like_implicit_clarification`

The heuristic is **not removed**. Instead, its role changes:

| Before | After |
|--------|-------|
| Used for classification decisions | Used for shadow metrics only |
| Controls whether workflow is interrupted | Only logged, never acts |
| In the critical path | Side channel for observability |

The heuristic result is still computed and logged alongside the guardrail result, allowing us to measure divergence and confirm the guardrail is performing better.

### 10.2 vs. Verification Gate (Phase 4)

The verification gate (lines 1428-1495 in executor.py) runs **after** the guardrail, on the `complete` branch only. They are complementary:

```
normalize_agent_outcome → OUTPUT GUARDRAIL → branch on kind
                                                |
                                          [complete branch]
                                                |
                                          VERIFICATION GATE
                                                |
                                          DONE / FAILED
```

- **Guardrail**: Ensures the agent produces a *structurally valid* outcome (called a terminal tool)
- **Verification**: Ensures the outcome is *semantically correct* (result makes sense for the task)

### 10.3 vs. ToolCallLimitMiddleware

The `ToolCallLimitMiddleware` limits how many tool calls the agent can make *within one invocation*. The guardrail adds at most 1 additional invocation (not additional tool calls within the same invocation). The tool call budget resets for the nudge re-invocation, but since the nudge asks the agent to make exactly 1 tool call, this is safe.

### 10.4 vs. Prompt Discipline Rules

The prompt already says (prompt.py:293-296):
```
ALWAYS call task_complete when you have successfully finished
ALWAYS call task_fail when you encounter an unrecoverable error
NEVER end with a plain text response without calling task_complete or task_fail
```

The guardrail is the **enforcement mechanism** for these rules. The prompt sets the expectation; the guardrail ensures compliance.

---

## 11. Edge Cases and Robustness

### 11.1 Nudge re-invocation triggers intervention middleware

If the agent calls a risky tool during nudge re-invocation (unlikely but possible), the `InterventionMiddleware` will intercept it normally. The guardrail should detect that the re-invoked result has `used_fallback=False` and accept it, regardless of what `outcome_kind` it is.

### 11.2 Nudge re-invocation exceeds tool call limit

The nudge creates a fresh agent instance, so the tool call counter resets. The nudge is expected to result in exactly 1 tool call. If the agent somehow makes many tool calls, the `ToolCallLimitMiddleware` will stop it.

### 11.3 Nudge re-invocation raises an exception

Wrap the re-invocation in try/except. On failure, fall through to `_apply_safe_default()` — treat as if nudge failed.

### 11.4 Agent is already in resume/continuation mode

Guardrails should apply regardless of continuation mode. The trigger condition (`used_fallback=True`) is the same whether this is a fresh invocation or a resumed one.

### 11.5 Nudge message language

The nudge is in English (the LLM's instruction-following language). The agent's output may be in Chinese or any language — that's fine, the nudge only asks the agent to call a tool, not to change its content language.

### 11.6 Cost and latency

- **Best case** (agent calls terminal tool): Zero additional cost. Guardrail check is a simple `if used_fallback` boolean test.
- **Nudge succeeds** (1 retry): One additional LLM call. Since the nudge asks for exactly 1 tool call with no new work, this is a short/cheap call.
- **Nudge fails** (safe default): Two additional LLM calls total. This is the worst case and should be rare.

Based on the live benchmark data, the fallback path triggers in ~40% of domain agent executions (before prompt improvements). With prompt discipline already in place, we expect ~10-15% trigger rate, of which ~90% should succeed on first nudge.

---

## 12. Extensibility: Future Guardrails

The framework supports adding new guardrails via the `OutputGuardrail` ABC:

| Guardrail | Trigger | Action | Priority |
|-----------|---------|--------|----------|
| `StructuredCompletionGuardrail` | `used_fallback=True` | Nudge + safe default | 0 (highest) |
| `OutputQualityGuardrail` (future) | Result too short / generic | Nudge for elaboration | 10 |
| `ConfidenceGuardrail` (future) | LLM uncertainty markers | Request verification | 20 |
| `SafetyGuardrail` (future) | Harmful content detected | Override to fail | -10 |

Guardrails are evaluated in priority order. The first `NUDGE_RETRY` verdict wins (only one retry is allowed). Multiple `OVERRIDE` verdicts are resolved by priority.

---

## 13. Implementation Plan

### Phase 1: Core Framework + StructuredCompletionGuardrail

1. Create `backend/src/agents/executor/guardrails/` module structure
2. Implement `base.py` (ABC, context, result types)
3. Implement `nudge.py` (nudge message templates)
4. Implement `structured_completion.py` (main guardrail)
5. Implement `__init__.py` (runner function)
6. Integrate into `executor_node` (minimal diff)
7. Add agent config fields for guardrail settings
8. Write unit tests

### Phase 2: Safe Default + Heuristic Deprecation

1. Implement `safe_default.py` (safe default policy)
2. Modify `normalize_agent_outcome()` to return heuristic result as metadata (shadow mode)
3. Add observability metrics comparing guardrail vs heuristic decisions
4. Deprecate `_looks_like_implicit_clarification` from classification path (keep for metrics only)

### Phase 3: Shadow Mode Validation

1. Deploy with guardrail in shadow mode (evaluate but don't act)
2. Collect metrics on trigger rate, nudge success rate, divergence from heuristic
3. Enable guardrail for real after metrics confirm improvement

---

## 14. Test Strategy

### Unit Tests

```
test_guardrail_accepts_when_no_fallback
test_guardrail_nudges_on_fallback_attempt_0
test_guardrail_overrides_to_complete_on_fallback_attempt_1
test_guardrail_skipped_when_disabled_in_config
test_nudge_message_includes_agent_output_preview
test_nudge_message_truncates_long_output
test_safe_default_produces_valid_complete_outcome
test_guardrail_handles_nudge_invocation_exception
test_guardrail_metadata_tracks_all_fields
test_guardrail_respects_max_retries_config
test_guardrail_does_not_fire_for_explicit_tool_signals
```

### Integration Tests (with mock agent)

```
test_executor_guardrail_converts_plain_text_to_complete_via_nudge
test_executor_guardrail_converts_plain_question_to_request_help_via_nudge
test_executor_guardrail_safe_default_when_nudge_fails
test_executor_guardrail_no_extra_invocation_on_structured_output
```
