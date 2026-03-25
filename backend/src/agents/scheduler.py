"""Dependency-aware parallel scheduler for the multi-agent workflow.

Phase 2 Stage 1: provides task scheduling logic that identifies runnable tasks
based on dependency satisfaction and selects an execution batch within a fixed
concurrency window.
"""

import logging

from src.agents.thread_state import TaskStatus

logger = logging.getLogger(__name__)

# Fixed concurrency window — intentionally conservative for MVP.
DEFAULT_CONCURRENCY_WINDOW = 3

_TERMINAL_TASK_STATES = {"DONE", "FAILED"}
_BLOCKED_TASK_STATES = {"WAITING_DEPENDENCY", "WAITING_INTERVENTION"}
# System-special agents that must execute alone (not batched with others).
_SYSTEM_SPECIAL_AGENTS = {"SYSTEM_FINISH", "SYSTEM_FALLBACK"}


def _dependencies_satisfied(task: TaskStatus, task_index: dict[str, TaskStatus]) -> bool:
    """Check whether all dependencies of a task are in a terminal DONE state."""
    dep_ids = task.get("depends_on_task_ids") or []
    if not dep_ids:
        return True
    for dep_id in dep_ids:
        dep_task = task_index.get(dep_id)
        if dep_task is None:
            # Unknown dependency — treat as unsatisfied.
            logger.warning(
                "[Scheduler] Task '%s' depends on unknown task_id '%s'.",
                task["task_id"], dep_id,
            )
            return False
        if dep_task["status"] != "DONE":
            return False
    return True


def _has_failed_dependency(task: TaskStatus, task_index: dict[str, TaskStatus]) -> bool:
    """Check if any dependency has permanently failed."""
    dep_ids = task.get("depends_on_task_ids") or []
    for dep_id in dep_ids:
        dep_task = task_index.get(dep_id)
        if dep_task is not None and dep_task["status"] == "FAILED":
            return True
    return False


def get_runnable_tasks(task_pool: list[TaskStatus]) -> list[TaskStatus]:
    """Identify all PENDING tasks whose dependencies are satisfied.

    Returns tasks sorted by priority (lower number = higher priority),
    then by pool order for stability.
    """
    if not task_pool:
        return []

    task_index = {t["task_id"]: t for t in task_pool}
    runnable: list[TaskStatus] = []

    for task in task_pool:
        if task["status"] != "PENDING":
            continue
        if not _dependencies_satisfied(task, task_index):
            continue
        runnable.append(task)

    # Sort by priority (lower = higher priority); None treated as 0.
    runnable.sort(key=lambda t: t.get("priority") or 0)
    return runnable


def get_currently_executing_count(task_pool: list[TaskStatus]) -> int:
    """Count tasks that are currently being executed (RUNNING status)."""
    return sum(1 for t in task_pool if t["status"] == "RUNNING")


def select_execution_batch(
    task_pool: list[TaskStatus],
    *,
    max_concurrency: int = DEFAULT_CONCURRENCY_WINDOW,
) -> list[TaskStatus]:
    """Select a batch of tasks to execute concurrently.

    Respects:
    - dependency ordering (only PENDING tasks with satisfied deps)
    - the fixed concurrency window (minus already-RUNNING tasks)
    - priority ordering within runnable set
    """
    currently_running = get_currently_executing_count(task_pool)
    available_slots = max(0, max_concurrency - currently_running)

    if available_slots == 0:
        return []

    runnable = get_runnable_tasks(task_pool)

    # System-special agents (SYSTEM_FINISH, SYSTEM_FALLBACK) must execute alone.
    if runnable and (runnable[0].get("assigned_agent") or "") in _SYSTEM_SPECIAL_AGENTS:
        return runnable[:1]

    # Filter out system-special tasks from concurrent batches.
    regular = [t for t in runnable if (t.get("assigned_agent") or "") not in _SYSTEM_SPECIAL_AGENTS]
    batch = regular[:available_slots]

    if batch:
        logger.info(
            "[Scheduler] Selected batch of %d task(s) for execution (running=%d, window=%d): %s",
            len(batch),
            currently_running,
            max_concurrency,
            [t["task_id"] for t in batch],
        )

    return batch


def has_pending_work(task_pool: list[TaskStatus]) -> bool:
    """Check if there are any tasks that could still make progress.

    Returns True if there are PENDING, RUNNING, or blocked tasks that
    might eventually unblock.
    """
    for task in task_pool:
        if task["status"] in ("PENDING", "RUNNING"):
            return True
        if task["status"] in _BLOCKED_TASK_STATES:
            return True
    return False


def all_tasks_terminal(task_pool: list[TaskStatus]) -> bool:
    """Check if all tasks in the pool are in a terminal state."""
    if not task_pool:
        return True
    return all(t["status"] in _TERMINAL_TASK_STATES for t in task_pool)


def get_blocked_by_failed_dependency(task_pool: list[TaskStatus]) -> list[TaskStatus]:
    """Find PENDING tasks that are blocked because a dependency has FAILED."""
    task_index = {t["task_id"]: t for t in task_pool}
    blocked: list[TaskStatus] = []
    for task in task_pool:
        if task["status"] != "PENDING":
            continue
        dep_ids = task.get("depends_on_task_ids") or []
        if not dep_ids:
            continue
        if _has_failed_dependency(task, task_index):
            blocked.append(task)
    return blocked
