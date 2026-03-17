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
  // Terminal statuses: icon + label are sufficient, no detail needed
  if (task.status === "completed" || task.status === "failed") {
    return undefined;
  }
  // User-visible prompts shown as-is
  if (task.clarificationPrompt) return task.clarificationPrompt;
  if (task.blockedReason) return task.blockedReason;
  // Localize structured @key values; filter raw legacy English
  const localized =
    localizeStatusDetail(task.latestUpdate, t) ??
    localizeStatusDetail(task.statusDetail, t);
  if (localized) return localized;
  // Don't leak unresolved @keys to UI
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
  if (task.status === "in_progress") {
    return <Loader2Icon className="size-3.5 animate-spin text-primary" />;
  }
  return <CircleDashedIcon className="size-3.5 text-muted-foreground/80" />;
}

function isActiveWorkflowTask(task: TaskViewModel | undefined) {
  return (
    task?.status === "waiting_dependency" ||
    task?.status === "in_progress" ||
    task?.status === "waiting_clarification"
  );
}

function WorkflowFooterTaskRow({
  task,
}: {
  task: TaskViewModel;
}) {
  const { t } = useI18n();
  const detail = getTaskDetail(task, t);
  const isActive = isActiveWorkflowTask(task);

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-xl border border-transparent px-3 py-2.5 transition-colors",
        isActive
          ? "bg-background border-border/70"
          : "bg-background/65",
      )}
    >
      <div className="mt-0.5 shrink-0">{getTaskIcon(task)}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <div className="truncate text-sm font-medium">{task.description}</div>
          <div
            className={cn(
              "shrink-0 text-[11px] font-medium",
              task.status === "failed" ? "text-red-500" : "text-muted-foreground",
            )}
          >
            {getTaskStatusLabel(task, t)}
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
  const progress = getWorkflowProgressSummary({
    isLoading: thread.isLoading,
    threadValues: thread.values,
    tasks: workflowTasks,
    t,
  });
  const preferStageTitle =
    progress?.workflowStage != null && progress.workflowStage !== "executing";
  const primaryTask = useMemo(
    () => pickPrimaryWorkflowTask(workflowTasks),
    [workflowTasks],
  );
  const compactTitle = preferStageTitle
    ? getCompactTaskTitle(undefined, progress?.title ?? progress?.detail)
    : getCompactTaskTitle(primaryTask, progress?.title ?? progress?.detail);
  const completedCount = workflowTasks.filter(
    (task) => task.status === "completed",
  ).length;
  const totalCount = progress?.totalTaskCount ?? workflowTasks.length;
  const summaryLabel =
    totalCount > 0
      ? t.workflowStatus.completedSummary(completedCount, totalCount)
      : t.workflowStatus.initializing;
  const summaryKey =
    totalCount > 0 ? `${completedCount}-${totalCount}` : summaryLabel;
  const headerTitle =
    compactTitle ?? progress?.title ?? t.workflowStatus.processing;
  const titleKey = primaryTask?.id ?? headerTitle;
  const showShimmer =
    isActiveWorkflowTask(primaryTask) || Boolean(progress && !primaryTask);

  if (workflowTasks.length === 0 && !progress) {
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
          <FlipDisplay
            uniqueKey={summaryKey}
            className="h-4 leading-4"
          >
            <span className="block whitespace-nowrap">{summaryLabel}</span>
          </FlipDisplay>
        </div>
        <div className="min-w-0 flex-1 text-sm font-medium">
          <FlipDisplay
            uniqueKey={titleKey}
            className="h-5 leading-5"
          >
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
            <div className="border-border/60 bg-accent flex max-h-[40vh] flex-col gap-2 border-t px-2 pb-2 pt-2">
              {progress && (
                <div className="text-muted-foreground px-2 text-xs leading-4">
                  <span className="font-medium text-foreground">{progress.title}</span>
                  {progress.detail ? ` - ${progress.detail}` : ""}
                </div>
              )}
              <div className="flex max-h-[28vh] flex-col gap-1 overflow-y-auto">
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
                    <WorkflowFooterTaskRow task={task} />
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
