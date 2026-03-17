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
import { useResolveIntervention } from "@/core/interventions/hooks";
import { useI18n } from "@/core/i18n/hooks";
import type { TaskViewModel } from "@/core/tasks/types";

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

async function submitInterventionAction({
  actionKey,
  fingerprint,
  payload,
  requestId,
  resolveMutation,
  threadId,
  t,
}: {
  actionKey: string;
  fingerprint: string;
  payload: Record<string, unknown>;
  requestId: string;
  resolveMutation: ReturnType<typeof useResolveIntervention>;
  threadId: string;
  t: ReturnType<typeof useI18n>["t"];
}) {
  try {
    await resolveMutation.mutateAsync({
      threadId,
      requestId,
      fingerprint,
      actionKey,
      payload,
    });
    toast.success(t.subtasks.interventionSubmitted);
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
  const request = task.interventionRequest;
  if (!request || !task.threadId) {
    return null;
  }

  const threadId = task.threadId;
  const resolveMutation = useResolveIntervention();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const contextEntries = useMemo(
    () => Object.entries(request.context ?? {}),
    [request.context],
  );
  const riskTone = getRiskTone(request.risk_level);
  const buttonActions = request.action_schema.actions.filter(
    (action) => action.kind === "button",
  );
  const inputActions = request.action_schema.actions.filter(
    (action) => action.kind === "input",
  );

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-background shadow-[0_10px_24px_rgba(15,23,42,0.05)]">
      <div className="flex items-center gap-2.5 border-b border-border/60 bg-muted/25 px-3 py-2.5">
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
          <div className="truncate text-[13px] font-semibold leading-5 text-foreground">
            {request.title}
          </div>
        </div>
        {request.risk_level ? (
          <Badge variant="outline" className={riskTone.badge}>
            {t.subtasks.interventionRisk(request.risk_level)}
          </Badge>
        ) : null}
      </div>

      <div className="space-y-3 p-3">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <AlertTriangleIcon className={`size-3 ${riskTone.icon}`} />
            <span className="truncate">{request.source_agent}</span>
            {request.tool_name ? (
              <>
                <span className="text-border">/</span>
                <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground/70">
                  {request.tool_name}
                </span>
              </>
            ) : null}
          </div>
          <div className="text-[13px] leading-5 text-foreground/88">
            {request.reason}
          </div>
        </div>

        {(request.description || request.action_summary) && (
          <div className="grid gap-2 md:grid-cols-2">
            {request.description ? (
              <div className="rounded-lg border border-border/60 bg-muted/18 px-3 py-2 text-[12px] leading-5 text-muted-foreground">
                {request.description}
              </div>
            ) : null}
            {request.action_summary ? (
              <div className="rounded-lg border border-border/60 bg-muted/12 px-3 py-2">
                <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                  <ArrowRightIcon className="size-3" />
                  {t.subtasks.interventionNextActionLabel}
                </div>
                <div className="mt-1 text-[12px] leading-5 text-foreground/88">
                  {request.action_summary}
                </div>
              </div>
            ) : null}
          </div>
        )}

        {contextEntries.length > 0 ? (
          <div className="grid gap-2 md:grid-cols-2">
            {contextEntries.map(([key, value]) => (
              <div
                key={key}
                className="rounded-lg border border-border/60 bg-muted/8 px-2.5 py-2"
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
        ) : null}

        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/6 p-2.5">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            <AlertTriangleIcon className={`size-3 ${riskTone.icon}`} />
            {t.subtasks.interventionDecisionLabel}
          </div>

          {buttonActions.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {buttonActions.map((action, index) => {
                const isDestructive =
                  action.resolution_behavior === "fail_current_task";
                const isPrimary = !isDestructive && index === 0;

                return (
                  <Button
                    key={action.key}
                    type="button"
                    variant={isPrimary ? "default" : "outline"}
                    className={
                      isPrimary
                        ? "h-8 rounded-lg bg-foreground px-3 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                        : "h-8 rounded-lg border-border bg-background px-3 text-[12px] text-foreground hover:bg-muted"
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
                      })
                    }
                  >
                    {resolveMutation.isPending ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : null}
                    {action.label || t.subtasks.interventionActionFallback}
                  </Button>
                );
              })}
            </div>
          ) : null}

          {inputActions.length > 0 ? (
            <div className="space-y-2 border-t border-border/60 pt-2">
              {inputActions.map((action) => {
                const draft = drafts[action.key] ?? "";
                const disabled = resolveMutation.isPending || !draft.trim();

                return (
                  <div
                    key={action.key}
                    className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <Textarea
                      value={draft}
                      placeholder={
                        action.placeholder ?? t.subtasks.interventionPlaceholder
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
                    <Button
                      type="button"
                      className="h-8 rounded-lg bg-foreground px-3 text-[12px] text-background shadow-sm hover:bg-foreground/90"
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
                        })
                      }
                    >
                      {resolveMutation.isPending ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : null}
                      {action.label || t.subtasks.interventionActionFallback}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
