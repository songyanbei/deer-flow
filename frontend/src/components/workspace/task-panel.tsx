import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, GitBranchIcon, Loader2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useSubtaskContext } from "@/core/tasks/context";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { Shimmer } from "../ai-elements/shimmer";

import { SubtaskCard } from "./messages/subtask-card";
import {
  filterWorkflowTasks,
  getWorkflowProgressSummary,
} from "./workflow-progress";

function pickPrimaryWorkflowTask(tasks: TaskViewModel[]) {
  return (
    tasks.find((task) => task.status === "waiting_intervention") ??
    tasks.find((task) => task.status === "waiting_clarification") ??
    tasks.find((task) => task.status === "waiting_dependency") ??
    tasks.find((task) => task.status === "in_progress") ??
    tasks.find((task) => task.status === "pending") ??
    tasks.find((task) => task.status === "failed") ??
    tasks[tasks.length - 1]
  );
}

function getCompactTaskTitle(task: TaskViewModel | undefined, fallback?: string) {
  const rawTitle = task?.description ?? fallback ?? "";
  return rawTitle
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean) ?? "";
}

function isActiveWorkflowTask(task: TaskViewModel | undefined) {
  return (
    task?.status === "waiting_dependency" ||
    task?.status === "waiting_intervention" ||
    task?.status === "in_progress" || task?.status === "waiting_clarification"
  );
}

export function TaskPanel({
  className,
  thread,
}: {
  className?: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const { orderedTaskIds, tasksById } = useSubtaskContext();
  const runId = thread.values.run_id ?? null;

  const workflowTasks = useMemo(
    () => filterWorkflowTasks(tasksById, orderedTaskIds, runId),
    [orderedTaskIds, runId, tasksById],
  );
  const taskIds = workflowTasks.map((task) => task.id);
  const progress = getWorkflowProgressSummary({
    isLoading: thread.isLoading,
    threadValues: thread.values,
    tasks: workflowTasks,
    t,
  });
  const primaryTask = useMemo(
    () => pickPrimaryWorkflowTask(workflowTasks),
    [workflowTasks],
  );
  const hiddenTaskCount = Math.max(taskIds.length - (expanded ? taskIds.length : 1), 0);
  const compactTitle = getCompactTaskTitle(primaryTask, progress?.title);
  const shouldShimmerPrimaryTask = isActiveWorkflowTask(primaryTask);

  if (taskIds.length === 0 && !progress) {
    return null;
  }

  return (
    <div
      className={cn(
        "bg-background/94 flex w-full origin-bottom flex-col overflow-visible rounded-t-xl border border-b-0 shadow-sm backdrop-blur-sm transition-all duration-200 ease-out",
        className,
      )}
    >
      <button
        type="button"
        className="bg-accent flex min-h-6 w-full items-center justify-between px-2.5 py-1 text-xs"
        onClick={() => setExpanded((value) => !value)}
      >
        <div className="text-muted-foreground flex min-w-0 items-center gap-1.5">
          <GitBranchIcon className="size-3.5" />
          <span className="shrink-0 font-medium">
            {t.inputBox.workflowOrchestrationMode}
          </span>
          {!expanded && (
            <span className="text-muted-foreground/80 truncate">
              {t.subtasks.executing(taskIds.length)}
            </span>
          )}
          {!expanded && hiddenTaskCount > 0 && (
            <span className="rounded-full border px-1.5 py-0 text-[10px] leading-4">
              +{hiddenTaskCount}
            </span>
          )}
        </div>
        <ChevronUpIcon
          className={cn(
            "text-muted-foreground size-3.5 transition-transform duration-300 ease-out",
            expanded ? "rotate-180" : "",
          )}
        />
      </button>
      {expanded ? (
        <div className="bg-accent flex max-h-[44vh] flex-col gap-1.5 overflow-visible px-2 pb-2 pt-1.5 transition-all duration-300 ease-out">
          {progress && (
            <div className="bg-background/80 border-border/60 flex items-start gap-2 rounded-md border px-2.5 py-1.5">
              <Loader2Icon className="text-muted-foreground mt-0.5 size-3.5 shrink-0 animate-spin" />
              <div className="min-w-0">
                <div className="text-xs font-medium">
                  {shouldShimmerPrimaryTask && compactTitle ? (
                    <Shimmer as="span" className="truncate" duration={3} spread={3}>
                      {compactTitle}
                    </Shimmer>
                  ) : (
                    progress.title
                  )}
                </div>
                {progress.detail && (
                  <div className="text-muted-foreground mt-0.5 truncate text-[11px]">
                    {progress.detail}
                  </div>
                )}
              </div>
            </div>
          )}
          {taskIds.length > 0 && (
            <div className="h-52 max-h-[34vh] overflow-y-auto px-0.5 py-1">
              <div className="flex flex-col gap-2 pr-1">
                {taskIds.map((taskId) => (
                  <SubtaskCard
                    key={taskId}
                    taskId={taskId}
                    isLoading={thread.isLoading}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-accent px-2 pb-1.5 pt-1 transition-all duration-300 ease-out">
          <div className="bg-background/80 border-border/60 flex min-h-8 items-center gap-2 rounded-md border px-2.5 py-1">
            {thread.isLoading ? (
              <Loader2Icon className="text-muted-foreground size-3.5 shrink-0 animate-spin" />
            ) : (
              <GitBranchIcon className="text-muted-foreground size-3.5 shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11px] font-medium leading-4">
                {shouldShimmerPrimaryTask && compactTitle ? (
                  <Shimmer as="span" className="truncate" duration={3} spread={3}>
                    {compactTitle}
                  </Shimmer>
                ) : (
                  compactTitle
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
