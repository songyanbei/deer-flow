from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    base64: str
    mime_type: str


class HelpRequestPayload(TypedDict):
    problem: str
    required_capability: str
    reason: str
    expected_output: str
    resolution_strategy: NotRequired[str | None]
    clarification_question: NotRequired[str | None]
    clarification_options: NotRequired[list[str] | None]
    clarification_context: NotRequired[str | None]
    context_payload: NotRequired[dict[str, Any] | None]
    candidate_agents: NotRequired[list[str] | None]


class ClarificationQuestionEntry(TypedDict):
    key: str
    label: str
    kind: Literal["input"]
    required: NotRequired[bool | None]
    placeholder: NotRequired[str | None]
    help_text: NotRequired[str | None]


class ClarificationRequest(TypedDict):
    title: str
    description: NotRequired[str | None]
    questions: list[ClarificationQuestionEntry]


class VerifiedFactEntry(TypedDict):
    agent: str
    task: str
    summary: str
    payload: NotRequired[dict[str, Any] | None]
    fact_type: NotRequired[str | None]
    source_task_id: NotRequired[str | None]
    updated_at: NotRequired[str | None]


# ---------------------------------------------------------------------------
# Intervention Protocol Types (Phase 1)
# ---------------------------------------------------------------------------

InterventionActionKind = Literal[
    "button",
    "input",
    "select",
    "composite",
    "confirm",
    "single_select",
    "multi_select",
]
InterventionResolutionBehavior = Literal["resume_current_task", "fail_current_task", "replan_from_resolution"]
InterventionStatusValue = Literal["pending", "resolved", "consumed", "rejected"]
InterventionRiskLevel = Literal["medium", "high", "critical"]
InterventionKind = Literal["before_tool", "clarification", "selection", "confirmation"]
InterventionSourceSignal = Literal["intervention_required", "request_help", "ask_clarification"]


class InterventionOptionEntry(TypedDict):
    """One selectable option inside an intervention action."""

    label: str
    value: str
    description: NotRequired[str | None]


class InterventionQuestionEntry(TypedDict):
    """One question inside a multi-question intervention request."""

    key: str
    label: str
    kind: InterventionActionKind
    required: NotRequired[bool | None]
    placeholder: NotRequired[str | None]
    description: NotRequired[str | None]
    confirm_text: NotRequired[str | None]
    options: NotRequired[list[InterventionOptionEntry] | None]
    min_select: NotRequired[int | None]
    max_select: NotRequired[int | None]
    default_value: NotRequired[Any | None]


class InterventionActionEntry(TypedDict):
    """One action option inside an intervention request."""

    key: str
    label: str
    kind: InterventionActionKind
    resolution_behavior: InterventionResolutionBehavior
    payload_schema: NotRequired[dict[str, Any] | None]
    placeholder: NotRequired[str | None]
    description: NotRequired[str | None]
    confirm_text: NotRequired[str | None]
    required: NotRequired[bool | None]
    options: NotRequired[list[InterventionOptionEntry] | None]
    min_select: NotRequired[int | None]
    max_select: NotRequired[int | None]
    default_value: NotRequired[Any | None]


class InterventionActionSchema(TypedDict):
    """Schema describing available user actions for an intervention."""

    actions: list[InterventionActionEntry]


class InterventionDisplayItem(TypedDict):
    """A single label-value pair in a display section."""

    label: str
    value: str


class InterventionDisplaySection(TypedDict):
    """A group of display items with an optional section title."""

    title: NotRequired[str | None]
    items: list[InterventionDisplayItem]


class InterventionDisplayDebug(TypedDict):
    """Debug-only raw details, collapsed by default in UI."""

    source_agent: NotRequired[str | None]
    tool_name: NotRequired[str | None]
    raw_args: NotRequired[dict[str, Any] | None]


class InterventionDisplay(TypedDict):
    """User-facing display projection for an intervention card."""

    title: str
    summary: NotRequired[str | None]
    sections: NotRequired[list[InterventionDisplaySection] | None]
    risk_tip: NotRequired[str | None]
    primary_action_label: NotRequired[str | None]
    secondary_action_label: NotRequired[str | None]
    respond_action_label: NotRequired[str | None]
    respond_placeholder: NotRequired[str | None]
    debug: NotRequired[InterventionDisplayDebug | None]


class InterventionRequest(TypedDict):
    """Structured request emitted when a workflow step requires user intervention."""

    request_id: str
    fingerprint: str
    interrupt_kind: NotRequired[InterventionKind | None]
    semantic_key: NotRequired[str | None]
    source_signal: NotRequired[InterventionSourceSignal | None]
    intervention_type: str
    title: str
    reason: str
    description: NotRequired[str | None]
    source_agent: str
    source_task_id: str
    tool_name: NotRequired[str | None]
    risk_level: NotRequired[InterventionRiskLevel | None]
    category: NotRequired[str | None]
    context: NotRequired[dict[str, Any] | None]
    action_summary: NotRequired[str | None]
    action_schema: InterventionActionSchema
    questions: NotRequired[list[InterventionQuestionEntry] | None]
    display: NotRequired[InterventionDisplay | None]
    created_at: str


class InterventionResolution(TypedDict):
    """User-submitted resolution for an intervention request."""

    request_id: str
    fingerprint: str
    action_key: str
    payload: dict[str, Any]
    resolution_behavior: InterventionResolutionBehavior


class CachedInterventionResolution(TypedDict):
    """Reusable intervention decision cached at the thread level."""

    action_key: str
    payload: dict[str, Any]
    resolution_behavior: str
    resolved_at: str
    intervention_type: str
    source_agent: str
    semantic_key: NotRequired[str | None]
    max_reuse: int
    reuse_count: int


# ---------------------------------------------------------------------------
# Continuation State Types (Phase 2)
# ---------------------------------------------------------------------------

ContinuationMode = Literal[
    "resume_tool_call",
    "continue_after_dependency",
    "continue_after_intervention",
    "continue_after_clarification",
    "replan",
]


class PendingToolCall(TypedDict):
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: NotRequired[str | None]
    idempotency_key: NotRequired[str | None]
    source_agent: NotRequired[str | None]
    source_task_id: NotRequired[str | None]
    snapshot_hash: NotRequired[str | None]
    interrupt_fingerprint: NotRequired[str | None]


class PendingInterrupt(TypedDict):
    """Describes the last unresolved blocking condition for a task."""

    interrupt_type: Literal["dependency", "clarification", "intervention"]
    interrupt_kind: NotRequired[InterventionKind | None]
    request_id: NotRequired[str | None]
    fingerprint: NotRequired[str | None]
    semantic_key: NotRequired[str | None]
    source_signal: NotRequired[InterventionSourceSignal | None]
    prompt: NotRequired[str | None]
    options: NotRequired[list[str] | None]
    source: NotRequired[str | None]
    source_agent: NotRequired[str | None]
    created_at: NotRequired[str | None]


class TaskStatus(TypedDict):
    """Status of a single sub-task in the shared task pool."""

    task_id: str
    description: str
    run_id: NotRequired[str | None]
    parent_task_id: NotRequired[str | None]
    depends_on_task_ids: NotRequired[list[str] | None]
    assigned_agent: NotRequired[str | None]
    requested_by_agent: NotRequired[str | None]
    request_help: NotRequired[HelpRequestPayload | None]
    resolved_inputs: NotRequired[dict[str, Any] | None]
    blocked_reason: NotRequired[str | None]
    resume_count: NotRequired[int | None]
    help_depth: NotRequired[int | None]
    helper_retry_count: NotRequired[int | None]
    helper_context: NotRequired[str | None]
    status: Literal["PENDING", "RUNNING", "WAITING_DEPENDENCY", "WAITING_INTERVENTION", "DONE", "FAILED"]
    status_detail: NotRequired[str | None]
    clarification_prompt: NotRequired[str | None]
    clarification_request: NotRequired[ClarificationRequest | None]
    updated_at: NotRequired[str | None]
    result: NotRequired[str | None]
    error: NotRequired[str | None]
    # Intervention fields
    intervention_request: NotRequired[InterventionRequest | None]
    intervention_status: NotRequired[InterventionStatusValue | None]
    intervention_fingerprint: NotRequired[str | None]
    intervention_resolution: NotRequired[InterventionResolution | None]
    # Agent conversation history for resume continuity (serialized via messages_to_dict)
    agent_messages: NotRequired[list[dict[str, Any]] | None]
    # Original tool call intercepted by intervention middleware (for fast-path resume)
    intercepted_tool_call: NotRequired[dict[str, Any] | None]
    # Continuation state (Phase 2) — explicit resume semantics
    continuation_mode: NotRequired[ContinuationMode | None]
    pending_interrupt: NotRequired[PendingInterrupt | None]
    pending_tool_call: NotRequired[PendingToolCall | None]
    agent_history_cutoff: NotRequired[int | None]
    # Scheduling fields (Phase 2 Stage 1)
    priority: NotRequired[int | None]
    # Verification fields (Phase 4)
    verification_status: NotRequired[str | None]
    verification_report: NotRequired[dict[str, Any] | None]


RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]
ResolvedOrchestrationMode = Literal["leader", "workflow"]
WorkflowStage = Literal[
    "queued",
    "acknowledged",
    "planning",
    "routing",
    "executing",
    "summarizing",
]


VerifiedFact = dict[str, VerifiedFactEntry]
InterventionCache = dict[str, CachedInterventionResolution]


def _is_valid_status_transition(old_status: str, new_status: str) -> bool:
    """Validate task status transitions for blackboard safety."""
    if old_status == new_status:
        return True

    allowed_transitions = {
        "PENDING": {"RUNNING", "FAILED"},
        "RUNNING": {"WAITING_DEPENDENCY", "WAITING_INTERVENTION", "DONE", "FAILED"},
        "WAITING_DEPENDENCY": {"PENDING", "RUNNING", "FAILED"},
        "WAITING_INTERVENTION": {"RUNNING", "FAILED"},
        "DONE": set(),
        "FAILED": set(),
    }
    return new_status in allowed_transitions.get(old_status, set())


def _normalize_agent_name(name: str | None) -> str:
    """Lowercase + strip for agent name comparison."""
    return (name or "").strip().lower()


def merge_task_pool(existing: list[TaskStatus] | None, new: list[TaskStatus] | None) -> list[TaskStatus]:
    """Reducer for task_pool with transition guard and FAILED-task supersession.

    When the planner re-decomposes after a task failure, it creates new tasks
    (new task_id) targeting the same agent within the same run.  Without
    supersession the old FAILED entry lingers forever, leaving the pool in a
    non-convergent state (FAILED + DONE for the "same" logical work).

    Supersession rules (a new task evicts an existing FAILED task when):
      1. Both share the same ``run_id``.
      2. Agent match: both have ``assigned_agent`` and they match
         (case-insensitive), **or** either side lacks ``assigned_agent``.
      3. The new task is in a non-terminal state (``PENDING`` or ``RUNNING``).
    """
    if existing is None:
        return new or []
    if new is None:
        return existing
    if len(new) == 0:
        return []

    mapping: dict[str, dict] = {t["task_id"]: dict(t) for t in existing}

    # -- Supersession: collect incoming replacement candidates ---------------
    # Key = (run_id_lower, agent_name_lower | "").  A new PENDING/RUNNING task
    # may supersede an existing FAILED task in the same run for the same agent.
    _REPLACEMENT_STATUSES = {"PENDING", "RUNNING"}
    replacements: list[dict] = [
        t for t in new
        if t.get("status") in _REPLACEMENT_STATUSES and t.get("run_id")
    ]

    if replacements:
        to_remove: list[str] = []
        for tid, t in mapping.items():
            if t.get("status") != "FAILED" or not t.get("run_id"):
                continue
            old_run = t["run_id"]
            old_agent = _normalize_agent_name(t.get("assigned_agent"))

            for repl in replacements:
                if repl["task_id"] == tid:
                    continue
                if repl["run_id"] != old_run:
                    continue
                new_agent = _normalize_agent_name(repl.get("assigned_agent"))
                # Match when agents agree, or when either side is unknown.
                if old_agent and new_agent and old_agent != new_agent:
                    continue
                to_remove.append(tid)
                break

        for tid in to_remove:
            del mapping[tid]

    # -- Normal merge with transition guard ---------------------------------
    for task in new:
        tid = task["task_id"]
        if tid in mapping:
            updates = dict(task)
            old_status = mapping[tid].get("status")
            new_status = updates.get("status")
            if old_status and new_status and not _is_valid_status_transition(str(old_status), str(new_status)):
                updates.pop("status", None)
            mapping[tid].update(updates)
        else:
            mapping[tid] = dict(task)
    return list(mapping.values())


def merge_verified_facts(existing: VerifiedFact | None, new: VerifiedFact | None) -> VerifiedFact:
    """Reducer for verified_facts as a keyed blackboard."""
    if existing is None:
        return new or {}
    if new is None:
        return existing
    if len(new) == 0:
        return {}
    return {**existing, **new}


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries."""
    if existing is None:
        return new or {}
    if new is None:
        return existing
    if len(new) == 0:
        return {}
    return {**existing, **new}


def merge_intervention_cache(
    existing: InterventionCache | None,
    new: InterventionCache | None,
) -> InterventionCache:
    """Reducer for intervention cache keyed by semantic fingerprint."""
    if existing is None:
        return new or {}
    if new is None:
        return existing
    return {**existing, **new}


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: NotRequired[list | None]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]

    original_input: NotRequired[str | None]
    requested_orchestration_mode: NotRequired[RequestedOrchestrationMode | None]
    resolved_orchestration_mode: NotRequired[ResolvedOrchestrationMode | None]
    orchestration_reason: NotRequired[str | None]
    workflow_stage: NotRequired[WorkflowStage | None]
    workflow_stage_detail: NotRequired[str | None]
    workflow_stage_updated_at: NotRequired[str | None]
    run_id: NotRequired[str | None]
    planner_goal: NotRequired[str | None]
    task_pool: Annotated[list[TaskStatus], merge_task_pool]
    verified_facts: Annotated[VerifiedFact, merge_verified_facts]
    intervention_cache: Annotated[InterventionCache, merge_intervention_cache]
    route_count: NotRequired[int]
    validate_retries: NotRequired[int]
    execution_state: NotRequired[str | None]
    final_result: NotRequired[str | None]
    # Verification fields (Phase 4)
    verification_feedback: NotRequired[dict[str, Any] | None]
    verification_retry_count: NotRequired[int]
    workflow_verification_status: NotRequired[str | None]
    workflow_verification_report: NotRequired[dict[str, Any] | None]
