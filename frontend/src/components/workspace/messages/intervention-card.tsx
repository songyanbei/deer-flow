"use client";

import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BotIcon,
  Loader2Icon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { type ResolveInterventionResponse } from "@/core/interventions/api";
import { useResolveIntervention } from "@/core/interventions/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useLocalSettings } from "@/core/settings";
import type { TaskViewModel } from "@/core/tasks/types";
import type { InterventionDisplay } from "@/core/threads";

import { useThread } from "./context";

function formatContextValue(value: unknown) {
  if (typeof value === "string") {
    return value;
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null ||
    value === undefined
  ) {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

function formatContextLabel(label: string) {
  return label
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function getRiskTone(riskLevel?: string) {
  if (riskLevel === "critical") {
    return {
      badge: "border-rose-300/80 bg-rose-500/10 text-rose-700",
      accent: "bg-rose-500",
      icon: "text-rose-600",
    };
  }
  if (riskLevel === "high") {
    return {
      badge: "border-amber-300/80 bg-amber-500/10 text-amber-700",
      accent: "bg-amber-500",
      icon: "text-amber-600",
    };
  }
  return {
    badge: "border-sky-300/80 bg-sky-500/10 text-sky-700",
    accent: "bg-sky-500",
    icon: "text-sky-600",
  };
}

function getDisplayActionLabel(
  display: InterventionDisplay | undefined,
  actionKey: string,
  fallbackLabel: string,
) {
  if (!display) {
    return fallbackLabel;
  }
  if (
    (actionKey === "approve" ||
      actionKey === "confirm" ||
      actionKey === "resume" ||
      actionKey === "continue") &&
    display.primary_action_label
  ) {
    return display.primary_action_label;
  }
  if (
    (actionKey === "reject" ||
      actionKey === "cancel" ||
      actionKey === "deny" ||
      actionKey === "fail") &&
    display.secondary_action_label
  ) {
    return display.secondary_action_label;
  }
  if (display.respond_action_label) {
    return display.respond_action_label;
  }
  return fallbackLabel;
}

function shouldRenderDisplayItem(label: string) {
  const normalized = label.trim().toLowerCase();
  return normalized !== "提醒" && normalized !== "reminders" && normalized !== "reminder";
}

async function submitInterventionAction({
  actionKey,
  fingerprint,
  payload,
  requestId,
  resolveMutation,
  threadId,
  t,
  onResumeSubmit,
}: {
  actionKey: string;
  fingerprint: string;
  payload: Record<string, unknown>;
  requestId: string;
  resolveMutation: ReturnType<typeof useResolveIntervention>;
  threadId: string;
  t: ReturnType<typeof useI18n>["t"];
  onResumeSubmit?: (response: ResolveInterventionResponse) => Promise<void>;
}) {
  try {
    const response = await resolveMutation.mutateAsync({
      threadId,
      requestId,
      fingerprint,
      actionKey,
      payload,
    });
    console.info("[Intervention] Resolve accepted:", {
      threadId,
      requestId,
      actionKey,
      response,
    });
    toast.success(t.subtasks.interventionSubmitted);

    if (response.resume_action === "submit_resume" && onResumeSubmit) {
      try {
        await onResumeSubmit(response);
        console.info("[Intervention] Resume submit completed:", {
          threadId,
          requestId,
          actionKey,
        });
      } catch (resumeError) {
        console.error("[Intervention] Failed to submit resume run:", resumeError);
        toast.error(t.subtasks.interventionSubmitFailed);
      }
    }
  } catch (error) {
    const status =
      typeof error === "object" &&
      error !== null &&
      "status" in error &&
      typeof error.status === "number"
        ? error.status
        : undefined;

    if (status === 409) {
      toast.error(t.subtasks.interventionStale);
      return;
    }
    if (status === 422) {
      toast.error(t.subtasks.interventionInvalid);
      return;
    }
    toast.error(t.subtasks.interventionSubmitFailed);
  }
}

export function InterventionCard({
  task,
}: {
  task: TaskViewModel;
}) {
  const { t } = useI18n();
  const { thread } = useThread();
  const [settings] = useLocalSettings();
  const resolveMutation = useResolveIntervention();
  const request = task.interventionRequest;
  if (!request || !task.threadId) {
    return null;
  }

  const threadId = task.threadId;

  const handleResumeSubmit = async (response: ResolveInterventionResponse) => {
    const resumeMessage = response.resume_payload?.message;
    if (!resumeMessage) return;

    const ctx = settings.context;
    const isWorkflow = ctx.requested_orchestration_mode === "workflow";
    const submitContext = {
      ...ctx,
      thinking_enabled: ctx.mode !== "flash",
      is_plan_mode: ctx.mode === "pro" || ctx.mode === "ultra",
      subagent_enabled: ctx.mode === "ultra",
      thread_id: threadId,
      workflow_clarification_resume: true,
      workflow_resume_run_id: thread.values.run_id ?? undefined,
      workflow_resume_task_id: task.id,
    };

    console.info("[Intervention] Submitting resume run:", {
      threadId,
      taskId: task.id,
      resumeMessage,
      checkpoint: response.checkpoint,
      submitContext,
    });

    await thread.submit(
      {
        messages: [
          {
            type: "human",
            content: [{ type: "text", text: resumeMessage }],
          },
        ],
      },
      {
        threadId,
        streamSubgraphs: !isWorkflow,
        streamResumable: true,
        streamMode: ["values", "messages-tuple", "custom"],
        checkpoint: response.checkpoint ?? null,
        config: {
          recursion_limit: 1000,
        },
        context: submitContext,
      },
    );
  };
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const riskTone = getRiskTone(request.risk_level);
  const display = request.display;
  const displayTitle = display?.title ?? request.title;
  const displaySummary =
    display?.summary ?? request.reason ?? request.description ?? undefined;
  const displaySections = display?.sections ?? [];
  const fallbackContextEntries = useMemo(
    () => (display ? [] : Object.entries(request.context ?? {})),
    [display, request.context],
  );
  const buttonActions = request.action_schema.actions.filter(
    (action) => action.kind === "button",
  );
  const inputActions = request.action_schema.actions.filter(
    (action) => action.kind === "input",
  );

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-background shadow-[0_10px_24px_rgba(15,23,42,0.05)]">
      <div className="flex items-center gap-2.5 border-b border-border/60 bg-muted/20 px-3 py-2.5">
        <div className="relative flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-background shadow-sm">
          <BotIcon className="size-3.5 text-foreground/75" />
          <span
            className={`absolute -right-0.5 -top-0.5 size-2 rounded-full ${riskTone.accent}`}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {t.subtasks.interventionRequiredLabel}
          </div>
          <div className="truncate text-[14px] font-semibold leading-5 text-foreground">
            {displayTitle}
          </div>
        </div>
      </div>

      <div className="space-y-3 p-3">
        {displaySummary ? (
          <div className="text-[13px] leading-5 text-foreground/88">
            {displaySummary}
          </div>
        ) : null}

        {(displaySections.length > 0 || fallbackContextEntries.length > 0) && (
          <div className="space-y-2">
            {displaySections.map((section, index) => (
              <div
                key={`${section.title ?? "section"}-${index}`}
                className="rounded-lg border border-border/60 bg-muted/8 px-3 py-2.5"
              >
                {section.title ? (
                  <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                    {section.title}
                  </div>
                ) : null}
                <div className="grid gap-2 sm:grid-cols-2">
                  {section.items
                    .filter((item) => shouldRenderDisplayItem(item.label))
                    .map((item, itemIndex) => (
                    <div
                      key={`${item.label}-${itemIndex}`}
                      className="rounded-md border border-border/50 bg-background/70 px-2.5 py-2"
                    >
                      <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                        {item.label}
                      </div>
                      <div className="mt-1 min-w-0 whitespace-pre-wrap break-words text-[12px] leading-5 text-foreground">
                        {item.value}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}

            {fallbackContextEntries.map(([key, value]) => (
              <div
                key={key}
                className="rounded-lg border border-border/60 bg-muted/8 px-3 py-2.5"
              >
                <div className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                  {formatContextLabel(key)}
                </div>
                {typeof value === "object" && value !== null ? (
                  <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-4.5 text-foreground/80">
                    {formatContextValue(value)}
                  </pre>
                ) : (
                  <div className="mt-1 whitespace-pre-wrap break-all text-[12px] leading-5 text-foreground/85">
                    {formatContextValue(value)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/4 p-2.5">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            <ArrowRightIcon className="size-3" />
            {t.subtasks.interventionDecisionLabel}
          </div>

          {buttonActions.length > 0 || inputActions.length > 0 ? (
            <div className="space-y-2">
              {inputActions.length > 0 ? (
                <div className="space-y-2">
                  {inputActions.map((action) => {
                    const draft = drafts[action.key] ?? "";
                    return (
                      <Textarea
                        key={action.key}
                        value={draft}
                        placeholder={
                          display?.respond_placeholder ??
                          action.placeholder ??
                          t.subtasks.interventionPlaceholder
                        }
                        rows={1}
                        className="min-h-0 rounded-lg border-border/70 bg-background px-3 py-2 text-[12px] leading-5 shadow-none focus-visible:ring-1 focus-visible:ring-foreground/15"
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [action.key]: event.target.value,
                          }))
                        }
                      />
                    );
                  })}
                </div>
              ) : null}

              <div className="flex flex-wrap gap-1.5">
                {buttonActions.map((action, index) => {
                  const isDestructive =
                    action.resolution_behavior === "fail_current_task";
                  const isPrimary = !isDestructive && index === 0;
                  const label = getDisplayActionLabel(
                    display,
                    action.key,
                    action.label || t.subtasks.interventionActionFallback,
                  );

                  return (
                    <Button
                      key={action.key}
                      type="button"
                      variant={isPrimary ? "default" : "outline"}
                      className={
                        isPrimary
                          ? "h-8 rounded-lg bg-foreground px-3 text-[12px] font-medium text-background shadow-sm hover:bg-foreground/90"
                          : "h-8 rounded-lg border-border bg-background px-3 text-[12px] font-medium text-foreground hover:bg-muted"
                      }
                      disabled={resolveMutation.isPending}
                      onClick={() =>
                        submitInterventionAction({
                          actionKey: action.key,
                          fingerprint: request.fingerprint,
                          payload: {},
                          requestId: request.request_id,
                          resolveMutation,
                          threadId,
                          t,
                          onResumeSubmit: handleResumeSubmit,
                        })
                      }
                    >
                      {resolveMutation.isPending ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : null}
                      {label}
                    </Button>
                  );
                })}

                {inputActions.map((action) => {
                  const draft = drafts[action.key] ?? "";
                  const disabled = resolveMutation.isPending || !draft.trim();
                  const label = getDisplayActionLabel(
                    display,
                    action.key,
                    action.label || t.subtasks.interventionActionFallback,
                  );

                  return (
                    <Button
                      key={action.key}
                      type="button"
                      className="h-8 rounded-lg bg-foreground px-3 text-[12px] font-medium text-background shadow-sm hover:bg-foreground/90"
                      disabled={disabled}
                      onClick={() =>
                        submitInterventionAction({
                          actionKey: action.key,
                          fingerprint: request.fingerprint,
                          payload: { comment: draft.trim() },
                          requestId: request.request_id,
                          resolveMutation,
                          threadId,
                          t,
                          onResumeSubmit: handleResumeSubmit,
                        })
                      }
                    >
                      {resolveMutation.isPending ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : null}
                      {label}
                    </Button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>

      </div>
    </div>
  );
}
