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

function getWorkflowStageTitle(
  workflowStage: AgentThreadState["workflow_stage"],
  t: Translations,
) {
  if (workflowStage === "queued") {
    return t.workflowStatus.queued;
  }
  if (workflowStage === "acknowledged") {
    return t.workflowStatus.acknowledged;
  }
  if (workflowStage === "planning") {
    return t.workflowStatus.planning;
  }
  if (workflowStage === "routing") {
    return t.workflowStatus.routing;
  }
  if (workflowStage === "executing") {
    return t.workflowStatus.executing;
  }
  if (workflowStage === "summarizing") {
    return t.workflowStatus.summarizing;
  }

  return t.workflowStatus.processing;
}

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
  const waitingTaskDetail = clarificationTask
    ? pickFirstNonEmpty([
        clarificationTask.clarificationPrompt,
        clarificationTask.statusDetail,
        clarificationTask.latestUpdate,
        clarificationTask.description,
      ])
    : dependencyTask
      ? pickFirstNonEmpty([
          dependencyTask.blockedReason,
          dependencyTask.statusDetail,
          dependencyTask.latestUpdate,
          dependencyTask.description,
        ])
      : undefined;

  if (workflowStage) {
    title = getWorkflowStageTitle(workflowStage, t);
    detail = pickFirstNonEmpty([
      waitingTaskDetail,
      threadValues.workflow_stage_detail,
      latestCompletedTask?.latestUpdate,
      latestCompletedTask?.result,
      latestCompletedTask?.description,
      threadValues.planner_goal,
      threadValues.orchestration_reason,
    ]);
  } else if (clarificationTask) {
    title = t.workflowStatus.waitingClarification;
    detail = waitingTaskDetail;
  } else if (dependencyTask) {
    title = t.workflowStatus.waitingDependency;
    detail = waitingTaskDetail;
  } else if (activeTaskCount > 0) {
    title = t.workflowStatus.running(activeTaskCount);
    const activeTask = activeTasks[0];
    detail = pickFirstNonEmpty([
      activeTask?.latestUpdate,
      activeTask?.statusDetail,
      activeTask?.description,
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
    ...(workflowStage ? { workflowStage } : {}),
  };
}
