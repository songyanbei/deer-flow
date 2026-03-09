import type { TaskSource, TaskViewModel } from "./types";

export function getTasksForSource(
  tasksById: Record<string, TaskViewModel>,
  source: TaskSource,
): TaskViewModel[] {
  return Object.values(tasksById).filter((task) => task.source === source);
}

export function getMultiAgentTasksByRunId(
  tasksById: Record<string, TaskViewModel>,
  runId?: string | null,
): TaskViewModel[] {
  return Object.values(tasksById).filter(
    (task) => task.source === "multi_agent" && task.runId === (runId ?? undefined),
  );
}

export function getVisibleMultiAgentTasks(
  tasksById: Record<string, TaskViewModel>,
): TaskViewModel[] {
  return getTasksForSource(tasksById, "multi_agent").filter(
    (task) => task.status !== "completed" || Boolean(task.result),
  );
}

export function getActiveTasks(
  tasksById: Record<string, TaskViewModel>,
): TaskViewModel[] {
  return Object.values(tasksById).filter((task) =>
    ["pending", "in_progress", "waiting_clarification"].includes(task.status),
  );
}

export function getFailedTasks(
  tasksById: Record<string, TaskViewModel>,
): TaskViewModel[] {
  return Object.values(tasksById).filter((task) => task.status === "failed");
}
