import type { Translations } from "@/core/i18n/locales/types";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";

type WorkflowProgressInput = {
  isLoading: boolean;
  threadValues: Pick<
    AgentThreadState,
    | "resolved_orchestration_mode"
    | "orchestration_reason"
    | "workflow_stage"
    | "workflow_stage_detail"
    | "planner_goal"
    | "execution_state"
    | "run_id"
  >;
  tasks: TaskViewModel[];
  t: Translations;
};

export type WorkflowProgressSummary = {
  title: string;
  detail?: string;
  activeTaskCount: number;
  totalTaskCount: number;
  isWaitingClarification: boolean;
  workflowStage?: AgentThreadState["workflow_stage"];
};

export function filterWorkflowTasks(
  tasksById: Record<string, TaskViewModel>,
  orderedTaskIds: string[],
  runId?: string | null,
) {
  return orderedTaskIds
    .map((taskId) => tasksById[taskId])
    .filter((task): task is TaskViewModel => {
      if (task?.source !== "multi_agent") {
        return false;
      }
      if (runId) {
        return task.runId === runId;
      }
      return true;
    });
}

function isActiveTask(task: TaskViewModel) {
  return (
    task.status === "pending" ||
    task.status === "waiting_dependency" ||
    task.status === "in_progress" ||
    task.status === "waiting_clarification"
  );
}

function pickFirstNonEmpty(values: Array<string | null | undefined>) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

export function getWorkflowProgressSummary({
  isLoading,
  threadValues,
  tasks,
  t,
}: WorkflowProgressInput): WorkflowProgressSummary | null {
  const executionState = threadValues.execution_state?.trim() ?? "";
  const workflowStage = threadValues.workflow_stage ?? null;
  const totalTaskCount = tasks.length;
  const activeTasks = tasks.filter(isActiveTask);
  const activeTaskCount = activeTasks.length;
  const clarificationTask = tasks.find(
    (task) => task.status === "waiting_clarification",
  );
  const dependencyTask = tasks.find(
    (task) => task.status === "waiting_dependency",
  );
  const latestCompletedTask = [...tasks]
    .reverse()
    .find((task) => task.status === "completed");
  const shouldShow =
    workflowStage !== null ||
    (isLoading &&
      (threadValues.resolved_orchestration_mode === "workflow" ||
        totalTaskCount > 0 ||
        executionState === "PLANNING_RESET" ||
        executionState === "PLANNING_DONE" ||
        executionState === "RESUMING" ||
        executionState === "EXECUTING_DONE"));

  if (!shouldShow) {
    return null;
  }

  let title = t.workflowStatus.processing;
  let detail: string | undefined;

  if (clarificationTask) {
    title = t.workflowStatus.waitingClarification;
    detail = pickFirstNonEmpty([
      clarificationTask.clarificationPrompt,
      clarificationTask.statusDetail,
      clarificationTask.latestUpdate,
      clarificationTask.description,
    ]);
  } else if (dependencyTask) {
    title = t.workflowStatus.waitingDependency;
    detail = pickFirstNonEmpty([
      dependencyTask.blockedReason,
      dependencyTask.statusDetail,
      dependencyTask.latestUpdate,
      dependencyTask.description,
    ]);
  } else if (
    workflowStage === "queued" ||
    workflowStage === "acknowledged" ||
    workflowStage === "planning" ||
    workflowStage === "routing" ||
    workflowStage === "summarizing"
  ) {
    if (workflowStage === "queued") {
      title = t.workflowStatus.queued;
    } else if (workflowStage === "acknowledged") {
      title = t.workflowStatus.acknowledged;
    } else if (workflowStage === "planning") {
      title = t.workflowStatus.planning;
    } else if (workflowStage === "routing") {
      title = t.workflowStatus.routing;
    } else {
      title = t.workflowStatus.summarizing;
    }
    detail = pickFirstNonEmpty([
      threadValues.workflow_stage_detail,
      latestCompletedTask?.latestUpdate,
      latestCompletedTask?.result,
      latestCompletedTask?.description,
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else if (activeTaskCount > 0) {
    title = t.workflowStatus.running(activeTaskCount);
    const activeTask = activeTasks[0];
    detail = pickFirstNonEmpty([
      activeTask?.latestUpdate,
      activeTask?.statusDetail,
      activeTask?.description,
    ]);
  } else if (workflowStage === "executing") {
    title = t.workflowStatus.executing;
    detail = pickFirstNonEmpty([
      threadValues.workflow_stage_detail,
      latestCompletedTask?.latestUpdate,
      latestCompletedTask?.description,
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else if (
    executionState === "PLANNING_RESET" ||
    executionState === "PLANNING_DONE"
  ) {
    title = t.workflowStatus.planning;
    detail = pickFirstNonEmpty([
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else if (executionState === "RESUMING") {
    title = t.workflowStatus.resuming;
    detail = pickFirstNonEmpty([
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else if (executionState === "EXECUTING_DONE") {
    title = t.workflowStatus.summarizing;
    detail = pickFirstNonEmpty([
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else {
    detail = pickFirstNonEmpty([
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  }

  if (detail === title) {
    detail = undefined;
  }

  return {
    title,
    detail,
    activeTaskCount,
    totalTaskCount,
    isWaitingClarification: Boolean(clarificationTask),
    workflowStage,
  };
}
