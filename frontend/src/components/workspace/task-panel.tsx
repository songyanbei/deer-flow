import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, GitBranchIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useSubtaskContext } from "@/core/tasks/context";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { SubtaskCard } from "./messages/subtask-card";

function shouldShowTask(task: TaskViewModel | undefined, runId?: string | null) {
  if (task?.source !== "multi_agent") {
    return false;
  }
  if (runId) {
    return task.runId === runId;
  }
  return true;
}

export function TaskPanel({
  className,
  thread,
}: {
  className?: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const { orderedTaskIds, tasksById } = useSubtaskContext();
  const runId = thread.values.run_id ?? null;

  const taskIds = useMemo(
    () =>
      orderedTaskIds.filter((taskId) => shouldShowTask(tasksById[taskId], runId)),
    [orderedTaskIds, runId, tasksById],
  );

  if (taskIds.length === 0) {
    return null;
  }

  return (
    <div
      className={cn(
        "bg-background/95 flex w-full origin-bottom translate-y-4 flex-col overflow-hidden rounded-t-xl border border-b-0 backdrop-blur-sm transition-all duration-200 ease-out",
        className,
      )}
    >
      <button
        type="button"
        className="bg-accent flex min-h-8 w-full items-center justify-between px-4 text-sm"
        onClick={() => setCollapsed((value) => !value)}
      >
        <div className="text-muted-foreground flex items-center gap-2">
          <GitBranchIcon className="size-4" />
          <span>{t.inputBox.workflowOrchestrationMode}</span>
          <span className="text-xs">{t.subtasks.executing(taskIds.length)}</span>
        </div>
        <ChevronUpIcon
          className={cn(
            "text-muted-foreground size-4 transition-transform duration-300 ease-out",
            collapsed ? "" : "rotate-180",
          )}
        />
      </button>
      <div
        className={cn(
          "bg-accent flex flex-col gap-2 px-2 transition-all duration-300 ease-out",
          collapsed ? "max-h-0 overflow-hidden pb-0" : "max-h-[60vh] overflow-y-auto pb-4",
        )}
      >
        <div className="flex flex-col gap-2 pt-2">
          {taskIds.map((taskId) => (
            <SubtaskCard
              key={taskId}
              taskId={taskId}
              isLoading={thread.isLoading}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
