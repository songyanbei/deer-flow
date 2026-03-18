"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  CheckCircle2Icon,
  ChevronUpIcon,
  CircleDashedIcon,
  GitBranchIcon,
  Loader2Icon,
  PauseCircleIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useMemo, useState } from "react";

import { Shimmer } from "@/components/ai-elements/shimmer";
import { useI18n } from "@/core/i18n/hooks";
import { useSubtaskContext } from "@/core/tasks/context";
import { localizeStatusDetail } from "@/core/tasks/status-detail";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { FlipDisplay } from "./flip-display";
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
  return (
    rawTitle
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? ""
  );
}

function getTaskDetail(
  task: TaskViewModel,
  t: ReturnType<typeof useI18n>["t"],
): string | undefined {
  if (task.status === "completed" || task.status === "failed") {
    return undefined;
  }
  if (task.clarificationRequest?.description) return task.clarificationRequest.description;
  if (task.clarificationRequest?.title) return task.clarificationRequest.title;
  if (task.clarificationPrompt) return task.clarificationPrompt;
  if (task.blockedReason) return task.blockedReason;
  if (task.interventionRequest?.reason) return task.interventionRequest.reason;

  const localized =
    localizeStatusDetail(task.latestUpdate, t) ??
    localizeStatusDetail(task.statusDetail, t);
  if (localized) return localized;

  const raw = task.latestUpdate ?? task.statusDetail;
  if (raw?.startsWith("@")) return undefined;
  return raw;
}

function getTaskStatusLabel(
  task: TaskViewModel,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (task.status === "pending") {
    return t.subtasks.pending;
  }
  if (task.status === "waiting_dependency") {
    return t.subtasks.waiting_dependency;
  }
  if (task.status === "waiting_clarification") {
    return t.subtasks.waiting_clarification;
  }
  if (task.status === "waiting_intervention") {
    return t.subtasks.waiting_intervention;
  }
  if (task.status === "in_progress") {
    return t.subtasks.in_progress;
  }
  if (task.status === "completed") {
    return t.subtasks.completed;
  }
  return t.subtasks.failed;
}

function getTaskIcon(task: TaskViewModel) {
  if (task.status === "completed") {
    return <CheckCircle2Icon className="size-3.5 text-emerald-600" />;
  }
  if (task.status === "failed") {
    return <XCircleIcon className="size-3.5 text-red-500" />;
  }
  if (task.status === "waiting_dependency") {
    return <PauseCircleIcon className="size-3.5 text-amber-500" />;
  }
  if (task.status === "waiting_clarification") {
    return <SparklesIcon className="size-3.5 text-sky-500" />;
  }
  if (task.status === "waiting_intervention") {
    return <PauseCircleIcon className="size-3.5 text-orange-500" />;
  }
  if (task.status === "in_progress") {
    return <Loader2Icon className="size-3.5 animate-spin text-primary" />;
  }
  return <CircleDashedIcon className="size-3.5 text-muted-foreground/80" />;
}

function getStoppedTaskIcon(task: TaskViewModel) {
  if (task.status === "completed") {
    return <CheckCircle2Icon className="size-3.5 text-emerald-600" />;
  }
  if (task.status === "failed") {
    return <XCircleIcon className="size-3.5 text-red-500" />;
  }
  return <PauseCircleIcon className="size-3.5 text-muted-foreground" />;
}

function isActiveWorkflowTask(task: TaskViewModel | undefined) {
  return (
    task?.status === "waiting_dependency" ||
    task?.status === "waiting_intervention" ||
    task?.status === "in_progress" ||
    task?.status === "waiting_clarification"
  );
}

function WorkflowFooterTaskRow({
  task,
  stopped = false,
}: {
  task: TaskViewModel;
  stopped?: boolean;
}) {
  const { t } = useI18n();
  const detail = getTaskDetail(task, t);
  const isActive = !stopped && isActiveWorkflowTask(task);
  const statusLabel = stopped
    ? t.workflowStatus.stopped
    : getTaskStatusLabel(task, t);

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-xl border border-transparent px-3 py-2.5 transition-colors",
        isActive ? "bg-background border-border/70" : "bg-background/65",
      )}
    >
      <div className="mt-0.5 shrink-0">
        {stopped ? getStoppedTaskIcon(task) : getTaskIcon(task)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <div className="truncate text-sm font-medium">{task.description}</div>
          <div
            className={cn(
              "shrink-0 text-[11px] font-medium",
              !stopped && task.status === "failed"
                ? "text-red-500"
                : "text-muted-foreground",
            )}
          >
            {statusLabel}
          </div>
        </div>
        {detail && (
          <div className="text-muted-foreground mt-1 line-clamp-2 text-xs leading-4">
            {detail}
          </div>
        )}
      </div>
    </div>
  );
}

export function WorkflowFooterBar({
  className,
  thread,
  hidden = false,
  stopped = false,
}: {
  className?: string;
  thread: BaseStream<AgentThreadState>;
  hidden?: boolean;
  stopped?: boolean;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const { orderedTaskIds, tasksById } = useSubtaskContext();
  const runId = thread.values.run_id ?? null;

  const workflowTasks = useMemo(
    () => filterWorkflowTasks(tasksById, orderedTaskIds, runId),
    [orderedTaskIds, runId, tasksById],
  );
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
  const preferStageTitle =
    progress?.workflowStage != null && progress.workflowStage !== "executing";
  const compactTitle = preferStageTitle
    ? getCompactTaskTitle(undefined, progress?.title ?? progress?.detail)
    : getCompactTaskTitle(primaryTask, progress?.title ?? progress?.detail);
  const completedCount = workflowTasks.filter(
    (task) => task.status === "completed",
  ).length;
  const totalCount = progress?.totalTaskCount ?? workflowTasks.length;
  const summaryLabel = stopped
    ? t.workflowStatus.stopped
    : totalCount > 0
      ? t.workflowStatus.completedSummary(completedCount, totalCount)
      : t.workflowStatus.initializing;
  const summaryKey =
    totalCount > 0 && !stopped ? `${completedCount}-${totalCount}` : summaryLabel;
  const headerTitle = stopped
    ? t.workflowStatus.stoppedDescription
    : compactTitle ?? progress?.title ?? t.workflowStatus.processing;
  const titleKey = primaryTask?.id ?? headerTitle;
  const showShimmer =
    !stopped &&
    (isActiveWorkflowTask(primaryTask) || Boolean(progress && !primaryTask));
  if (hidden || (workflowTasks.length === 0 && !progress && !stopped)) {
    return null;
  }

  return (
    <div
      className={cn(
        "flex w-full origin-bottom flex-col overflow-hidden rounded-t-2xl border border-b-0 bg-background/95 shadow-sm backdrop-blur-sm transition-all duration-200 ease-out",
        className,
      )}
    >
      <button
        type="button"
        className="flex min-h-9 w-full items-center gap-3 px-4 py-2 text-left"
        onClick={() => setExpanded((value) => !value)}
      >
        <div className="text-muted-foreground shrink-0 text-xs font-medium whitespace-nowrap">
          <FlipDisplay uniqueKey={summaryKey} className="h-4 leading-4">
            <span className="block whitespace-nowrap">{summaryLabel}</span>
          </FlipDisplay>
        </div>
        <div className="min-w-0 flex-1 text-sm font-medium">
          <FlipDisplay uniqueKey={titleKey} className="h-5 leading-5">
            {showShimmer ? (
              <Shimmer
                as="span"
                className="block truncate"
                duration={3}
                spread={3}
              >
                {headerTitle}
              </Shimmer>
            ) : (
              <span className="block truncate">{headerTitle}</span>
            )}
          </FlipDisplay>
        </div>
        <div className="text-muted-foreground flex shrink-0 items-center gap-2">
          <GitBranchIcon className="size-3.5" />
          <ChevronUpIcon
            className={cn(
              "size-4 transition-transform duration-300 ease-out",
              expanded ? "rotate-180" : "",
            )}
          />
        </div>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="border-border/60 bg-accent flex max-h-[34vh] flex-col gap-2 border-t px-2 pb-2 pt-2">
              {(progress || stopped) && (
                <div className="text-muted-foreground px-2 text-xs leading-4">
                  <span className="font-medium text-foreground">
                    {stopped ? t.workflowStatus.stopped : progress?.title}
                  </span>
                  {!stopped && progress?.detail ? ` - ${progress.detail}` : ""}
                </div>
              )}
              <div className="flex max-h-[24vh] flex-col gap-1 overflow-y-auto pr-1">
                {workflowTasks.map((task, index) => (
                  <motion.div
                    key={task.id}
                    initial={{ y: 12, opacity: 0 }}
                    animate={{ y: 0, opacity: 1 }}
                    transition={{
                      duration: 0.3,
                      delay: index * 0.05,
                      ease: [0.32, 0.72, 0, 1],
                    }}
                  >
                    <WorkflowFooterTaskRow task={task} stopped={stopped} />
                  </motion.div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
