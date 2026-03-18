"use client";

import {
  BotIcon,
  CheckIcon,
  Loader2Icon,
  MessageSquareQuoteIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import type { ClarificationQuestion } from "@/core/threads/types";
import type { TaskViewModel } from "@/core/tasks/types";

export interface ClarificationSubmitPayload {
  text: string;
  answers: Record<string, { text: string }>;
}

function cleanClarificationLine(line: string) {
  return line.replace(/^\s*(?:[-—–•+*]|\d+[.)、])\s*/, "").trim();
}

function isSeparatorLine(line: string) {
  return /^[-—–•_\s]+$/.test(line.trim());
}

function isExplanatoryLine(line: string) {
  const text = cleanClarificationLine(line);
  if (!text || isSeparatorLine(text)) {
    return true;
  }
  if (
    /(?:请提供以下|请补充以下|我需要更多信息|我需要以下|需要以下|为了帮您|为了帮助您|为了给您|为了继续|包括[:：]?|如下[:：]?|基础信息|关键信息|please provide the following|please answer the following|i need more information|i need the following|to continue|to help|the following details|key information|basic information)/i.test(
      text,
    )
  ) {
    return true;
  }
  return /[:：]$/.test(text);
}

function isQuestionLike(line: string) {
  const text = cleanClarificationLine(line);
  if (!text || isSeparatorLine(text) || isExplanatoryLine(text)) {
    return false;
  }
  if (/[?？.]$/.test(text)) {
    return true;
  }
  return /^(请问|请填写|请提供|请补充|请输入|请选择|是否|能否|可否|what\b|when\b|where\b|who\b|which\b|how\b|please provide\b|please enter\b|please choose\b)/i.test(
    text,
  );
}

export function splitClarificationQuestions(prompt: string): string[] {
  const normalized = prompt.replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const rawLines = normalized
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);

  const numberedQuestions = rawLines
    .filter((line) => /^\s*\d+[.)、]\s*/.test(line))
    .map(cleanClarificationLine)
    .filter(isQuestionLike);
  if (numberedQuestions.length > 0) {
    return numberedQuestions;
  }

  const lineQuestions = rawLines.map(cleanClarificationLine).filter(isQuestionLike);
  if (rawLines.length > 1 && lineQuestions.length > 0) {
    return lineQuestions;
  }

  const sentenceQuestions = normalized
    .split(/(?<=[。！？?!])\s*/)
    .map(cleanClarificationLine)
    .filter(isQuestionLike);
  if (sentenceQuestions.length > 0) {
    return sentenceQuestions;
  }

  const cleaned = cleanClarificationLine(normalized);
  return isQuestionLike(cleaned) ? [cleaned] : [];
}

function normalizeStructuredQuestions(
  questions: ClarificationQuestion[] | undefined,
  fallbackPlaceholder: string,
) {
  return (questions ?? [])
    .filter((question) => question.kind === "input")
    .map((question) => ({
      key: question.key,
      label: question.label.trim(),
      placeholder: question.placeholder?.trim() || fallbackPlaceholder,
      helpText: question.help_text?.trim() || "",
    }))
    .filter((question) => Boolean(question.label));
}

function buildClarificationAnswer(
  questions: Array<{ label: string }>,
  answers: string[],
) {
  return questions
    .map((question, index) => `${question.label} ${answers[index]?.trim() ?? ""}`.trim())
    .filter(Boolean)
    .join("\n");
}

function buildStructuredClarificationAnswers(
  questions: Array<{ key: string }>,
  answers: string[],
): Record<string, { text: string }> {
  return questions.reduce<Record<string, { text: string }>>((acc, question, index) => {
    const value = answers[index]?.trim() ?? "";
    if (!value) {
      return acc;
    }
    acc[question.key] = { text: value };
    return acc;
  }, {});
}

export function ClarificationCard({
  task,
  onSubmit,
  disabled = false,
}: {
  task: TaskViewModel;
  onSubmit: (payload: ClarificationSubmitPayload) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const prompt = task.clarificationPrompt?.trim() ?? "";
  const structuredRequest = task.clarificationRequest;
  const structuredQuestions = useMemo(
    () =>
      normalizeStructuredQuestions(
        structuredRequest?.questions,
        t.subtasks.interventionPlaceholder,
      ),
    [structuredRequest?.questions, t.subtasks.interventionPlaceholder],
  );
  const fallbackQuestions = useMemo(
    () =>
      splitClarificationQuestions(prompt).map((label, index) => ({
        key: `fallback_${index + 1}`,
        label,
        placeholder: t.subtasks.interventionPlaceholder,
        helpText: "",
      })),
    [prompt, t.subtasks.interventionPlaceholder],
  );
  const effectiveQuestions =
    structuredQuestions.length > 0 ? structuredQuestions : fallbackQuestions;

  const [answers, setAnswers] = useState<string[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);

  const normalizedAnswers = useMemo(
    () =>
      effectiveQuestions.map((_, index) => {
        const value = answers[index];
        return typeof value === "string" ? value : "";
      }),
    [answers, effectiveQuestions],
  );

  if (effectiveQuestions.length === 0) {
    return null;
  }

  const activeQuestion = effectiveQuestions[activeIndex] ?? effectiveQuestions[0]!;
  const activeAnswer = normalizedAnswers[activeIndex] ?? "";
  const isLastQuestion = activeIndex === effectiveQuestions.length - 1;
  const title = structuredRequest?.title?.trim() || task.description;
  const description =
    structuredQuestions.length > 0
      ? structuredRequest?.description?.trim()
      : undefined;
  const submitting = disabled;
  const canSubmit = activeAnswer.trim().length > 0 && !submitting;

  const updateAnswer = (value: string) => {
    setAnswers((current) => {
      const next = [...current];
      next[activeIndex] = value;
      return next;
    });
  };

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-background shadow-[0_10px_24px_rgba(15,23,42,0.05)]">
      <div className="flex items-center gap-2.5 border-b border-border/60 bg-muted/25 px-3 py-2.5">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-background shadow-sm">
          <BotIcon className="size-3.5 text-foreground/75" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {t.subtasks.waiting_clarification}
          </div>
          <div className="truncate text-[13px] font-semibold leading-5 text-foreground">
            {title}
          </div>
          {description ? (
            <div className="mt-1 truncate text-xs leading-5 text-muted-foreground">
              {description}
            </div>
          ) : null}
        </div>
        {effectiveQuestions.length > 1 ? (
          <div className="flex items-center gap-1">
            {effectiveQuestions.map((question, index) => {
              const answered = (normalizedAnswers[index]?.trim().length ?? 0) > 0;
              const active = index === activeIndex;
              return (
                <button
                  key={question.key}
                  type="button"
                  className={[
                    "flex size-6 items-center justify-center rounded-md border text-[11px] transition-colors",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : answered
                        ? "border-primary/30 bg-primary/8 text-primary"
                        : "border-border/70 bg-background text-muted-foreground",
                  ].join(" ")}
                  onClick={() => setActiveIndex(index)}
                >
                  {answered && !active ? (
                    <CheckIcon className="size-3" />
                  ) : (
                    index + 1
                  )}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="space-y-3 p-3">
        <div className="flex items-start gap-2 rounded-xl border border-border/60 bg-muted/10 px-3 py-3">
          <MessageSquareQuoteIcon className="mt-0.5 size-4 shrink-0 text-primary" />
          <div className="min-w-0">
            <div className="text-sm leading-6 text-foreground">
              {activeQuestion.label}
            </div>
            {activeQuestion.helpText ? (
              <div className="mt-1 text-xs leading-5 text-muted-foreground">
                {activeQuestion.helpText}
              </div>
            ) : null}
          </div>
        </div>

        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
          <Input
            value={activeAnswer}
            placeholder={activeQuestion.placeholder}
            className="h-10 rounded-lg border-border/70 bg-background text-sm"
            disabled={submitting}
            onInput={(event) =>
              updateAnswer(
                (event.target as HTMLInputElement | null)?.value ?? "",
              )
            }
            onChange={(event) => updateAnswer(event.target.value)}
          />
          <Button
            type="button"
            className="h-10 rounded-lg bg-foreground px-4 text-[12px] text-background shadow-sm hover:bg-foreground/90"
            disabled={!canSubmit}
            onClick={() => {
              if (!activeAnswer.trim()) {
                return;
              }
              if (!isLastQuestion) {
                setActiveIndex((current) =>
                  Math.min(current + 1, effectiveQuestions.length - 1),
                );
                return;
              }
              onSubmit({
                text: buildClarificationAnswer(
                  effectiveQuestions,
                  normalizedAnswers,
                ),
                answers: buildStructuredClarificationAnswers(
                  effectiveQuestions,
                  normalizedAnswers,
                ),
              });
            }}
          >
            {submitting ? <Loader2Icon className="size-3.5 animate-spin" /> : null}
            {t.subtasks.interventionActionFallback}
          </Button>
        </div>
      </div>
    </div>
  );
}
