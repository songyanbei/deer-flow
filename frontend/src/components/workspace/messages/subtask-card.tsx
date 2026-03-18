import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useState } from "react";
import { Streamdown } from "streamdown";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { useSubtask } from "@/core/tasks/context";
import { localizeStatusDetail } from "@/core/tasks/status-detail";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";
import { InterventionCard } from "./intervention-card";

const INTERNAL_ERROR_PATTERNS = [
  /domain agent returned no final answer/i,
  /no running task found/i,
  /mcp.*connection error/i,
  /request_help returned invalid/i,
  /executor.*no running/i,
  /timeout/i,
];

function friendlyError(
  error: string | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (!error) return t.subtasks.failed;
  if (INTERNAL_ERROR_PATTERNS.some((p) => p.test(error))) {
    return t.subtasks.errorGeneric;
  }
  return error;
}

function getStatusLabel(task: ReturnType<typeof useSubtask>, t: ReturnType<typeof useI18n>["t"]) {
  if (!task) {
    return "";
  }

  const localizedDetail = localizeStatusDetail(task.statusDetail, t);
  const localizedUpdate = localizeStatusDetail(task.latestUpdate, t);

  if (task.status === "pending") {
    return localizedDetail ?? t.subtasks.pending;
  }
  if (task.status === "waiting_dependency") {
    return (
      task.blockedReason ??
      localizedDetail ??
      localizedUpdate ??
      t.subtasks.waiting_dependency
    );
  }
  if (task.status === "waiting_clarification") {
    return (
      task.clarificationRequest?.description ??
      task.clarificationRequest?.title ??
      task.clarificationPrompt ??
      localizedDetail ??
      localizedUpdate ??
      t.subtasks.waiting_clarification
    );
  }
  if (task.status === "waiting_intervention") {
    return (
      task.interventionRequest?.reason ??
      localizedDetail ??
      localizedUpdate ??
      t.subtasks.waiting_intervention
    );
  }
  if (task.status === "in_progress") {
    return task.latestMessage && hasToolCalls(task.latestMessage)
      ? explainLastToolCall(task.latestMessage, t)
      : localizedUpdate ?? localizedDetail ?? t.subtasks.in_progress;
  }
  if (task.status === "completed") {
    return t.subtasks.completed;
  }
  return t.subtasks.failed;
}

function getCollapsedStatusLabel(
  task: ReturnType<typeof useSubtask>,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (!task) {
    return "";
  }

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

export function SubtaskCard({
  className,
  taskId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const task = useSubtask(taskId);
  if (!task) {
    return null;
  }
  let icon = <ClipboardListIcon className="size-3" />;
  const hasResolvedInputs =
    !!task.resolvedInputs && Object.keys(task.resolvedInputs).length > 0;
  const wasResumed =
    (task.resumeCount ?? 0) > 0 ||
    (task.status !== "waiting_dependency" && hasResolvedInputs);
  if (task.status === "completed") {
    icon = <CheckCircleIcon className="size-3" />;
  } else if (task.status === "failed") {
    icon = <XCircleIcon className="size-3 text-red-500" />;
  } else if (
    task.status === "waiting_dependency" ||
    task.status === "waiting_intervention" ||
    task.status === "in_progress" ||
    task.status === "waiting_clarification"
  ) {
    icon = <Loader2Icon className="size-3 animate-spin" />;
  }

  const progressLabel = getStatusLabel(task, t);
  const collapsedStatusLabel = getCollapsedStatusLabel(task, t);
  const isActive =
    task.status === "in_progress" ||
    task.status === "waiting_clarification" ||
    task.status === "waiting_intervention" ||
    task.status === "waiting_dependency";

  return (
    <ChainOfThought
      className={cn(
        "relative w-full gap-2 rounded-lg border py-0 transition-shadow",
        isActive &&
          "border-[#c8a8ff] shadow-[0_0_0_1px_rgba(200,168,255,0.24),0_14px_32px_rgba(160,124,254,0.12)]",
        className,
      )}
      open={!collapsed}
    >
      <div
        className={cn(
          "ambilight absolute inset-0 rounded-[inherit]",
          isActive ? "enabled" : "",
        )}
      ></div>
      <div className="bg-background flex w-full flex-col rounded-lg">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  isActive ? (
                    <Shimmer duration={3} spread={3}>
                      {task.description}
                    </Shimmer>
                  ) : (
                    task.description
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed" ? "text-red-500 opacity-67" : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[180px] min-h-4 truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? task.latestUpdate ?? task.status}
                    >
                      {collapsedStatusLabel}
                    </FlipDisplay>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <Streamdown
                  {...streamdownPluginsWithWordAnimation}
                  components={{ a: CitationLink }}
                >
                  {task.prompt}
                </Streamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {(task.status === "in_progress" ||
            task.status === "waiting_dependency" ||
            task.status === "waiting_intervention" ||
            task.status === "waiting_clarification" ||
            task.status === "pending") &&
            progressLabel && (
            <ChainOfThoughtStep
              label={progressLabel}
              icon={
                task.status === "pending" ? (
                  <ClipboardListIcon className="size-4" />
                ) : task.status === "waiting_dependency" ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <Loader2Icon className="size-4 animate-spin" />
                )
              }
            ></ChainOfThoughtStep>
            )}
          {task.status === "waiting_dependency" && task.requestHelp && (
            <ChainOfThoughtStep
              label={
                t.subtasks.needCapability?.(
                  task.requestHelp.requiredCapability,
                ) ?? `Need capability: ${task.requestHelp.requiredCapability}`
              }
            ></ChainOfThoughtStep>
          )}
          {task.status === "waiting_dependency" && task.requestedByAgent && (
            <ChainOfThoughtStep
              label={
                t.subtasks.requestedBy?.(task.requestedByAgent) ??
                `Requested by: ${task.requestedByAgent}`
              }
            ></ChainOfThoughtStep>
          )}
          {wasResumed && (
            <ChainOfThoughtStep
              label={t.subtasks.resumed ?? "Dependency resolved; resumed execution"}
            ></ChainOfThoughtStep>
          )}
          {hasResolvedInputs && task.resolvedInputs && (
            <ChainOfThoughtStep
              label={
                <div className="space-y-2">
                  <div className="text-muted-foreground text-sm">
                    {t.subtasks.resolvedInputs ?? "Resolved inputs"}
                  </div>
                  <MarkdownContent
                    content={`\`\`\`json\n${JSON.stringify(task.resolvedInputs, null, 2)}\n\`\`\``}
                    isLoading={false}
                    rehypePlugins={rehypePlugins}
                  />
                </div>
              }
            ></ChainOfThoughtStep>
          )}
          {task.status === "waiting_intervention" && (
            <ChainOfThoughtStep
              label={<InterventionCard task={task} />}
              icon={<AlertTriangleIcon className="size-4 text-orange-500" />}
            ></ChainOfThoughtStep>
          )}
          {task.status === "completed" && (
            <ChainOfThoughtStep
              label={t.subtasks.completed}
              icon={<CheckCircleIcon className="size-4" />}
            ></ChainOfThoughtStep>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={
                <div className="text-red-500">
                  {friendlyError(task.error, t)}
                </div>
              }
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
