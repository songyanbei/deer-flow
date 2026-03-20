"use client";

import type { Checkpoint } from "@langchain/langgraph-sdk";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BotIcon,
  CheckSquareIcon,
  CircleDotIcon,
  CircleIcon,
  Loader2Icon,
  PlusCircleIcon,
  SquareIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useResolveIntervention } from "@/core/interventions/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useLocalSettings } from "@/core/settings";
import type { TaskViewModel } from "@/core/tasks/types";
import type {
  InterventionActionSchema,
  InterventionDisplay,
  InterventionOption,
  InterventionQuestion,
} from "@/core/threads";
import { cn } from "@/lib/utils";

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
  return label.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
}

function normalizeText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isBrokenDisplayText(value: unknown): boolean {
  if (typeof value !== "string") {
    return true;
  }
  const text = value.trim();
  if (!text) {
    return true;
  }
  return /^[?？]+$/.test(text);
}

function safeDisplayText(value: unknown, fallback = ""): string {
  if (!isBrokenDisplayText(value)) {
    return String(value).trim();
  }
  return fallback;
}

function extractNumberedOptions(text: string): InterventionOption[] {
  const matches = Array.from(
    text.matchAll(/(?:^|\n)\s*\d+[\.\)、]\s+(.+?)(?=\n\s*\d+[\.\)、]\s+|$)/g),
  );
  const seen = new Set<string>();
  return matches
    .map((match) => normalizeText(match[1]))
    .filter((value) => {
      if (!value || seen.has(value)) {
        return false;
      }
      seen.add(value);
      return true;
    })
    .map((value) => ({
      label: value,
      value,
    }));
}

function stripNumberedOptions(text: string): string {
  return text
    .replace(/(?:^|\n)\s*\d+[\.\)、]\s+.+?(?=\n\s*\d+[\.\)、]\s+|$)/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function getActionOptions(
  action: InterventionActionSchema["actions"][number],
  request: NonNullable<TaskViewModel["interventionRequest"]>,
): InterventionOption[] {
  if (Array.isArray(action.options) && action.options.length > 0) {
    return action.options;
  }
  return extractNumberedOptions(
    [
      request.reason,
      request.description,
      request.action_summary,
      action.description,
    ]
      .filter(
        (value): value is string =>
          typeof value === "string" && value.trim().length > 0,
      )
      .join("\n"),
  );
}

function getQuestionOptions(question: InterventionQuestion): InterventionOption[] {
  if (Array.isArray(question.options) && question.options.length > 0) {
    return question.options;
  }
  return [];
}

function getVisibleOptions(
  options: InterventionOption[],
  limitOptions: boolean,
): InterventionOption[] {
  if (!limitOptions || options.length <= 3) {
    return options;
  }
  return options.slice(0, 3);
}

function isRenderableQuestion(question: InterventionQuestion) {
  const label = question.label.trim();
  if (!label) {
    return false;
  }
  if (
    /^(需要这些信息|请提供以下|请用户提供|为了|以下信息|基础信息|关键信息|包括[:：]|如下[:：])/.test(
      label,
    )
  ) {
    return false;
  }
  if (
    question.kind === "input" &&
    !/[？?]/.test(label) &&
    !/^(请填写|请提供|请告诉|请输入|请补充|预定|预订|会议主题|参会人数|开始时间|结束时间|日期|时间段)/.test(
      label,
    )
  ) {
    return false;
  }
  return true;
}

function isExplanatoryQuestion(question: InterventionQuestion) {
  const label = question.label.trim();
  if (!label) {
    return false;
  }
  return /^(需要这些信息|请提供以下|请用户提供|为了|以下信息|基础信息|关键信息|包括[:：]|如下[:：]|我需要了解一些基本信息)/.test(
    label,
  );
}

function getActionHint(
  action: InterventionActionSchema["actions"][number],
  fallback: string,
) {
  return safeDisplayText(
    action.description,
    safeDisplayText(action.placeholder, fallback),
  );
}

function getActionButtonLabel(
  action: InterventionActionSchema["actions"][number],
  fallback: string,
) {
  if (!isBrokenDisplayText(action.confirm_text)) {
    return action.confirm_text!.trim();
  }
  if (!isBrokenDisplayText(action.label)) {
    return action.label!.trim();
  }
  return fallback;
}

function getDisplayActionLabel(
  display: InterventionDisplay | undefined,
  action: InterventionActionSchema["actions"][number],
  fallback: string,
) {
  if (!display) {
    return getActionButtonLabel(action, fallback);
  }
  if (
    action.kind === "input" &&
    !isBrokenDisplayText(display.respond_action_label)
  ) {
    return display.respond_action_label!.trim();
  }
  if (action.resolution_behavior === "fail_current_task") {
    if (!isBrokenDisplayText(display.secondary_action_label)) {
      return display.secondary_action_label!.trim();
    }
    return getActionButtonLabel(action, fallback);
  }
  if (!isBrokenDisplayText(display.primary_action_label)) {
    return display.primary_action_label!.trim();
  }
  return getActionButtonLabel(action, fallback);
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
  onResumeSubmit,
}: {
  actionKey: string;
  fingerprint: string;
  payload: Record<string, unknown>;
  requestId: string;
  resolveMutation: ReturnType<typeof useResolveIntervention>;
  threadId: string;
  t: ReturnType<typeof useI18n>["t"];
  onResumeSubmit?: (response: unknown) => Promise<void>;
}) {
  try {
    const response = await resolveMutation.mutateAsync({
      threadId,
      requestId,
      fingerprint,
      actionKey,
      payload,
    });
    toast.success(t.subtasks.interventionSubmitted);
    if (
      typeof response === "object" &&
      response !== null &&
      "resume_action" in response &&
      response.resume_action === "submit_resume" &&
      onResumeSubmit
    ) {
      await onResumeSubmit(response);
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
  const interactionCopy = t.subtasks.interventionCopy;
  const { thread } = useThread();
  const [settings] = useLocalSettings();
  const resolveMutation = useResolveIntervention();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selectedValues, setSelectedValues] = useState<Record<string, string>>(
    {},
  );
  const [customValues, setCustomValues] = useState<Record<string, string>>({});
  const [multiSelectedValues, setMultiSelectedValues] = useState<
    Record<string, string[]>
  >({});
  const [activeQuestionIndex, setActiveQuestionIndex] = useState(0);
  const [questionAnswers, setQuestionAnswers] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const request = task.interventionRequest;
  const contextEntries = useMemo(
    () =>
      Object.entries(request?.context ?? {}).filter(
        ([key]) =>
          key !== "tool_args" &&
          key !== "idempotency_key" &&
          key !== "tool_call_id",
      ),
    [request?.context],
  );

  if (!request || !task.threadId) {
    return null;
  }

  const threadId = task.threadId;
  const handleResumeSubmit = async (response: unknown) => {
    const resumePayload =
      typeof response === "object" &&
      response !== null &&
      "resume_payload" in response &&
      typeof response.resume_payload === "object" &&
      response.resume_payload !== null
        ? response.resume_payload
        : null;
    const resumeMessage =
      resumePayload &&
      "message" in resumePayload &&
      typeof resumePayload.message === "string"
        ? resumePayload.message
        : null;

    if (!resumeMessage) {
      return;
    }

    const checkpoint =
      typeof response === "object" &&
      response !== null &&
      "checkpoint" in response
        ? (response.checkpoint as
            | Omit<Checkpoint, "thread_id">
            | null
            | undefined)
        : null;

    const ctx = settings.context;
    const isWorkflow = ctx.requested_orchestration_mode === "workflow";
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
        checkpoint,
        config: { recursion_limit: 1000 },
        context: {
          ...ctx,
          thinking_enabled: ctx.mode !== "flash",
          is_plan_mode: ctx.mode === "pro" || ctx.mode === "ultra",
          subagent_enabled: ctx.mode === "ultra",
          thread_id: threadId,
          workflow_clarification_resume: true,
          workflow_resume_run_id: thread.values.run_id ?? undefined,
          workflow_resume_task_id: task.id,
        },
      },
    );
  };
  const rawQuestions = request.questions ?? [];
  const explanatoryQuestion = rawQuestions.find(isExplanatoryQuestion);
  const questions = rawQuestions.filter(isRenderableQuestion);
  const compositeAction = request.action_schema.actions[0];
  const display = request.display;
  const riskTone = getRiskTone(request.risk_level);
  const isClarificationIntervention =
    request.intervention_type === "clarification" ||
    request.category === "user_clarification";
  const confirmTitleText = isClarificationIntervention
    ? interactionCopy.clarificationConfirmTitle
    : interactionCopy.confirmTitle;
  const confirmHintText = isClarificationIntervention
    ? interactionCopy.clarificationConfirmHint
    : interactionCopy.confirmHint;
  const singleSelectHintText = isClarificationIntervention
    ? interactionCopy.clarificationSingleSelectHint
    : interactionCopy.singleSelectHint;
  const multiSelectHintText = isClarificationIntervention
    ? interactionCopy.clarificationMultiSelectHint
    : interactionCopy.multiSelectHint;
  const customSectionTitleText = isClarificationIntervention
    ? interactionCopy.clarificationCustomSectionTitle
    : interactionCopy.customSectionTitle;
  const singleSubmitFallbackText = isClarificationIntervention
    ? interactionCopy.clarificationSubmitLabel
    : t.subtasks.interventionActionFallback;

  if (questions.length > 0 && compositeAction) {
    const activeQuestion = questions[activeQuestionIndex] ?? questions[0];
    const activeQuestionKey = activeQuestion?.key ?? "";
    const activeQuestionDraft = drafts[activeQuestionKey] ?? "";
    const activeQuestionSelected = selectedValues[activeQuestionKey] ?? "";
    const activeQuestionCustom = customValues[activeQuestionKey] ?? "";
    const activeQuestionMulti = multiSelectedValues[activeQuestionKey] ?? [];
    const activeQuestionOptions = activeQuestion
      ? getQuestionOptions(activeQuestion)
      : [];
    const visibleActiveQuestionOptions = getVisibleOptions(
      activeQuestionOptions,
      isClarificationIntervention,
    );
    const activeQuestionCustomItems = activeQuestionCustom
      .split(/[\n,，]/)
      .map((item) => item.trim())
      .filter(Boolean);
    const activeQuestionMultiValues = Array.from(
      new Set([...activeQuestionMulti, ...activeQuestionCustomItems]),
    );
    const answeredQuestionKeys = new Set(Object.keys(questionAnswers));
    const isLastQuestion = activeQuestionIndex === questions.length - 1;

    const buildCurrentQuestionPayload = () => {
      if (!activeQuestion) {
        return null;
      }
      if (activeQuestion.kind === "confirm") {
        return { confirmed: true };
      }
      if (activeQuestion.kind === "input") {
        const textValue = activeQuestionDraft.trim();
        if (!textValue) {
          return null;
        }
        return { text: textValue, comment: textValue };
      }
      if (
        activeQuestion.kind === "single_select" ||
        activeQuestion.kind === "select"
      ) {
        const effectiveValue =
          activeQuestionCustom.trim() || activeQuestionSelected.trim();
        if (!effectiveValue) {
          return null;
        }
        return {
          selected: effectiveValue,
          custom: Boolean(activeQuestionCustom.trim()),
          custom_text: activeQuestionCustom.trim() || undefined,
        };
      }
      if (activeQuestion.kind === "multi_select") {
        const minSelect =
          activeQuestion.min_select ?? (activeQuestion.required ? 1 : 0);
        const maxSelect = activeQuestion.max_select;
        if (activeQuestionMultiValues.length < minSelect) {
          return null;
        }
        if (
          typeof maxSelect === "number" &&
          activeQuestionMultiValues.length > maxSelect
        ) {
          return null;
        }
        return {
          selected: activeQuestionMultiValues,
          custom: activeQuestionCustomItems.length > 0,
          custom_text: activeQuestionCustom.trim() || undefined,
          custom_values:
            activeQuestionCustomItems.length > 0
              ? activeQuestionCustomItems
              : undefined,
        };
      }
      return null;
    };

    const currentQuestionPayload = buildCurrentQuestionPayload();
    const canAdvance = currentQuestionPayload !== null && !resolveMutation.isPending;

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
              {safeDisplayText(
                explanatoryQuestion?.label,
                safeDisplayText(display?.title, safeDisplayText(request.title)),
              )}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {questions.map((question, index) => {
              const answered = answeredQuestionKeys.has(question.key);
              const active = index === activeQuestionIndex;
              return (
                <button
                  key={question.key}
                  type="button"
                  className={cn(
                    "flex size-6 items-center justify-center rounded-md border text-[11px] transition-colors",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : answered
                        ? "border-primary/30 bg-primary/8 text-primary"
                        : "border-border/70 bg-background text-muted-foreground",
                  )}
                  onClick={() => setActiveQuestionIndex(index)}
                >
                  {answered && !active ? (
                    <CheckSquareIcon className="size-3" />
                  ) : (
                    index + 1
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-3 p-3">
          <div className="flex items-start gap-2 rounded-xl border border-border/60 bg-muted/10 px-3 py-3">
            <AlertTriangleIcon className={`mt-0.5 size-4 shrink-0 ${riskTone.icon}`} />
            <div className="space-y-1">
              <div className="text-sm leading-6 text-foreground">
                {safeDisplayText(activeQuestion?.label)}
              </div>
              {safeDisplayText(activeQuestion?.description) ? (
                <div className="text-xs leading-5 text-muted-foreground">
                  {safeDisplayText(activeQuestion?.description)}
                </div>
              ) : null}
            </div>
          </div>

          {activeQuestion?.kind === "input" ? (
            <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
              <Input
                value={activeQuestionDraft}
                placeholder={
                  safeDisplayText(
                    activeQuestion.placeholder,
                    t.subtasks.interventionPlaceholder,
                  )
                }
                className="h-10 rounded-lg border-border/70 bg-background text-sm"
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [activeQuestion.key]: event.target.value,
                  }))
                }
                onInput={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [activeQuestion.key]:
                      (event.target as HTMLInputElement | null)?.value ?? "",
                  }))
                }
              />
              <Button
                type="button"
                className="h-10 rounded-lg bg-foreground px-4 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                disabled={!canAdvance}
                onClick={() => {
                  if (!currentQuestionPayload || !activeQuestion) {
                    return;
                  }
                  const nextAnswers = {
                    ...questionAnswers,
                    [activeQuestion.key]: currentQuestionPayload,
                  };
                  setQuestionAnswers(nextAnswers);
                  if (!isLastQuestion) {
                    setActiveQuestionIndex((current) => current + 1);
                    return;
                  }
                  void submitInterventionAction({
                    actionKey: compositeAction.key,
                    fingerprint: request.fingerprint,
                    payload: { answers: nextAnswers },
                    requestId: request.request_id,
                    resolveMutation,
                    threadId,
                    t,
                    onResumeSubmit: handleResumeSubmit,
                  });
                }}
              >
                {resolveMutation.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                {isLastQuestion
                  ? getDisplayActionLabel(
                      display,
                      compositeAction,
                      singleSubmitFallbackText,
                    )
                  : interactionCopy.nextStepLabel}
              </Button>
            </div>
          ) : null}

          {(activeQuestion?.kind === "single_select" ||
            activeQuestion?.kind === "select") && activeQuestion ? (
            <div className="space-y-3">
              <div className="space-y-2">
                {visibleActiveQuestionOptions.map((option) => {
                  const checked =
                    !activeQuestionCustom.trim() &&
                    activeQuestionSelected === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={cn(
                        "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                        checked
                          ? "border-primary bg-primary/5"
                          : "border-border/70 bg-background hover:bg-muted/40",
                      )}
                      onClick={() => {
                        setSelectedValues((current) => ({
                          ...current,
                          [activeQuestion.key]: option.value,
                        }));
                        setCustomValues((current) => ({
                          ...current,
                          [activeQuestion.key]: "",
                        }));
                      }}
                    >
                      <span className="mt-0.5 shrink-0 text-primary">
                        {checked ? (
                          <CircleDotIcon className="size-4" />
                        ) : (
                          <CircleIcon className="size-4" />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-foreground">
                          {safeDisplayText(option.label, option.value)}
                        </span>
                        {safeDisplayText(option.description) ? (
                          <span className="text-muted-foreground mt-1 block text-xs leading-4">
                            {safeDisplayText(option.description)}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="space-y-2 rounded-xl border border-dashed border-border/70 bg-muted/10 p-3">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <PlusCircleIcon className="size-3.5" />
                  {customSectionTitleText}
                </div>
                <Input
                  value={activeQuestionCustom}
                  placeholder={interactionCopy.singleCustomPlaceholder}
                  className="h-9 rounded-lg border-border/70 bg-background text-sm"
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setCustomValues((current) => ({
                      ...current,
                      [activeQuestion.key]: nextValue,
                    }));
                    if (nextValue.trim()) {
                      setSelectedValues((current) => ({
                        ...current,
                        [activeQuestion.key]: "",
                      }));
                    }
                  }}
                />
              </div>
              <Button
                type="button"
                className="h-10 rounded-lg bg-foreground px-4 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                disabled={!canAdvance}
                onClick={() => {
                  if (!currentQuestionPayload || !activeQuestion) {
                    return;
                  }
                  const nextAnswers = {
                    ...questionAnswers,
                    [activeQuestion.key]: currentQuestionPayload,
                  };
                  setQuestionAnswers(nextAnswers);
                  if (!isLastQuestion) {
                    setActiveQuestionIndex((current) => current + 1);
                    return;
                  }
                  void submitInterventionAction({
                    actionKey: compositeAction.key,
                    fingerprint: request.fingerprint,
                    payload: { answers: nextAnswers },
                    requestId: request.request_id,
                    resolveMutation,
                    threadId,
                    t,
                    onResumeSubmit: handleResumeSubmit,
                  });
                }}
              >
                {resolveMutation.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                {isLastQuestion
                  ? getDisplayActionLabel(
                      display,
                      compositeAction,
                      t.subtasks.interventionActionFallback,
                    )
                  : interactionCopy.nextStepLabel}
              </Button>
            </div>
          ) : null}

          {activeQuestion?.kind === "multi_select" && activeQuestion ? (
            <div className="space-y-3">
              <div className="space-y-2">
                {visibleActiveQuestionOptions.map((option) => {
                  const checked = activeQuestionMulti.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={cn(
                        "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                        checked
                          ? "border-primary bg-primary/5"
                          : "border-border/70 bg-background hover:bg-muted/40",
                      )}
                      onClick={() => {
                        setMultiSelectedValues((current) => {
                          const currentValues = current[activeQuestion.key] ?? [];
                          const nextValues = currentValues.includes(option.value)
                            ? currentValues.filter((value) => value !== option.value)
                            : [...currentValues, option.value];
                          return {
                            ...current,
                            [activeQuestion.key]: nextValues,
                          };
                        });
                      }}
                    >
                      <span className="mt-0.5 shrink-0 text-primary">
                        {checked ? (
                          <CheckSquareIcon className="size-4" />
                        ) : (
                          <SquareIcon className="size-4" />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-foreground">
                          {safeDisplayText(option.label, option.value)}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
              <Textarea
                value={activeQuestionCustom}
                placeholder={interactionCopy.multiCustomPlaceholder}
                rows={3}
                className="min-h-0 rounded-lg border-border/70 bg-background px-3 py-2 text-[12px] leading-5 shadow-none focus-visible:ring-1 focus-visible:ring-foreground/15"
                onChange={(event) =>
                  setCustomValues((current) => ({
                    ...current,
                    [activeQuestion.key]: event.target.value,
                  }))
                }
              />
              <Button
                type="button"
                className="h-10 rounded-lg bg-foreground px-4 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                disabled={!canAdvance}
                onClick={() => {
                  if (!currentQuestionPayload || !activeQuestion) {
                    return;
                  }
                  const nextAnswers = {
                    ...questionAnswers,
                    [activeQuestion.key]: currentQuestionPayload,
                  };
                  setQuestionAnswers(nextAnswers);
                  if (!isLastQuestion) {
                    setActiveQuestionIndex((current) => current + 1);
                    return;
                  }
                  void submitInterventionAction({
                    actionKey: compositeAction.key,
                    fingerprint: request.fingerprint,
                    payload: { answers: nextAnswers },
                    requestId: request.request_id,
                    resolveMutation,
                    threadId,
                    t,
                    onResumeSubmit: handleResumeSubmit,
                  });
                }}
              >
                {resolveMutation.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : null}
                {isLastQuestion
                  ? getDisplayActionLabel(
                      display,
                      compositeAction,
                      t.subtasks.interventionActionFallback,
                    )
                  : interactionCopy.nextStepLabel}
              </Button>
            </div>
          ) : null}

          {activeQuestion?.kind === "confirm" ? (
            <Button
              type="button"
              className="h-10 rounded-lg bg-foreground px-4 text-[12px] text-background shadow-sm hover:bg-foreground/90"
              disabled={!canAdvance}
              onClick={() => {
                if (!currentQuestionPayload || !activeQuestion) {
                  return;
                }
                const nextAnswers = {
                  ...questionAnswers,
                  [activeQuestion.key]: currentQuestionPayload,
                };
                setQuestionAnswers(nextAnswers);
                if (!isLastQuestion) {
                  setActiveQuestionIndex((current) => current + 1);
                  return;
                }
                void submitInterventionAction({
                  actionKey: compositeAction.key,
                  fingerprint: request.fingerprint,
                  payload: { answers: nextAnswers },
                  requestId: request.request_id,
                  resolveMutation,
                  threadId,
                  t,
                  onResumeSubmit: handleResumeSubmit,
                });
              }}
            >
              {resolveMutation.isPending ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : null}
              {isLastQuestion
                ? getDisplayActionLabel(
                    display,
                    compositeAction,
                    t.subtasks.interventionActionFallback,
                  )
                : interactionCopy.nextStepLabel}
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  const confirmActions = request.action_schema.actions.filter(
    (action) => action.kind === "confirm",
  );
  const plainButtonActions = request.action_schema.actions.filter(
    (action) => action.kind === "button",
  );
  const inputActions = request.action_schema.actions.filter(
    (action) => action.kind === "input",
  );
  const singleSelectActions = request.action_schema.actions.filter(
    (action) => action.kind === "select" || action.kind === "single_select",
  );
  const multiSelectActions = request.action_schema.actions.filter(
    (action) => action.kind === "multi_select",
  );
  const clarificationTitle = safeDisplayText(
    display?.title,
    safeDisplayText(request.title, "请补充信息"),
  );
  const interventionTitle = safeDisplayText(
    display?.title,
    safeDisplayText(request.title, t.subtasks.interventionRequiredLabel),
  );
  const summaryText =
    safeDisplayText(
      display?.summary,
      singleSelectActions.length > 0 || multiSelectActions.length > 0
        ? safeDisplayText(
            stripNumberedOptions(request.reason),
            safeDisplayText(request.reason),
          )
        : safeDisplayText(request.reason),
    );
  const displaySections = display?.sections ?? [];
  const showProtocolMeta = !display;

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-background shadow-[0_10px_24px_rgba(15,23,42,0.05)]">
      <div className="flex items-center gap-2 border-b border-border/60 bg-muted/25 px-2.5 py-2">
        <div className="relative flex size-7 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-background shadow-sm">
          <BotIcon className="size-3 text-foreground/75" />
          <span
            className={`absolute -right-0.5 -top-0.5 size-1.5 rounded-full ${riskTone.accent}`}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {isClarificationIntervention
              ? t.subtasks.waiting_clarification
              : t.subtasks.interventionRequiredLabel}
          </div>
          <div className="truncate text-[13px] font-semibold leading-5 text-foreground">
            {isClarificationIntervention ? clarificationTitle : interventionTitle}
          </div>
        </div>
        {request.risk_level ? (
          <Badge variant="outline" className={riskTone.badge}>
            {t.subtasks.interventionRisk(request.risk_level)}
          </Badge>
        ) : null}
      </div>

      <div className="space-y-2 p-2.5">
        {!isClarificationIntervention ? (
          <>
            <div className="space-y-1">
              {showProtocolMeta ? (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                  <AlertTriangleIcon className={`size-3 ${riskTone.icon}`} />
                  <span className="truncate">
                    {safeDisplayText(request.source_agent, "workflow")}
                  </span>
                  {request.tool_name ? (
                    <>
                      <span className="text-border">/</span>
                      <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground/70">
                        {request.tool_name}
                      </span>
                    </>
                  ) : null}
                </div>
              ) : null}
              <div className="text-[13px] leading-5 text-foreground/88">
                {summaryText}
              </div>
            </div>

            {displaySections.length > 0 ? (
              <div className="space-y-1.5">
                {displaySections.map((section, index) => (
                  <div
                    key={`${safeDisplayText(section.title, "section")}-${index}`}
                    className="rounded-lg border border-border/60 bg-muted/18 px-2.5 py-2"
                  >
                    {safeDisplayText(section.title) ? (
                      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                        {safeDisplayText(section.title)}
                      </div>
                    ) : null}
                    <div className="grid gap-1.5 sm:grid-cols-2">
                      {section.items.map((item) => (
                        <div
                          key={`${safeDisplayText(item.label, "item")}-${safeDisplayText(item.value, "value")}`}
                          className="rounded-md border border-border/50 bg-background/70 px-2 py-1.5"
                        >
                          <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                            {safeDisplayText(item.label, "Item")}
                          </div>
                          <div className="mt-0.5 min-w-0 whitespace-pre-wrap break-words text-[12px] leading-5 text-foreground">
                            {safeDisplayText(item.value)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {(safeDisplayText(request.description) ||
              safeDisplayText(request.action_summary)) && (
              <div className="grid gap-1.5 md:grid-cols-2">
                {safeDisplayText(request.description) ? (
                  <div className="rounded-lg border border-border/60 bg-muted/18 px-2.5 py-1.5 text-[12px] leading-5 text-muted-foreground">
                    {safeDisplayText(request.description)}
                  </div>
                ) : null}
                {safeDisplayText(request.action_summary) ? (
                  <div className="rounded-lg border border-border/60 bg-muted/12 px-2.5 py-1.5">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                      <ArrowRightIcon className="size-3" />
                      {t.subtasks.interventionNextActionLabel}
                    </div>
                    <div className="mt-0.5 text-[12px] leading-5 text-foreground/88">
                      {safeDisplayText(request.action_summary)}
                    </div>
                  </div>
                ) : null}
              </div>
            )}

            {contextEntries.length > 0 ? (
              <div className="grid gap-1.5 md:grid-cols-2">
                {contextEntries.map(([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-border/60 bg-muted/8 px-2 py-1.5"
                  >
                    <div className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                      {formatContextLabel(key)}
                    </div>
                    {typeof value === "object" && value !== null ? (
                      <pre className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-4.5 text-foreground/80">
                        {formatContextValue(value)}
                      </pre>
                    ) : (
                      <div className="mt-0.5 whitespace-pre-wrap break-all text-[12px] leading-5 text-foreground/85">
                        {formatContextValue(value)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : null}

          </>
        ) : null}

        <div className="space-y-1.5 rounded-lg border border-border/60 bg-muted/6 p-2">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            <AlertTriangleIcon className={`size-3 ${riskTone.icon}`} />
            {t.subtasks.interventionDecisionLabel}
          </div>

          {confirmActions.length > 0 ? (
            <div className="space-y-2 border-t border-border/60 pt-2">
              {confirmActions.map((action) => (
                <div
                  key={action.key}
                  className="space-y-2 rounded-xl border border-border/70 bg-background/80 p-2.5"
                >
                  <div className="space-y-0.5">
                    <div className="text-sm font-medium text-foreground">
                      {safeDisplayText(action.label, confirmTitleText)}
                    </div>
                    <div className="text-xs leading-5 text-muted-foreground">
                      {getActionHint(action, confirmHintText)}
                    </div>
                  </div>
                  <Button
                    type="button"
                    className="h-8 rounded-lg bg-foreground px-3 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                      disabled={resolveMutation.isPending}
                      onClick={() =>
                        submitInterventionAction({
                        actionKey: action.key,
                        fingerprint: request.fingerprint,
                        payload: { confirmed: true },
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
                    {getDisplayActionLabel(
                      display,
                      action,
                      singleSubmitFallbackText,
                    )}
                  </Button>
                </div>
              ))}
            </div>
          ) : null}

          {plainButtonActions.length > 0 || inputActions.length > 0 ? (
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
                          safeDisplayText(
                            display?.respond_placeholder,
                            safeDisplayText(
                              action.placeholder,
                              t.subtasks.interventionPlaceholder,
                            ),
                          )
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
                {plainButtonActions.map((action, index) => {
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
                      {getDisplayActionLabel(
                        display,
                        action,
                        t.subtasks.interventionActionFallback,
                      )}
                    </Button>
                  );
                })}

                {inputActions.map((action) => {
                  const draft = drafts[action.key] ?? "";
                  const disabled = resolveMutation.isPending || !draft.trim();

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
                          payload: {
                            text: draft.trim(),
                            comment: draft.trim(),
                          },
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
                      {getDisplayActionLabel(
                        display,
                        action,
                        t.subtasks.interventionActionFallback,
                      )}
                    </Button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {singleSelectActions.length > 0 ? (
            <div className="space-y-3 border-t border-border/60 pt-3">
              {singleSelectActions.map((action) => {
                const options = getActionOptions(action, request);
                const visibleOptions = getVisibleOptions(
                  options,
                  isClarificationIntervention,
                );
                const selectedValue = selectedValues[action.key] ?? "";
                const customValue = customValues[action.key] ?? "";
                const effectiveValue = customValue.trim() || selectedValue;
                const disabled =
                  resolveMutation.isPending || !effectiveValue.trim();

                return (
                  <div key={action.key} className="space-y-3">
                    <div className="text-xs leading-5 text-muted-foreground">
                      {getActionHint(
                        action,
                        singleSelectHintText,
                      )}
                    </div>
                    <div className="space-y-2">
                      {visibleOptions.map((option) => {
                        const checked =
                          !customValue.trim() && selectedValue === option.value;
                        return (
                          <button
                            key={option.value}
                            type="button"
                            className={cn(
                              "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                              checked
                                ? "border-primary bg-primary/5"
                                : "border-border/70 bg-background hover:bg-muted/40",
                            )}
                            onClick={() => {
                              setSelectedValues((current) => ({
                                ...current,
                                [action.key]: option.value,
                              }));
                              setCustomValues((current) => ({
                                ...current,
                                [action.key]: "",
                              }));
                            }}
                          >
                            <span className="mt-0.5 shrink-0 text-primary">
                              {checked ? (
                                <CircleDotIcon className="size-4" />
                              ) : (
                                <CircleIcon className="size-4" />
                              )}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-foreground">
                                {safeDisplayText(option.label, option.value)}
                              </span>
                              {safeDisplayText(option.description) ? (
                                <span className="text-muted-foreground mt-1 block text-xs leading-4">
                                  {safeDisplayText(option.description)}
                                </span>
                              ) : null}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <div className="space-y-2 rounded-xl border border-dashed border-border/70 bg-muted/10 p-3">
                      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                        <PlusCircleIcon className="size-3.5" />
                        {customSectionTitleText}
                      </div>
                      <Input
                        value={customValue}
                        placeholder={interactionCopy.singleCustomPlaceholder}
                        className="h-9 rounded-lg border-border/70 bg-background text-sm"
                        onChange={(event) => {
                          const nextValue = event.target.value;
                          setCustomValues((current) => ({
                            ...current,
                            [action.key]: nextValue,
                          }));
                          if (nextValue.trim()) {
                            setSelectedValues((current) => ({
                              ...current,
                              [action.key]: "",
                            }));
                          }
                        }}
                        onInput={(event) => {
                          const nextValue = (
                            event.target as HTMLInputElement | null
                          )?.value ?? "";
                          setCustomValues((current) => ({
                            ...current,
                            [action.key]: nextValue,
                          }));
                          if (nextValue.trim()) {
                            setSelectedValues((current) => ({
                              ...current,
                              [action.key]: "",
                            }));
                          }
                        }}
                      />
                    </div>
                    <Button
                      type="button"
                      className="h-8 rounded-lg bg-foreground px-3 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                      disabled={disabled}
                      onClick={() =>
                        submitInterventionAction({
                          actionKey: action.key,
                          fingerprint: request.fingerprint,
                          payload: {
                            selected: effectiveValue.trim(),
                            custom: Boolean(customValue.trim()),
                            custom_text: customValue.trim() || undefined,
                          },
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
                      {getDisplayActionLabel(
                        display,
                        action,
                        singleSubmitFallbackText,
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : null}

          {multiSelectActions.length > 0 ? (
            <div className="space-y-3 border-t border-border/60 pt-3">
              {multiSelectActions.map((action) => {
                const options = getActionOptions(action, request);
                const visibleOptions = getVisibleOptions(
                  options,
                  isClarificationIntervention,
                );
                const selected = multiSelectedValues[action.key] ?? [];
                const customValue = customValues[action.key] ?? "";
                const customItems = customValue
                  .split(/[\n,，]/)
                  .map((item) => item.trim())
                  .filter(Boolean);
                const finalSelected = Array.from(
                  new Set([...selected, ...customItems]),
                );
                const minSelect = action.min_select ?? (action.required ? 1 : 0);
                const maxSelect = action.max_select;
                const disabled =
                  resolveMutation.isPending ||
                  finalSelected.length < minSelect ||
                  (typeof maxSelect === "number" &&
                    finalSelected.length > maxSelect);

                return (
                  <div key={action.key} className="space-y-3">
                    <div className="text-xs leading-5 text-muted-foreground">
                      {getActionHint(action, multiSelectHintText)}
                    </div>
                    <div className="space-y-2">
                      {visibleOptions.map((option) => {
                        const checked = selected.includes(option.value);
                        return (
                          <button
                            key={option.value}
                            type="button"
                            className={cn(
                              "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                              checked
                                ? "border-primary bg-primary/5"
                                : "border-border/70 bg-background hover:bg-muted/40",
                            )}
                            onClick={() => {
                              setMultiSelectedValues((current) => {
                                const currentValues = current[action.key] ?? [];
                                const nextValues = currentValues.includes(
                                  option.value,
                                )
                                  ? currentValues.filter(
                                      (value) => value !== option.value,
                                    )
                                  : [...currentValues, option.value];
                                return {
                                  ...current,
                                  [action.key]: nextValues,
                                };
                              });
                            }}
                          >
                            <span className="mt-0.5 shrink-0 text-primary">
                              {checked ? (
                                <CheckSquareIcon className="size-4" />
                              ) : (
                                <SquareIcon className="size-4" />
                              )}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-foreground">
                                {safeDisplayText(option.label, option.value)}
                              </span>
                              {safeDisplayText(option.description) ? (
                                <span className="text-muted-foreground mt-1 block text-xs leading-4">
                                  {safeDisplayText(option.description)}
                                </span>
                              ) : null}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <div className="space-y-2 rounded-xl border border-dashed border-border/70 bg-muted/10 p-3">
                      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                        <PlusCircleIcon className="size-3.5" />
                        {customSectionTitleText}
                      </div>
                      <Textarea
                        value={customValue}
                        placeholder={interactionCopy.multiCustomPlaceholder}
                        rows={3}
                        className="min-h-0 rounded-lg border-border/70 bg-background px-3 py-2 text-[12px] leading-5 shadow-none focus-visible:ring-1 focus-visible:ring-foreground/15"
                        onChange={(event) =>
                          setCustomValues((current) => ({
                            ...current,
                            [action.key]: event.target.value,
                          }))
                        }
                        onInput={(event) =>
                          setCustomValues((current) => ({
                            ...current,
                            [action.key]:
                              (event.target as HTMLTextAreaElement | null)
                                ?.value ?? "",
                          }))
                        }
                      />
                    </div>
                    {finalSelected.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {finalSelected.map((value) => (
                          <span
                            key={value}
                            className="rounded-full border border-border/70 bg-muted/40 px-2.5 py-1 text-xs text-foreground/80"
                          >
                            {value}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <Button
                      type="button"
                      className="h-8 rounded-lg bg-foreground px-3 text-[12px] text-background shadow-sm hover:bg-foreground/90"
                      disabled={disabled}
                      onClick={() =>
                        submitInterventionAction({
                          actionKey: action.key,
                          fingerprint: request.fingerprint,
                          payload: {
                            selected: finalSelected,
                            custom: customItems.length > 0,
                            custom_text: customValue.trim() || undefined,
                            custom_values:
                              customItems.length > 0 ? customItems : undefined,
                          },
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
                      {getDisplayActionLabel(
                        display,
                        action,
                        singleSubmitFallbackText,
                      )}
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
