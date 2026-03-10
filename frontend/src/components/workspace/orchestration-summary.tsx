import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { GitBranchIcon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

export function OrchestrationSummary({
  className,
  thread,
}: {
  className?: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const { t } = useI18n();
  const mode = thread.values.resolved_orchestration_mode;
  const reason = thread.values.orchestration_reason?.trim();

  if (!mode) {
    return null;
  }

  const modeLabel =
    mode === "workflow"
      ? t.inputBox.workflowOrchestrationMode
      : t.inputBox.leaderOrchestrationMode;

  return (
    <div
      className={cn(
        "text-muted-foreground flex min-w-0 items-center gap-2 text-xs font-normal",
        className,
      )}
    >
      <span className="border-border/70 bg-background/70 inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5">
        <GitBranchIcon className="size-3" />
        <span>{modeLabel}</span>
      </span>
      {mode === "workflow" && reason ? (
        <span className="hidden truncate md:block">{reason}</span>
      ) : null}
    </div>
  );
}
