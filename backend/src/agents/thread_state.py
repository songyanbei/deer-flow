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
    status: Literal["PENDING", "RUNNING", "WAITING_DEPENDENCY", "DONE", "FAILED"]
    status_detail: NotRequired[str | None]
    clarification_prompt: NotRequired[str | None]
    updated_at: NotRequired[str | None]
    result: NotRequired[str | None]
    error: NotRequired[str | None]


RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]
ResolvedOrchestrationMode = Literal["leader", "workflow"]


VerifiedFact = dict[str, VerifiedFactEntry]


def _is_valid_status_transition(old_status: str, new_status: str) -> bool:
    """Validate task status transitions for blackboard safety."""
    if old_status == new_status:
        return True

    allowed_transitions = {
        "PENDING": {"RUNNING", "FAILED"},
        "RUNNING": {"WAITING_DEPENDENCY", "DONE", "FAILED"},
        "WAITING_DEPENDENCY": {"PENDING", "RUNNING", "FAILED"},
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
    run_id: NotRequired[str | None]
    planner_goal: NotRequired[str | None]
    task_pool: Annotated[list[TaskStatus], merge_task_pool]
    verified_facts: Annotated[VerifiedFact, merge_verified_facts]
    route_count: NotRequired[int]
    execution_state: NotRequired[str | None]
    final_result: NotRequired[str | None]
