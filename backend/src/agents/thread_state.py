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

InterventionActionKind = Literal["button", "input", "select", "composite"]
InterventionResolutionBehavior = Literal["resume_current_task", "fail_current_task", "replan_from_resolution"]
InterventionStatusValue = Literal["pending", "resolved", "consumed", "rejected"]
InterventionRiskLevel = Literal["medium", "high", "critical"]


class InterventionActionEntry(TypedDict):
    """One action option inside an intervention request."""

    key: str
    label: str
    kind: InterventionActionKind
    resolution_behavior: InterventionResolutionBehavior
    payload_schema: NotRequired[dict[str, Any] | None]
    placeholder: NotRequired[str | None]


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
    display: NotRequired[InterventionDisplay | None]
    created_at: str


class InterventionResolution(TypedDict):
    """User-submitted resolution for an intervention request."""

    request_id: str
    fingerprint: str
    action_key: str
    payload: dict[str, Any]


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


def merge_task_pool(existing: list[TaskStatus] | None, new: list[TaskStatus] | None) -> list[TaskStatus]:
    """Reducer for task_pool with transition guard."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    if len(new) == 0:
        return []

    mapping: dict[str, dict] = {t["task_id"]: dict(t) for t in existing}
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
    route_count: NotRequired[int]
    execution_state: NotRequired[str | None]
    final_result: NotRequired[str | None]
